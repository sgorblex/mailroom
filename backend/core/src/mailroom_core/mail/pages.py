"""Newest-first pages of messages over one or more folders (`src/mail/pages.ts`): the list,
search and thread results, with the TypeScript's merge, cursor format and previews.

The cursor holds, per source, the UID below which the walk continues, so mail arriving between
pages never shifts them; a source whose UIDVALIDITY changed fails the cursor rather than skipping
or repeating. A source that fails is reported and keeps its place for the next page.
"""

import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

from mailroom_core.mail._mime import addresses, aware_date, header_text, text_value
from mailroom_core.mail.download import body_text, part_contents
from mailroom_core.mail.errors import MailError, MailErrorCode
from mailroom_core.mail.ref import (
    MessageRef,
    b64url_encode,
    b64url_json,
    format_ref,
    js_integer,
    js_json,
)
from mailroom_core.mail.selected import Criteria, search_arguments, search_uids
from mailroom_core.mail.structure import Structure, analyse_structure
from mailroom_core.render import (
    Address,
    ListItem,
    clean_header,
    list_line,
    render_body,
)
from mailroom_core.render._js import WHITESPACE, trim, utf16_prefix

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from mailroom_core.mail.imap import MailboxContext, MailConnections

_CURSOR_VERSION: Final = 1
_MAX_UID: Final = 0xFFFFFFFF
"""UIDs are 32-bit (RFC 3501): a cursor position beyond is no position."""
_FROM_NEWEST: Final = 0
PREVIEW_BYTES: Final = 2048
"""Decoded bytes of the body part a preview is made from."""
_PREVIEW_CHARS: Final = 1000
PREVIEW_LENGTH: Final = 200
"""A preview's length at most, in UTF-16 units."""

_FETCH: Final = ["ENVELOPE", "FLAGS", "BODYSTRUCTURE", "INTERNALDATE", "RFC822.SIZE"]
_SPACE: Final = f"[{re.escape(WHITESPACE)}]"
_LINK: Final = re.compile(
    # JavaScript's `.` (any but a line terminator) after the backslash, and its `\s`.
    rf"\[((?:[^\]\\]|\\[^\n\r\u2028\u2029])*)\]\((?:https?|mailto):[^){re.escape(WHITESPACE)}]*\)"
)
_LINK_ESCAPE: Final = re.compile(r"\\([\[\]])")
_SPACES: Final = re.compile(f"{_SPACE}+")


@dataclass(frozen=True, slots=True)
class Source:
    """One folder of one account that a page reads from."""

    account: str
    folder: str


@dataclass(frozen=True, slots=True)
class Position:
    """Per source: the UIDVALIDITY the walk started under (`""` for a source that failed before
    it was ever read) and the UID below which it continues (0: from the newest)."""

    account: str
    folder: str
    uid_validity: str
    before_uid: int

    def json(self) -> list[object]:
        """The position as a cursor entry: account, folder, UIDVALIDITY and before-UID."""
        return [self.account, self.folder, self.uid_validity, self.before_uid]


def encode_cursor(positions: Sequence[Position]) -> str:
    """The cursor for the next page: ``positions`` as base64url-encoded, versioned JSON."""
    return b64url_encode(js_json([_CURSOR_VERSION, [p.json() for p in positions]]))


def decode_cursor(text: str) -> list[Position]:
    """`invalid_argument` unless `text` is a cursor `encode_cursor` (or the TypeScript) made."""
    try:
        value = b64url_json(text)
    except ValueError:
        value = None
    if (
        isinstance(value, list)
        and len(value) >= 2
        and js_integer(value[0]) == _CURSOR_VERSION
        and isinstance(value[1], list)
    ):
        positions: list[Position] = []
        for p in value[1]:
            if not (
                isinstance(p, list)
                and len(p) == 4
                and isinstance(p[0], str)
                and isinstance(p[1], str)
                and isinstance(p[2], str)
            ):
                break
            before = js_integer(p[3])
            if before is None or not 0 <= before <= _MAX_UID:
                break
            positions.append(Position(p[0], p[1], p[2], before))
        else:
            return positions
    raise MailError("invalid_argument", "Not a cursor; use the one from the previous page.")


