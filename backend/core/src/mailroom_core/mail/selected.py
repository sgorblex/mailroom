"""Operations on the folder a `with_mailbox` session has selected, by UID: searching, fetching,
appending, moving, flagging and the one permanent delete. Organize, drafts and replies are built
on them, and so is anything else that acts inside a session of its own (the filter engine),
which never needs a second connection: the folders come from the one it holds.

Each takes the session's `MailboxContext`. Refusals are `MailError`s raised before anything is
written; server and connection errors come out as raised, for `with_mailbox` to map. None of
them retries a write.
"""

import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from imapclient.datetime_util import format_criteria_date
from imapclient.fixed_offset import FixedOffset
from imapclient.imapclient import _literal
from imapclient.response_parser import parse_message_list

from mailroom_core.mail._mime import text_value
from mailroom_core.mail.audit import REFUSALS
from mailroom_core.mail.changes import stored_labels
from mailroom_core.mail.errors import MailError, is_connection_error, mail_error
from mailroom_core.mail.handled import HandledKey, handled_criteria, holds_handled
from mailroom_core.mail.imap import folders_from
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

    from imapclient.response_types import Envelope

    from mailroom_core.mail.errors import MailErrorCode
    from mailroom_core.mail.imap import Folder, FolderRole, MailboxContext

type Criteria = Sequence[object]
"""Core's own SEARCH criteria, written by `search_arguments` (not imapclient), e.g.
`["SUBJECT", "invoice", "UNSEEN"]`; nested lists are parenthesised groups."""

TO_SORT: Final = "$MailroomSort"
"""The keyword To sort, which Handled replaced, was kept as: what the accounts that used it keep
Handled as, and what a filter's To sort criteria and actions still mean. Servers compare keywords
ignoring case."""
FORWARDED: Final = "$Forwarded"
"""The keyword (RFC 5788) a sent forward sets on its original; only `store_forwarded` sets it,
so it is not among `SETTABLE_FLAGS`."""
SETTABLE_FLAGS: Final = ("\\Seen", "\\Flagged", "\\Answered", TO_SORT)
"""The flags `store_flags` sets and clears; `\\Deleted` is only ever set by a removal."""
_DELETED: Final = b"\\Deleted"
_MAX_UID: Final = 0xFFFFFFFF
_APPENDUID: Final = re.compile(rb"\[APPENDUID ([0-9]{1,10}) ([0-9]{1,10})\]", re.IGNORECASE)
_NOT_REMOVED: Final = (
    "They were copied to the destination but may not have been removed from this folder: do not "
    "repeat the move."
)


# SEARCH arguments, written here rather than by imapclient: it sends a value unquoted unless it
# holds a space, a quote or a backslash, so `(`, `)` or `{5}` in a search would change the
# command (a literal the server then waits for), and it never refuses CR or LF, which would end
# the command and start another. imapflow sends such values as strings or literals.

_ATOM: Final = re.compile(rb"[!#$&'+,\-./0-9:;<=>?@A-Z\[^_`a-z|}~]+")
"""An atom (RFC 3501): no space, control, `(`, `)`, `{`, `"`, `\\`, `%`, `*` or `]`."""
_LINE_BREAKS: Final = re.compile("[\r\n\0]")


def _token(item: object) -> bytes:
    """One argument: a number or date as IMAP writes it, a text as an atom when it is one, else
    as a quoted string (ASCII) or a literal (anything else, sent under CHARSET UTF-8)."""
    if isinstance(item, bool):
        msg = "a search argument cannot be a bool"
        raise TypeError(msg)
    if isinstance(item, int):
        return str(item).encode("ascii")
    if isinstance(item, datetime | date):
        return bytes(format_criteria_date(item))
    text = item.decode("utf-8", "replace") if isinstance(item, bytes) else str(item)
    if _LINE_BREAKS.search(text):
        raise MailError("invalid_argument", "Search text cannot contain line breaks.")
    raw = text.encode("utf-8")
    if _ATOM.fullmatch(raw):
        return raw
    if raw.isascii():
        return b'"' + raw.replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'
    literal: bytes = _literal(raw)
    return literal


