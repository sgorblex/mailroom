"""Listing, searching and following a conversation (`src/mcp/tools/listing.ts` and
`thread.ts`): which sources and criteria, over `read_page`."""

import re
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final

from mailroom_core.mail._mime import header_text, text_value
from mailroom_core.mail.errors import MailError
from mailroom_core.mail.folders import with_subfolders
from mailroom_core.mail.handled import HandledKey
from mailroom_core.mail.pages import Page, PageFailure, Source, failed_accounts, read_page
from mailroom_core.mail.parsed import MAX_HEADER_BYTES, parsed_message
from mailroom_core.mail.ref import MessageRef, check_fresh, parse_ref
from mailroom_core.mail.selected import search_uids

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from mailroom_core.mail.imap import MailboxContext, MailConnections
    from mailroom_core.mail.selected import Criteria

ALL_FOLDERS: Final = "*"
"""A search's folder (or account) meaning all of them."""
_MAX_THREAD_IDS: Final = 20
"""References beyond this many (the most recent ones) are ignored: the OR query grows with each."""
_MESSAGE_ID: Final = re.compile(r"<[!-;=?-~]+>")
"""A Message-ID as it can be searched for: printable ASCII, no space or angle bracket inside."""
_HEADERS: Final = ("Message-ID", "References", "In-Reply-To")
"""The headers that tie a conversation together."""
_THREAD_FIELDS: Final = f"BODY.PEEK[HEADER.FIELDS ({' '.join(h.upper() for h in _HEADERS)})]"
_THREAD_KEY_FIELDS: Final = f"{_THREAD_FIELDS}<0.{MAX_HEADER_BYTES + 1}>"
"""The fields, cut past `MAX_HEADER_BYTES`: `parsed_message` then drops the field the cut split."""
_THREAD_CANDIDATES: Final = 500
"""Per folder, of the messages a conversation's search finds, how many of the newest are read to
check they belong to it."""
_REPLY_PREFIX: Final = re.compile(
    r"^(?:\s*(?:re|r|fwd?|fw|i|aw|wg|sv|vs|tr|rif)(?:\[\d+\])?\s*:)+", re.IGNORECASE
)
"""Reply and forward prefixes, in the languages mail clients write them in."""


@dataclass(frozen=True, slots=True)
class ThreadKey:
    """What ties a message to its conversation, as a host keeps it (a muted thread)."""

    message_ids: tuple[str, ...]
    """Its References ids (the first, the thread's root, then the newest), its In-Reply-To ids
    and its own Message-ID, each once, as `parsed_message` reads them: oldest first."""
    gmail_thread: int | None
    """Gmail's thread id (`X-GM-THRID`), on a server with `X-GM-EXT-1`."""


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A search's criteria, all of which must match; none matches everything."""

    sender: str | None = None
    """From contains this (address or name)."""
    to: str | None = None
    subject: str | None = None
    text: str | None = None
    """Headers or body contain this."""
    since: date | None = None
    """Arrived on or after this day: IMAP compares INTERNALDATE (when the server received the
    message, by the server's day), not the Date header."""
    before: date | None = None
    """Arrived before this day, by INTERNALDATE likewise."""
    unread: bool | None = None
    flagged: bool | None = None
    handled: bool | None = None
    """Marked Handled, as each account keeps it, or not."""
    has_attachment: bool | None = None
    """True matches messages with an attachment: Gmail's own `has:attachment`, elsewhere an
    approximation (sent as multipart/mixed). False is ignored."""
    gmail_query: str | None = None
    """Gmail search syntax (X-GM-RAW); Gmail accounts only, else `unsupported`."""

    def criteria(self, *, gmail: bool = False) -> list[object]:
        """As core's own `Criteria` (written by `search_arguments`, not imapclient), imapflow's
        compilation of the TypeScript query; for an account whose capabilities say `gmail`,
        `has_attachment` goes into X-GM-RAW, since Gmail ignores SEARCH HEADER."""
        out: list[object] = []
        for key, value in (
            ("FROM", self.sender),
            ("TO", self.to),
            ("SUBJECT", self.subject),
            ("TEXT", self.text),
        ):
            if value is not None:
                out += [key, value]
        if self.since is not None:
            out += ["SINCE", self.since]
        if self.before is not None:
            out += ["BEFORE", self.before]
        if self.unread is not None:
            out.append("UNSEEN" if self.unread else "SEEN")
        if self.flagged is not None:
            out.append("FLAGGED" if self.flagged else "UNFLAGGED")
        if self.handled is not None:
            out.append(HandledKey(self.handled))
        raw = self.gmail_query
        if self.has_attachment is True:
            if gmail:
                raw = f"has:attachment ({raw})" if raw and raw.strip() else "has:attachment"
            else:
                out += ["HEADER", "Content-Type", "multipart/mixed"]
        if raw is not None:
            out += ["X-GM-RAW", raw]
        return out or ["ALL"]