@dataclass(frozen=True, slots=True)
class MessageSummary:
    """One message of a page. Header text is decoded and flattened (no line breaks or invisible
    characters), but still the sender's words: escape it, or put it in the envelope."""

    ref: str
    account: str
    folder: str
    uid: int
    uid_validity: int
    date: datetime | None
    """The Date header, else the arrival time."""
    received: datetime | None
    """INTERNALDATE, the arrival time: what pages are merged by."""
    sender: tuple[Address, ...]
    """The From header."""
    subject: str
    """`""` when absent."""
    flags: frozenset[str]
    """As the server has them, `\\Seen`."""
    has_attachments: bool
    """Some part is an attachment (`Structure.attachments`; inline parts are not)."""
    preview: str | None
    """The start of the body as one line, rendered as a read renders it, minus quoted history;
    None when there is no text body or previews were not asked for."""
    size: int
    """RFC822.SIZE: the whole message as stored, in bytes (parts are sized decoded)."""

    @property
    def message_ref(self) -> MessageRef:
        """The summary's message ref."""
        return MessageRef(self.account, self.folder, self.uid_validity, self.uid)

    def list_item(self) -> ListItem:
        """The summary as the item its list line is made from."""
        return ListItem(
            ref=self.ref,
            date=self.date,
            sender=self.sender,
            subject=self.subject or None,
            flags=self.flags,
            has_attachments=self.has_attachments,
            preview=self.preview,
        )

    def list_line(self, time_zone: str) -> str:
        """The TypeScript's line: `ref · date · from · subject · flags · preview`."""
        return list_line(self.list_item(), time_zone)


@dataclass(frozen=True, slots=True)
class PageFailure:
    """A source that could not be read: the page goes on without it."""

    account: str
    folder: str | None
    """None when the account failed before a folder was chosen (search over all folders)."""
    code: MailErrorCode
    message: str
    """The MailError's fixed message."""

    @classmethod
    def of(cls, account: str, folder: str | None, error: MailError) -> PageFailure:
        """The failure of one source, from the error that stopped it."""
        return cls(account, folder, error.code, error.message)

    @property
    def text(self) -> str:
        """The TypeScript's wording, `work/INBOX: <message>`."""
        where = (
            self.account
            if self.folder is None
            else f"{self.account}/{clean_header(self.folder, 200)}"
        )
        return f"{where}: {self.message}"


@dataclass(frozen=True, slots=True)
class Page:
    """A page of messages, the cursor for the next one and the sources that failed."""

    messages: tuple[MessageSummary, ...]
    next_cursor: str | None
    """Set when more messages follow: pass it back, with the same query, as `cursor`."""
    failures: tuple[PageFailure, ...] = field(default=())

    def lines(self, time_zone: str) -> list[str]:
        """The messages as list lines, with dates in ``time_zone``."""
        return [message.list_line(time_zone) for message in self.messages]


@dataclass(eq=False, slots=True)
class _Candidate:
    index: int
    """Of its source."""
    uid_validity: int
    uid: int
    data: Mapping[bytes, object]
    structure: Structure
    arrival: float


def _timestamp(value: datetime | None) -> float:
    try:
        return 0.0 if value is None else value.timestamp()
    except OverflowError, ValueError, OSError:
        return 0.0


def preview_of(content: str, format: Literal["text", "html"]) -> str:  # noqa: A002
    """A body's start as a list preview: rendered as a read renders it (entities decoded,
    quotes collapsed), minus the markers, links as their text (a URL would fill it), one line."""
    rendered = render_body(
        content, format, include_quoted=False, offset=0, max_chars=_PREVIEW_CHARS, marker=None
    ).text
    joined = " ".join(rendered.split("\n"))
    joined = _LINK.sub(lambda match: _LINK_ESCAPE.sub(r"\1", match.group(1)), joined)
    return utf16_prefix(trim(_SPACES.sub(" ", joined)), PREVIEW_LENGTH)