def search_arguments(
    criteria: Criteria, resolve: Callable[[HandledKey], Criteria] = lambda _: ["ALL"]
) -> tuple[list[bytes], bool]:
    """`criteria` as UID SEARCH arguments, and whether they need CHARSET UTF-8; nested lists
    become parenthesised groups. A group ending in non-ASCII text gets `ALL` before its closing
    parenthesis, which cannot follow a literal. `resolve` gives a `HandledKey`'s own criteria, sent
    as a group (without it, to check criteria only, `ALL` stands for them). `invalid_argument` for
    text with a line break, for an empty group, or for non-ASCII text opening a group."""
    out: list[bytes] = []

    def walk(items: Sequence[object], depth: int) -> None:
        if not items:
            raise MailError("invalid_argument", "A search group cannot be empty.")
        start = len(out)
        for item in items:
            if isinstance(item, HandledKey):
                walk(resolve(item), depth + 1)
            elif isinstance(item, list | tuple):
                walk(item, depth + 1)
            else:
                out.append(_token(item))
        if depth > 0:
            if isinstance(out[start], _literal):
                raise MailError("invalid_argument", "Non-ASCII text cannot open a search group.")
            if isinstance(out[-1], _literal):
                out.append(b"ALL")  # ANDed in, so it changes nothing
            out[start] = b"(" + out[start]
            out[-1] = out[-1] + b")"

    walk(criteria or ["ALL"], 0)
    return out, any(isinstance(token, _literal) for token in out)


def search_uids(context: MailboxContext, criteria: Criteria) -> list[int]:
    """UID SEARCH in the selected folder: the matching UIDs, as the server ordered them."""

    def resolve(key: HandledKey) -> Criteria:
        return handled_criteria(key, context.handled, gmail=context.gmail, folder=context.path)

    arguments, utf8 = search_arguments(criteria, resolve)
    charset = [b"CHARSET", b"UTF-8"] if utf8 else []
    # imapclient's own sender, which writes a `_literal` as an IMAP literal (LITERAL+ when the
    # server has it) and the other arguments as they are.
    data = context.client._raw_command_untagged(b"SEARCH", [*charset, *arguments])  # noqa: SLF001
    return [int(uid) for uid in parse_message_list(data)]


def message_id_uids(
    context: MailboxContext,
    message_id: str,
    *,
    since_uid: int = 0,
    size: int | None = None,
    gmail: bool = False,
) -> list[int]:
    """The UIDs, in order, from `since_uid` on and of `size` bytes (RFC822.SIZE) when given,
    whose ENVELOPE carries `message_id`. Gmail ignores SEARCH HEADER, so there it searches
    `X-GM-RAW rfc822msgid:`. Elsewhere a header search can miss: a server may search by words,
    not have indexed a message just added yet, or enforce a full-text index that leaves
    Message-ID out (Mailu's Dovecot), so it never matches. With a bound, by UID or by size, the
    candidates are found by that bound alone, which needs no index, and each one's Message-ID
    is read; with neither, the header search is all there is."""
    criteria: list[object]
    if gmail:
        criteria = ["X-GM-RAW", f"rfc822msgid:{message_id.strip().strip('<>')}"]
    elif since_uid > 0 or size is not None:
        criteria = []
        if since_uid > 0:
            criteria += ["UID", f"{since_uid}:{_MAX_UID}"]
        if size is not None:
            criteria += ["LARGER", max(size - 1, 0), "SMALLER", size + 1]
    else:
        criteria = ["HEADER", "Message-ID", message_id]
    found = [uid for uid in search_uids(context, criteria) if uid >= since_uid]
    if not found:
        return []
    fetched = context.client.fetch(found, ["ENVELOPE", "RFC822.SIZE"])
    return sorted(
        uid
        for uid, data in fetched.items()
        if text_value(getattr(data.get(b"ENVELOPE"), "message_id", None)).strip() == message_id
        and (size is None or data.get(b"RFC822.SIZE") == size)
    )