def searchable_folders(connections: MailConnections, account: int) -> list[str]:
    """The folders a search over all folders reads: not Junk or Trash, and on Gmail only All
    Mail (every label at once)."""
    folders = connections.folders(account)
    every = next((f for f in folders if f.role == "all"), None)
    if every is not None:
        return [every.path]
    return [f.path for f in folders if f.selectable and f.role not in ("junk", "trash")]


def check_subfolders(folder: str, *, subfolders: bool) -> None:
    """Refuses (`invalid_argument`) `subfolders` with `ALL_FOLDERS`."""
    if subfolders and folder == ALL_FOLDERS:
        raise MailError("invalid_argument", 'subfolders cannot go with folder "*".')


def read_folders(
    connections: MailConnections, account: int, folder: str, *, subfolders: bool
) -> list[str]:
    """The folders a list or search of `folder` reads: itself, or with `subfolders` the set
    `with_subfolders` gives (`mailbox_missing`, `invalid_argument` past its cap)."""
    check_subfolders(folder, subfolders=subfolders)
    if not subfolders:
        return [folder]
    return with_subfolders(connections.folders(account), folder, account)


def search_sources(
    connections: MailConnections,
    account: int,
    folder: str,
    query: SearchQuery,
    *,
    subfolders: bool,
) -> tuple[list[str], Criteria]:
    """The folders a search of `folder` reads in `account` (`searchable_folders` for
    `ALL_FOLDERS`, else `read_folders`), and `query`'s criteria there: on Gmail
    `has_attachment` goes into X-GM-RAW, so the capabilities are asked only when the query needs
    them. `unsupported` for a `gmail_query` elsewhere."""
    gmail = (
        query.gmail_query is not None or query.has_attachment is True
    ) and connections.capabilities(account).gmail
    if query.gmail_query is not None and not gmail:
        raise MailError(
            "unsupported",
            f"gmail_query works only on Gmail accounts; account {account} is not one.",
        )
    folders = (
        searchable_folders(connections, account)
        if folder == ALL_FOLDERS
        else read_folders(connections, account, folder, subfolders=subfolders)
    )
    return folders, query.criteria(gmail=gmail)


def list_messages(
    connections: MailConnections,
    account: int,
    folder: str = "INBOX",
    *,
    limit: int = 20,
    cursor: str | None = None,
    previews: bool = True,
    subfolders: bool = False,
) -> Page:
    """The newest messages of one folder, or with `subfolders` of it and its subfolders merged
    (`read_folders`). A failing folder is a failure of the page: when it reads one folder, the
    only one, and a host showing one folder may raise `MailError(page.failures[0].code)`
    instead."""
    folders = read_folders(connections, account, folder, subfolders=subfolders)
    return read_page(
        connections,
        [Source(account, path) for path in folders],
        ["ALL"],
        limit=limit,
        cursor=cursor,
        previews=previews,
    )