def _summary(source: Source, candidate: _Candidate, preview: str | None) -> MessageSummary:
    data = candidate.data
    envelope = data.get(b"ENVELOPE")
    received = aware_date(data.get(b"INTERNALDATE"))
    date = aware_date(getattr(envelope, "date", None)) or received
    size = data.get(b"RFC822.SIZE")
    flags = data.get(b"FLAGS")
    return MessageSummary(
        ref=format_ref(
            MessageRef(source.account, source.folder, candidate.uid_validity, candidate.uid)
        ),
        account=source.account,
        folder=source.folder,
        uid=candidate.uid,
        uid_validity=candidate.uid_validity,
        date=date,
        received=received,
        sender=addresses(getattr(envelope, "from_", None)),
        subject=header_text(getattr(envelope, "subject", None)),
        flags=frozenset(
            text_value(flag) for flag in (flags if isinstance(flags, tuple | list) else ())
        ),
        has_attachments=len(candidate.structure.attachments) > 0,
        preview=preview,
        size=size if isinstance(size, int) else 0,
    )


def _fetch_candidates(
    context: MailboxContext, uids: Sequence[int], index: int = 0
) -> list[_Candidate]:
    """What a page shows of `uids`, highest UID first; UIDs gone meanwhile are left out."""
    if not uids:
        return []
    fetched = context.client.fetch(list(uids), _FETCH)
    candidates = []
    for uid, data in fetched.items():
        body = data.get(b"BODYSTRUCTURE")
        structure = analyse_structure(body if isinstance(body, tuple | list) else ())
        received = aware_date(data.get(b"INTERNALDATE"))
        envelope_date = aware_date(getattr(data.get(b"ENVELOPE"), "date", None))
        candidates.append(
            _Candidate(
                index=index,
                uid_validity=context.uid_validity,
                uid=int(uid),
                data=data,
                structure=structure,
                arrival=_timestamp(received or envelope_date),
            )
        )
    candidates.sort(key=lambda c: c.uid, reverse=True)
    return candidates


def _by_account[T](
    sources: Sequence[Source], run: Callable[[int], T]
) -> dict[int, T | BaseException]:
    """`run(index)` for every source: the sources of one account one after the other (they share
    its connections), different accounts side by side."""
    groups: dict[str, list[int]] = {}
    for index, source in enumerate(sources):
        groups.setdefault(source.account, []).append(index)

    def group(indexes: list[int]) -> dict[int, T | BaseException]:
        out: dict[int, T | BaseException] = {}
        for index in indexes:
            try:
                out[index] = run(index)
            except Exception as error:
                out[index] = error
        return out

    results: dict[int, T | BaseException] = {}
    if len(groups) <= 1:
        for indexes in groups.values():
            results.update(group(indexes))
        return results
    with ThreadPoolExecutor(max_workers=min(len(groups), 8)) as pool:
        for part in pool.map(group, groups.values()):
            results.update(part)
    return results