def appended_uid(response: bytes | None, uid_validity: int) -> int | None:
    """The UID an APPEND's tagged response reports (APPENDUID, RFC 4315), which imapclient hands
    over as raw bytes; None without one, or for a UIDVALIDITY other than the folder's."""
    found = _APPENDUID.match(response or b"")
    if found is None or int(found.group(1)) != uid_validity:
        return None
    uid = int(found.group(2))
    return uid if 0 < uid <= _MAX_UID else None


def append_raw(
    context: MailboxContext,
    raw: bytes,
    flags: Sequence[str],
    *,
    internal_date: datetime | None = None,
) -> int | None:
    """APPENDs `raw` to the selected folder with `flags`, and `internal_date` as its arrival
    date when given (else the server's clock). Returns the UID APPENDUID reports, None without
    one. Never retried: an error means the message may or may not be there. The new message
    need not be in view until a NOOP (RFC 3501, 6.3.11)."""
    response = context.client.append(context.path, raw, tuple(flags), msg_time=internal_date)
    return appended_uid(response, context.uid_validity)


def abandon_read_back(context: MailboxContext, error: Exception) -> MailError:
    """After a write that went through, all or in part, an error reading it back or going on:
    logs `error` and, for a connection error, shuts the connection down, its state unknown, so
    the next caller's SELECT fails and reconnects. What was written stands. Returns `error` as a
    MailError."""
    mapped = mail_error(error, context.account)
    if is_connection_error(error):
        with suppress(OSError):
            context.client.shutdown()
    return mapped


def _flags(data: Mapping[bytes, object]) -> frozenset[str]:
    """The FLAGS of a FETCH response, as text."""
    flags = data.get(b"FLAGS")
    return frozenset(
        text_value(flag) for flag in (flags if isinstance(flags, tuple | list) else ())
    )


@dataclass(frozen=True, slots=True)
class MessageSource:
    """A whole message as stored, with its flags and arrival date. The source is the sender's
    bytes."""

    source: bytes
    flags: frozenset[str]
    internal_date: datetime | None
    """INTERNALDATE in UTC, None when the server gave none."""


def fetch_source(context: MailboxContext, uid: int, max_bytes: int) -> MessageSource:
    """The whole source of `uid`, its flags and arrival date, with `BODY.PEEK[]` (never sets
    `\\Seen`): `too_large` over `max_bytes` rather than cut, `not_found` when it is gone."""
    client = context.client
    sized = client.fetch([uid], ["RFC822.SIZE", "FLAGS", "INTERNALDATE"]).get(uid)
    if sized is None:
        raise MailError("not_found")
    size = sized.get(b"RFC822.SIZE")
    if isinstance(size, int) and size > max_bytes:
        raise MailError("too_large")
    # One byte over the cap is enough to know it is too large.
    fetched = client.fetch([uid], [f"BODY.PEEK[]<0.{max_bytes + 1}>"]).get(uid)
    raw = None if fetched is None else fetched.get(b"BODY[]<0>")
    if not isinstance(raw, bytes):
        raise MailError("not_found")
    if len(raw) > max_bytes:
        raise MailError("too_large")
    arrived = sized.get(b"INTERNALDATE")
    return MessageSource(
        source=raw,
        flags=_flags(sized),
        internal_date=_aware(arrived) if isinstance(arrived, datetime) else None,
    )