def search_messages(
    connections: MailConnections,
    accounts: Sequence[int],
    folder: str,
    query: SearchQuery,
    *,
    limit: int = 20,
    cursor: str | None = None,
    previews: bool = True,
    subfolders: bool = False,
) -> Page:
    """`query` over `accounts` (the ids the caller may use, already resolved from `*`), in one
    folder (with `subfolders`, its subfolders too: `read_folders`) or all (`ALL_FOLDERS`).
    With several accounts one that cannot be searched becomes a failure with no folder and the
    others still answer, and the following pages report it without asking it again; with one,
    its MailError is raised."""
    check_subfolders(folder, subfolders=subfolders)
    sources: list[Source] = []
    failures: list[PageFailure] = []
    criteria: dict[int, Criteria] = {}
    failed_before = failed_accounts(cursor)
    for account in accounts:
        if account in failed_before:
            continue
        try:
            folders, criteria[account] = search_sources(
                connections, account, folder, query, subfolders=subfolders
            )
            sources.extend(Source(account, path) for path in folders)
        except MailError as error:
            if len(accounts) == 1:
                raise
            failures.append(PageFailure.of(account, None, error))
    return read_page(
        connections,
        sources,
        criteria,
        limit=limit,
        cursor=cursor,
        previews=previews,
        account_failures=failures,
    )


def _any_of(terms: Sequence[list[object]]) -> list[object]:
    """A balanced tree of IMAP's binary OR over `terms`, so a long list stays shallow."""
    if len(terms) == 1:
        return terms[0]
    middle = len(terms) // 2
    return ["OR", _any_of(terms[:middle]), _any_of(terms[middle:])]


def _thread_ids(headers: str, own: str | None) -> list[str]:
    """The Message-IDs a conversation is searched by: those in `headers` (Message-ID,
    References, In-Reply-To) and the envelope's own, the last `_MAX_THREAD_IDS` of them. An id
    that is not printable ASCII cannot be searched for, so it is left out rather than failing
    the whole conversation."""
    found = _MESSAGE_ID.findall(headers) + ([] if own is None else _MESSAGE_ID.findall(own))
    return list(dict.fromkeys(found))[-_MAX_THREAD_IDS:]


def _base_subject(subject: str) -> str:
    """`subject` without its reply and forward prefixes (`Re:`, `Fwd:`, `R:`, `I:`, `AW:` and the
    like, repeated): what every message of a conversation's subject holds."""
    return _REPLY_PREFIX.sub("", subject).strip()


def fetched_fields(data: Mapping[bytes, object]) -> bytes:
    """The `BODY[HEADER.FIELDS (...)]` a FETCH answered; empty when it has none."""
    block = next(
        (v for k, v in data.items() if isinstance(k, bytes) and k.startswith(b"BODY[HEADER")),
        b"",
    )
    return block if isinstance(block, bytes) else b""


def _header_block(data: Mapping[bytes, object]) -> str:
    """The `BODY[HEADER.FIELDS (...)]` a FETCH answered, as text."""
    return text_value(fetched_fields(data))


def _members(context: MailboxContext, ids: Sequence[str], subject: str, own: int | None) -> str:
    """The UIDs of the selected folder's messages in the conversation `ids` name, as a UID set
    (empty for none): those whose Message-ID, References or In-Reply-To holds one of them, read
    from the newest `_THREAD_CANDIDATES` that a search by those headers or by `subject` finds,
    and `own`, the message the conversation was asked for. The headers are read, not trusted to
    the search: a server's full-text index may leave them out (Mailu's Dovecot), so a header
    search never matches, and may match by words."""
    found = set(search_uids(context, [_any_of([["HEADER", n, i] for i in ids for n in _HEADERS])]))
    if subject:
        found |= set(search_uids(context, ["SUBJECT", subject]))
    candidates = sorted(found - {own})[-_THREAD_CANDIDATES:]
    wanted = set(ids)
    members = set() if own is None else {own}
    if candidates:
        fetched = context.client.fetch(candidates, [_THREAD_FIELDS])
        members |= {
            uid
            for uid, data in fetched.items()
            if wanted.intersection(_MESSAGE_ID.findall(_header_block(data)))
        }
    return ",".join(str(uid) for uid in sorted(members))