def read_page(
    connections: MailConnections,
    sources: Sequence[Source],
    criteria: Criteria | Mapping[str, Criteria],
    *,
    limit: int,
    cursor: str | None = None,
    previews: bool = True,
) -> Page:
    """Up to `limit` messages matching `criteria` across `sources`, newest first by arrival.
    `criteria` may differ per account (a mapping with every source's account), as a provider's
    own search keys do.

    Each source is taken from its highest UID down, whichever head arrived last first: what a
    source shows is then always its highest fetched UIDs, so the next page skips nothing, even
    when a message's date is older than its UID suggests (moved or imported mail). Sources of
    different accounts are read side by side, in threads of their own.
    """
    if limit < 1:
        raise MailError("invalid_argument", "limit must be at least 1.")
    by_account = (
        criteria
        if isinstance(criteria, Mapping)
        else {source.account: criteria for source in sources}
    )
    missing = sorted({source.account for source in sources} - by_account.keys())
    if missing:
        raise ValueError(f"criteria has no entry for the accounts {missing}")
    for each in by_account.values():
        search_arguments(each)  # refused here, not as a failure of each source
    positions = None if cursor is None else decode_cursor(cursor)

    def position_of(source: Source) -> Position | None:
        if positions is None:
            return None
        return next(
            (p for p in positions if p.account == source.account and p.folder == source.folder),
            None,
        )

    exhausted: set[int] = set()

    def read(index: int) -> list[_Candidate]:
        source = sources[index]
        position = position_of(source)
        if positions is not None and position is None:
            exhausted.add(index)
            return []

        def run(context: MailboxContext) -> list[_Candidate]:
            if (
                position is not None
                and position.uid_validity != ""
                and position.uid_validity != str(context.uid_validity)
            ):
                raise MailError(
                    "stale_reference",
                    "The folder changed since the previous page; start over without a cursor.",
                )
            before = (
                None
                if position is None or position.before_uid == _FROM_NEWEST
                else position.before_uid
            )
            if before is not None and before <= 1:
                exhausted.add(index)
                return []
            own = by_account[source.account]
            scoped = list(own) if before is None else ["UID", f"1:{before - 1}", *own]
            uids = sorted(search_uids(context, scoped or ["ALL"]), reverse=True)[:limit]
            if len(uids) < limit:
                exhausted.add(index)
            return _fetch_candidates(context, uids, index)

        return connections.with_mailbox(source.account, source.folder, True, run)

    results = _by_account(sources, read)
    per_source: list[list[_Candidate]] = []
    failures: list[PageFailure] = []
    failed: set[int] = set()
    for index, source in enumerate(sources):
        result = results[index]
        if isinstance(result, BaseException):
            if isinstance(result, MailError) and result.code == "stale_reference":
                raise result
            error = result if isinstance(result, MailError) else MailError("server_error")
            failures.append(PageFailure.of(source.account, source.folder, error))
            failed.add(index)
            per_source.append([])
        else:
            per_source.append(result)

    heads = [0] * len(per_source)
    taken: list[_Candidate] = []
    while len(taken) < limit:
        best: int | None = None
        for i, candidates in enumerate(per_source):
            if heads[i] >= len(candidates):
                continue
            if best is None or candidates[heads[i]].arrival > per_source[best][heads[best]].arrival:
                best = i
        if best is None:
            break
        taken.append(per_source[best][heads[best]])
        heads[best] += 1

    shown_previews = _previews(connections, sources, taken) if previews else {}
    messages = tuple(_summary(sources[c.index], c, shown_previews.get(id(c))) for c in taken)

    # Next positions: below the lowest UID shown per source; a source with nothing shown keeps
    # its place.
    following: list[Position] = []
    more = False
    for index, source in enumerate(sources):
        shown = [c for c in taken if c.index == index]
        previous = position_of(source)
        if index in failed:
            more = True
            following.append(previous or Position(source.account, source.folder, "", _FROM_NEWEST))
            continue
        fetched = per_source[index]
        if fetched:
            uid_validity: str | None = str(fetched[0].uid_validity)
        else:
            uid_validity = None if previous is None else previous.uid_validity
        if uid_validity is None:
            continue
        if shown:
            before = min(c.uid for c in shown)
        else:
            before = _FROM_NEWEST if previous is None else previous.before_uid
        if len(fetched) > len(shown) or index not in exhausted:
            more = True
            following.append(Position(source.account, source.folder, uid_validity, before))
    return Page(
        messages=messages,
        next_cursor=encode_cursor(following) if more else None,
        failures=tuple(failures),
    )


def _previews(
    connections: MailConnections, sources: Sequence[Source], taken: Sequence[_Candidate]
) -> dict[int, str]:
    """One small download per shown message's body part, under its folder's connection; a
    source whose previews fail shows none."""
    wanted: dict[int, list[_Candidate]] = {}
    for candidate in taken:
        if candidate.structure.body is not None:
            wanted.setdefault(candidate.index, []).append(candidate)
    if not wanted:
        return {}
    indexes = sorted(wanted)

    def fetch(position: int) -> dict[int, bytes]:
        index = indexes[position]
        source = sources[index]
        mine = wanted[index]

        def run(context: MailboxContext) -> dict[int, bytes]:
            current = [c for c in mine if c.uid_validity == context.uid_validity]
            return part_contents(
                context.client,
                [(c.uid, c.structure.body) for c in current if c.structure.body is not None],
                PREVIEW_BYTES,
            )

        return connections.with_mailbox(source.account, source.folder, True, run)

    picked = [sources[index] for index in indexes]
    results = _by_account(picked, fetch)
    out: dict[int, str] = {}
    for position, index in enumerate(indexes):
        result = results[position]
        if isinstance(result, BaseException):
            continue
        for candidate in wanted[index]:
            body = candidate.structure.body
            data = result.get(candidate.uid)
            if body is None or data is None:
                continue
            text = body_text(data, body, complete=len(data) < PREVIEW_BYTES)
            fmt = candidate.structure.body_format
            out[id(candidate)] = preview_of(text, fmt or "text")
    return out