def _aware(moment: datetime) -> datetime:
    """An INTERNALDATE in UTC. imapclient hands it over naive, in local time at the system's
    current offset (not the one of its own date), so that offset puts it back."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=FixedOffset.for_system())
    return moment.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class FetchedMessage:
    """A whole message as stored, and its flags. The source is the sender's bytes."""

    uid: int
    source: bytes
    flags: frozenset[str]
    """As the server has them, `\\Seen`."""
    gmail_thread: int | None = None
    """Gmail's thread id (`X-GM-THRID`), on a server with `X-GM-EXT-1`."""
    handled: bool = False
    """Marked Handled, in the account's own way."""


def fetch_sources(context: MailboxContext, uids: Iterable[int]) -> dict[int, FetchedMessage]:
    """The whole source and flags of each of `uids` still in the folder, by UID, with
    `BODY.PEEK[]`: unlike `BODY[]`, it never sets `\\Seen`. On Gmail, its thread id too."""
    wanted = list(dict.fromkeys(uids))
    if not wanted:
        return {}
    gmail = context.client.has_capability("X-GM-EXT-1")
    items = ["BODY.PEEK[]", "FLAGS", *(["X-GM-THRID"] if gmail else []), *label_items(context)]
    fetched = context.client.fetch(wanted, items)
    out: dict[int, FetchedMessage] = {}
    for uid, data in fetched.items():
        source = data.get(b"BODY[]")
        if not isinstance(source, bytes):
            continue
        thread = data.get(b"X-GM-THRID")
        out[int(uid)] = FetchedMessage(
            uid=int(uid),
            source=source,
            flags=_flags(data),
            gmail_thread=thread if isinstance(thread, int) else None,
            handled=fetched_handled(context, data),
        )
    return out


def label_items(context: MailboxContext) -> list[str]:
    """What a FETCH asks besides FLAGS to tell whether a message is marked Handled: Gmail's
    labels, where the account keeps it as one."""
    return ["X-GM-LABELS"] if context.handled.kind == "label" and context.gmail else []


def fetched_handled(context: MailboxContext, data: Mapping[bytes, object]) -> bool:
    """Whether the message a FETCH of FLAGS and `label_items` answered for is marked Handled."""
    flags = data.get(b"FLAGS")
    texts = [text_value(flag) for flag in (flags if isinstance(flags, tuple | list) else ())]
    mark = context.handled
    if mark.kind != "label":
        return holds_handled(mark, texts)
    if not context.gmail:
        return False
    return holds_handled(mark, texts, stored_labels(data), folder=context.path)


def fetch_envelopes(context: MailboxContext, uids: Iterable[int]) -> dict[int, Envelope | None]:
    """The ENVELOPE of each of `uids` still in the folder, by UID; None for a message the server
    answered for without one. A UID missing from the result is gone."""
    wanted = list(dict.fromkeys(uids))
    if not wanted:
        return {}
    fetched = context.client.fetch(wanted, ["ENVELOPE"])
    return {uid: fetched[uid].get(b"ENVELOPE") for uid in wanted if uid in fetched}


def listed_folders(context: MailboxContext) -> list[Folder]:
    """The account's folders, listed on the session's own connection, as
    `MailConnections.folders` lists them: for a session that needs them, which cannot take a
    second connection of the account."""
    return folders_from(context.client.list_folders())


def special_folder_in(folders: Iterable[Folder], role: FolderRole) -> Folder | None:
    """The folder of `folders` with `role` (one at most has it), or None."""
    return next((folder for folder in folders if folder.role == role), None)


def move_target(
    source: str | None, target: str, folders: Sequence[Folder], *, account: int
) -> Folder:
    """The folder of `folders` that a move from `source` to `target` lands in (INBOX in any
    case), or a refusal: `mailbox_missing` when it is not a selectable folder of `account`,
    `invalid_argument` when it is `source`, Drafts or Sent. `source` is None on a move from
    another account."""
    found = next(
        (f for f in folders if f.path == target or (_is_inbox(f.path) and _is_inbox(target))),
        None,
    )
    if found is None or not found.selectable:
        raise MailError(
            "mailbox_missing",
            f'"{clean_header(target, 200)}" is not a folder of account {account}; '
            "list its folders.",
        )
    if found.path == source:
        raise MailError("invalid_argument", "They are already in that folder.")
    # Drafts are what a draft send sends, and Sent is the owner's own history: neither may be
    # filled with someone else's message.
    if found.role in ("drafts", "sent"):
        raise MailError(
            "invalid_argument",
            "Messages cannot be moved into Drafts or Sent; drafts come only from the draft tools.",
        )
    return found


