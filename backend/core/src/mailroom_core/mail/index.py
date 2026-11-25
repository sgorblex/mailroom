"""What a host keeping an index of a folder stores per message, read in batches: the envelope,
flags, size and structure a page shows, the thread headers, a preview (`pages`), and on Gmail
(`X-GM-EXT-1`) the labels and Gmail's message and thread ids. No bodies but the preview's start.
Each read runs in the session `with_mailbox` opened (EXAMINE will do) and changes nothing.

Every text here is the sender's: decoded and flattened as a page shows it, and cut to a fixed
size, but still to be kept and shown as untrusted.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mailroom_core.mail._mime import addresses, aware_date, header_text, well_formed
from mailroom_core.mail.changes import stored_flags, stored_labels, uids_matching
from mailroom_core.mail.errors import MailError
from mailroom_core.mail.pages import _FETCH, _Candidate, _fetch_candidates, _preview_texts
from mailroom_core.mail.parsed import MAX_HEADER_BYTES, parsed_message
from mailroom_core.mail.ref import MessageRef
from mailroom_core.mail.search import fetched_fields
from mailroom_core.mail.selected import search_uids
from mailroom_core.render import Address

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    from mailroom_core.mail.imap import MailboxContext, MailConnections

INDEX_BATCH: Final = 500
"""UIDs per batch an index fetches at once, by default."""
INDEX_ADDRESSES: Final = 100
"""Of each of From, To and Cc, the first ones an index keeps."""
_GMAIL_FETCH: Final = ["X-GM-LABELS", "X-GM-MSGID", "X-GM-THRID"]
_THREAD_FIELDS: Final = (
    "BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES LIST-ID)]"
    f"<0.{MAX_HEADER_BYTES + 1}>"
)
"""The thread headers, cut past `MAX_HEADER_BYTES` as `search.thread_key` reads them:
`parsed_message` then drops the field the cut split."""


@dataclass(frozen=True, slots=True)
class IndexPlan:
    """The UIDs of a folder to index, newest (highest UID) first, in batches."""

    uid_validity: int
    highest_modseq: int | None
    """As `MailboxContext.highest_modseq`, before the UIDs were read: where flag changes are
    asked from (`changes.changed_flags_of`) once the batches are fetched."""
    batches: tuple[tuple[int, ...], ...]


@dataclass(frozen=True, slots=True)
class IndexedMessage:
    """One message as an index keeps it."""

    account: int
    folder: str
    uid_validity: int
    uid: int
    date: datetime | None
    """The Date header; None when absent or unreadable."""
    received: datetime | None
    """INTERNALDATE, the arrival time."""
    sender: tuple[Address, ...]
    to: tuple[Address, ...]
    cc: tuple[Address, ...]
    subject: str
    """`""` when absent."""
    flags: frozenset[str]
    """Flags and keywords as the server has them, without `\\Recent`."""
    size: int
    """RFC822.SIZE, in bytes."""
    has_attachments: bool
    message_id: str | None
    """The Message-ID, angle brackets included, as `parsed_message` reads it."""
    in_reply_to: str | None
    """The first In-Reply-To id."""
    references_root: str | None
    """The first References id: the conversation's first message."""
    list_id: str | None
    """List-Id's identifier, without its angle brackets."""
    snippet: str | None
    """The body's start as a page's preview (`preview_of`); None without a text body."""
    gmail_labels: tuple[str, ...] = ()
    """X-GM-LABELS, decoded (`\\Inbox`, `\\Sent`, the user's own labels); Gmail only."""
    gmail_message_id: int | None = None
    """X-GM-MSGID: the same in every folder (label) showing the message."""
    gmail_thread_id: int | None = None
    """X-GM-THRID."""

    @property
    def message_ref(self) -> MessageRef:
        """The message's ref."""
        return MessageRef(self.account, self.folder, self.uid_validity, self.uid)

    @property
    def thread_key(self) -> str | None:
        """What groups the message with its conversation: Gmail's thread id, else the
        References root, else the first In-Reply-To id, else its own Message-ID."""
        if self.gmail_thread_id is not None:
            return f"gmail:{self.gmail_thread_id}"
        return self.references_root or self.in_reply_to or self.message_id


def plan_index(
    context: MailboxContext, *, since: date | None = None, size: int = INDEX_BATCH
) -> IndexPlan:
    """The selected folder's UIDs in batches of `size`, newest first: all of them, or with
    `since` those that arrived on or after that day (SINCE: INTERNALDATE, by the server's day).
    Read range by range (`changes.uids_matching`)."""
    if size < 1:
        raise MailError("invalid_argument", "A batch holds at least one message.")
    criteria: list[object] = [] if since is None else ["SINCE", since]
    uids = uids_matching(context, criteria)[::-1]
    return IndexPlan(
        context.uid_validity,
        context.highest_modseq,
        tuple(uids[start : start + size] for start in range(0, len(uids), size)),
    )