def thread_page(connections: MailConnections, ref: str | MessageRef, *, limit: int = 30) -> Page:
    """The conversation `ref` belongs to, newest first: its folder plus Sent, matched by
    Message-ID, References and In-Reply-To (on Gmail: the Gmail thread, from All Mail). Elsewhere
    the candidates are those a search by those headers or by the subject finds, and each one's
    headers are read (`_members`); a reply whose subject changed is found only where the header
    search works."""
    message = parse_ref(ref) if isinstance(ref, str) else ref
    gmail = connections.capabilities(message.account).gmail

    def find(context: MailboxContext) -> tuple[str, str | None, str, int | None]:
        check_fresh(message, context.uid_validity)
        items = ["ENVELOPE", _THREAD_FIELDS, *(["X-GM-THRID"] if gmail else [])]
        data = context.client.fetch([message.uid], items).get(message.uid)
        if data is None:
            raise MailError("not_found")
        envelope = data.get(b"ENVELOPE")
        own = getattr(envelope, "message_id", None)
        thread_id = data.get(b"X-GM-THRID")
        return (
            _header_block(data),
            None if own is None else text_value(own),
            header_text(getattr(envelope, "subject", None)),
            thread_id if isinstance(thread_id, int) else None,
        )

    headers, own, subject, thread_id = connections.with_mailbox(
        message.account, message.folder, True, find
    )
    folders = connections.folders(message.account)
    if gmail and thread_id is not None:
        every = next((f.path for f in folders if f.role == "all"), message.folder)
        return read_page(
            connections,
            [Source(message.account, every)],
            ["X-GM-THRID", thread_id],
            limit=limit,
            previews=True,
        )
    ids = _thread_ids(headers, own)
    if not ids:
        raise MailError("not_found", "This message has no Message-ID to follow.")
    sent = next((f.path for f in folders if f.role == "sent"), None)
    base = _base_subject(subject)
    criteria: dict[Source, Criteria] = {}
    failures: list[PageFailure] = []
    for path in dict.fromkeys([message.folder, *([] if sent is None else [sent])]):
        mine = message.uid if path == message.folder else None
        try:
            uids = connections.with_mailbox(
                message.account,
                path,
                True,
                partial(_members, ids=ids, subject=base, own=mine),
            )
        except MailError as error:
            if path == message.folder:
                raise
            failures.append(PageFailure.of(message.account, path, error))
            continue
        if uids:
            criteria[Source(message.account, path)] = ["UID", uids]
    page = read_page(connections, list(criteria), criteria, limit=limit, previews=True)
    return Page(page.messages, page.next_cursor, (*failures, *page.failures))


def thread_key(connections: MailConnections, ref: str | MessageRef) -> ThreadKey:
    """The ids that tie the message `ref` names to its conversation: its Message-ID,
    In-Reply-To and References read from their header fields' first `MAX_HEADER_BYTES` (never
    `\\Seen`), and on Gmail its thread id. A message with none of them has an empty key."""
    message = parse_ref(ref) if isinstance(ref, str) else ref
    gmail = connections.capabilities(message.account).gmail

    def find(context: MailboxContext) -> ThreadKey:
        check_fresh(message, context.uid_validity)
        items = [_THREAD_KEY_FIELDS, *(["X-GM-THRID"] if gmail else [])]
        data = context.client.fetch([message.uid], items).get(message.uid)
        if data is None:
            raise MailError("not_found")
        parsed = parsed_message(fetched_fields(data))
        own = () if parsed.message_id is None else (parsed.message_id,)
        ids = (*parsed.references, *parsed.in_reply_to, *own)
        thread_id = data.get(b"X-GM-THRID")
        return ThreadKey(
            tuple(dict.fromkeys(ids)), thread_id if isinstance(thread_id, int) else None
        )

    return connections.with_mailbox(message.account, message.folder, True, find)