def _is_inbox(path: str) -> bool:
    return path.upper() == "INBOX"


@dataclass(frozen=True, slots=True)
class Moved:
    """Where a move took its messages."""

    destination: str
    uid_validity: int | None
    """The destination's, from COPYUID; None when the server did not say where they landed."""
    new_uids: Mapping[int, int] = field(default_factory=dict)
    """Each moved UID's UID in the destination; empty when `uid_validity` is None."""


class CopiedNotRemovedError(MailError):
    """A move without MOVE that copied the messages but may not have removed them from the
    source: they can now be in both folders, so the move must not be repeated. `moved` says
    where the copies are."""

    def __init__(self, code: MailErrorCode, moved: Moved) -> None:
        """The failure of the removal, as `code`, with where the copies went."""
        super().__init__(code, _NOT_REMOVED)
        self.moved = moved


def check_movable(context: MailboxContext) -> None:
    """`unsupported` unless the server can move exactly the UIDs given: with MOVE, or with
    UIDPLUS for `move_uids`' copy and UID EXPUNGE."""
    client = context.client
    if not client.has_capability("MOVE") and not client.has_capability("UIDPLUS"):
        raise MailError(
            "unsupported",
            "This server can only move mail by also deleting other messages marked for deletion.",
        )


def move_uids(context: MailboxContext, uids: Sequence[int], destination: str) -> Moved:
    """Moves `uids` to `destination` with MOVE. Without it, copies them, marks them `\\Deleted`
    and expunges exactly those UIDs, which only UIDPLUS allows: a server with neither is refused
    first (`check_movable`), since a plain EXPUNGE would also remove other messages marked
    `\\Deleted`. When that copy succeeded and the removal did not, raises
    `CopiedNotRemovedError`."""
    check_movable(context)
    if not uids:
        return Moved(destination, None)
    client = context.client
    wanted = list(uids)
    # imapclient's move() and copy() drop the response codes; imaplib keeps them here, COPYUID
    # untagged for MOVE and tagged for COPY, both under its code.
    responses = client._imap.untagged_responses  # noqa: SLF001
    responses.pop("COPYUID", None)
    if client.has_capability("MOVE"):
        client.move(wanted, destination)
        return _moved(destination, responses.pop("COPYUID", None), len(wanted))
    moved = copy_uids(context, wanted, destination)
    try:
        client.add_flags(wanted, [_DELETED], silent=True)
        client.uid_expunge(wanted)
    except Exception as error:
        code: MailErrorCode = mail_error(error, context.account).code
        if code in REFUSALS:
            code = "server_error"  # something was written
        raise CopiedNotRemovedError(code, moved) from None
    return moved


def copy_uids(context: MailboxContext, uids: Sequence[int], destination: str) -> Moved:
    """Copies `uids` to `destination`, leaving them where they are (on Gmail, adds the
    destination's label)."""
    if not uids:
        return Moved(destination, None)
    client = context.client
    responses = client._imap.untagged_responses  # noqa: SLF001
    responses.pop("COPYUID", None)
    client.copy(list(uids), destination)
    return _moved(destination, responses.pop("COPYUID", None), len(uids))