def fetch_index(
    context: MailboxContext, uids: Sequence[int], *, uid_validity: int
) -> list[IndexedMessage]:
    """`uids` of the selected folder as an index keeps them, highest UID first; a message gone
    meanwhile is left out. One FETCH for the batch, then one per body part type for the
    previews. `stale_reference` when the folder's UIDVALIDITY is no longer `uid_validity`
    (the plan's): its UIDs may now name other messages."""
    if context.uid_validity != uid_validity:
        raise MailError(
            "stale_reference", "The folder's UIDVALIDITY changed: its UIDs name other messages."
        )
    items = [*_FETCH, _THREAD_FIELDS, *(_GMAIL_FETCH if context.gmail else [])]
    candidates = _fetch_candidates(context, list(dict.fromkeys(uids)), items=items)
    previews = _preview_texts(context, candidates)
    return [_indexed(context, c, previews.get(c.uid)) for c in candidates]


def gmail_uid(context: MailboxContext, gmail_message_id: int) -> int | None:
    """The UID the message with Gmail's id `gmail_message_id` (X-GM-MSGID) has in the selected
    folder, a label's or All Mail; None when the label is not on it. `unsupported` elsewhere."""
    if not context.gmail:
        raise MailError("unsupported", "Gmail message ids exist on Gmail accounts only.")
    found = search_uids(context, ["X-GM-MSGID", gmail_message_id])
    return max(found) if found else None


def folder_index_plan(
    connections: MailConnections,
    account: int,
    folder: str,
    *,
    since: date | None = None,
    size: int = INDEX_BATCH,
) -> IndexPlan:
    """`plan_index` of the folder, under an EXAMINE of its own: changes are asked since its
    HIGHESTMODSEQ."""
    return connections.with_mailbox(
        account,
        folder,
        True,
        lambda context: plan_index(context, since=since, size=size),
        reselect=True,
    )


def indexed_messages(
    connections: MailConnections,
    account: int,
    folder: str,
    uids: Sequence[int],
    *,
    uid_validity: int,
) -> list[IndexedMessage]:
    """`fetch_index` of one batch of the folder, under EXAMINE."""
    return connections.with_mailbox(
        account,
        folder,
        True,
        lambda context: fetch_index(context, uids, uid_validity=uid_validity),
    )


def gmail_label_ref(
    connections: MailConnections, account: int, folder: str, gmail_message_id: int
) -> MessageRef | None:
    """The ref the message with Gmail's id `gmail_message_id` has in `folder`, a label's folder
    (`gmail_uid`, under EXAMINE): what an action on a label folder acts on, given the All Mail
    row an index keeps. None when the label is not on it."""

    def find(context: MailboxContext) -> MessageRef | None:
        uid = gmail_uid(context, gmail_message_id)
        return None if uid is None else MessageRef(account, folder, context.uid_validity, uid)

    return connections.with_mailbox(account, folder, True, find)


def gmail_label_refs(
    connections: MailConnections, account: int, folder: str, gmail_message_ids: Sequence[int]
) -> dict[int, MessageRef]:
    """`gmail_label_ref` of several messages in one label's folder, in one session (one UID
    SEARCH each): the refs by Gmail id, those the label is not on left out."""

    def find(context: MailboxContext) -> dict[int, MessageRef]:
        found: dict[int, MessageRef] = {}
        for gmail_id in dict.fromkeys(gmail_message_ids):
            uid = gmail_uid(context, gmail_id)
            if uid is not None:
                found[gmail_id] = MessageRef(account, folder, context.uid_validity, uid)
        return found

    return connections.with_mailbox(account, folder, True, find)


def _indexed(context: MailboxContext, candidate: _Candidate, snippet: str | None) -> IndexedMessage:
    data = candidate.data
    envelope = data.get(b"ENVELOPE")
    thread = parsed_message(fetched_fields(data))
    size = data.get(b"RFC822.SIZE")
    message_id = data.get(b"X-GM-MSGID")
    thread_id = data.get(b"X-GM-THRID")
    return IndexedMessage(
        account=context.account,
        folder=context.path,
        uid_validity=candidate.uid_validity,
        uid=candidate.uid,
        date=aware_date(getattr(envelope, "date", None)),
        received=aware_date(data.get(b"INTERNALDATE")),
        sender=_addresses(getattr(envelope, "from_", None)),
        to=_addresses(getattr(envelope, "to", None)),
        cc=_addresses(getattr(envelope, "cc", None)),
        subject=well_formed(header_text(getattr(envelope, "subject", None))),
        flags=frozenset(well_formed(flag) for flag in stored_flags(data)),
        size=size if isinstance(size, int) else 0,
        has_attachments=len(candidate.structure.attachments) > 0,
        message_id=thread.message_id,
        in_reply_to=next(iter(thread.in_reply_to), None),
        references_root=next(iter(thread.references), None),
        list_id=thread.list_id,
        snippet=None if snippet is None else well_formed(snippet),
        gmail_labels=stored_labels(data),
        gmail_message_id=message_id if isinstance(message_id, int) else None,
        gmail_thread_id=thread_id if isinstance(thread_id, int) else None,
    )


def _addresses(raw: object) -> tuple[Address, ...]:
    return tuple(
        Address(
            name=None if a.name is None else well_formed(a.name),
            address=None if a.address is None else well_formed(a.address),
        )
        for a in addresses(raw)[:INDEX_ADDRESSES]
    )