def store_flags(
    context: MailboxContext,
    uids: Sequence[int],
    *,
    add: Collection[str] = (),
    remove: Collection[str] = (),
) -> None:
    """Sets the flags of `add` on `uids`, then clears those of `remove`; only
    `SETTABLE_FLAGS`, anything else (`\\Deleted` above all) refused with `invalid_argument`
    before any is stored. `TO_SORT`, a keyword, is `unsupported` where the folder keeps none
    (`MailboxContext.keywords`). Silent: nothing is read back."""
    other = [flag for flag in (*add, *remove) if flag not in SETTABLE_FLAGS]
    if other:
        raise MailError(
            "invalid_argument", "Only \\Seen, \\Flagged, \\Answered and To sort can be set."
        )
    if set(add) & set(remove):
        raise MailError("invalid_argument", "A flag cannot be both set and cleared.")
    if TO_SORT in (*add, *remove) and not context.keywords:
        raise MailError(
            "unsupported",
            "This mail server keeps no keywords in this folder, so it cannot keep To sort.",
        )
    if not uids:
        return
    client = context.client
    wanted = list(uids)
    if add:
        client.add_flags(wanted, [flag.encode() for flag in dict.fromkeys(add)], silent=True)
    if remove:
        client.remove_flags(wanted, [flag.encode() for flag in dict.fromkeys(remove)], silent=True)


def store_forwarded(context: MailboxContext, uids: Sequence[int]) -> None:
    """Sets `FORWARDED` on `uids` where the folder keeps keywords (`MailboxContext.keywords`);
    elsewhere, Gmail included, nothing is set. Silent: nothing is read back."""
    if context.keywords and uids:
        context.client.add_flags(list(uids), [FORWARDED.encode()], silent=True)


def delete_uids(context: MailboxContext, uids: Sequence[int]) -> None:
    """Deletes `uids` for good: marks them `\\Deleted` and expunges exactly those UIDs, leaving
    any other message marked `\\Deleted` where it is. Needs UIDPLUS: without it, `unsupported`
    before anything is marked.

    Mailroom deletes mail only deliberately (docs/core.md, *Safety*). This is for three of those
    cases: a draft it wrote itself, when replaced or deleted; a filter's `delete` action, the
    user's own rule; and a message moved to another account "for good", once its copy there is
    confirmed (a folder's delete, the fourth, is `delete_folder`). Nothing else calls this.
    """
    if not context.client.has_capability("UIDPLUS"):
        raise MailError(
            "unsupported",
            "This server can only delete mail by also deleting other messages marked for deletion.",
        )
    if not uids:
        return
    wanted = list(uids)
    context.client.add_flags(wanted, [_DELETED], silent=True)
    context.client.uid_expunge(wanted)


def _moved(destination: str, data: Sequence[bytes] | None, count: int) -> Moved:
    copied = _copy_uids(data, count)
    if copied is None:
        return Moved(destination, None)
    return Moved(destination, copied[0], copied[1])


def _copy_uids(data: Sequence[bytes] | None, count: int) -> tuple[int, dict[int, int]] | None:
    """A COPYUID response code's data (`<uidvalidity> <source set> <destination set>`, RFC
    4315; the last one when several came) as the destination's UIDVALIDITY and old UID → new
    UID; None when there is none, or it does not parse or names more than `count` messages."""
    if not data:
        return None
    try:
        validity, source, target = data[-1].split()
        pairs = zip(_uid_set(source, count), _uid_set(target, count), strict=True)
        return int(validity), dict(pairs)
    except ValueError:
        return None


def _uid_set(text: bytes, limit: int) -> list[int]:
    """A UID set of numbers and ranges, in its order (the two sets of COPYUID correspond);
    ValueError past `limit` UIDs, so a hostile range cannot fill memory."""
    out: list[int] = []
    for piece in text.split(b","):
        low, _, high = piece.partition(b":")
        first, last = int(low), int(high or low)
        if len(out) + abs(last - first) + 1 > limit:
            raise ValueError("more UIDs than were copied")
        out.extend(range(first, last + 1) if first <= last else range(first, last - 1, -1))
    return out
