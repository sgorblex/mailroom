"""Reading lists, searches and folder counts from the mail index
(``docs/requirements/mail-client.md``, *Index*), for the mail service, which reads live whenever
a folder a read involves is not indexed and ready.

A source is read from the index when its folder's row is on, ``ready`` and holds a UIDVALIDITY. A
read of every folder leaves out a virtual one (All Mail, Flagged) off Gmail unless it is on. On
Gmail (``X-GM-EXT-1``) INBOX and the label folders are All Mail's rows carrying their label
(``gmail_label``), each with All Mail's ref; Spam and Trash stay live. Rows are ordered newest
first by arrival, then folder and UID: the index's cursor (version 3) keeps that key, so mail
indexed meanwhile never shifts a page. A folder indexed under an age limit holds only the mail
since its day: once its rows run out, the rest of it is read live (``BEFORE`` that day), and the
cursor then carries the live one.

Grouped by conversation (``read_conversations``), a read gives each conversation once, at its
latest matching row, with the rest of its rows in the source read: within one account, those of
the same ``thread_key`` (``mailroom.mail.index.threads``), a row without one alone. Paged as rows
are, by that latest row's key.

Every query is the user's own (``user_id``). Rows are mail-derived and untrusted: nothing here logs
them.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import (
    BigInteger,
    Text,
    and_,
    any_,
    func,
    literal,
    not_,
    or_,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import aliased

from mailroom.mail.index import settings as index_settings
from mailroom.models import MailIndexFolder, MailIndexMessage
from mailroom_core import mail as core
from mailroom_core.mail import (
    TO_SORT,
    MailError,
    MessageRef,
    MessageSummary,
    Page,
    PageFailure,
)
from mailroom_core.render import Address

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import InstrumentedAttribute, Session

    from mailroom_core.mail import Folder, SearchQuery, Source

CURSOR_VERSION: Final = 3
"""The index's cursors; mailroom-core's live ones are version 2."""
INDEX: Final = "index"
TAIL: Final = "tail"
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND: Final = timedelta(microseconds=1)
_SEEN: Final = "\\Seen"
_FLAGGED: Final = "\\Flagged"
INBOX_LABEL: Final = "\\Inbox"
"""The X-GM-LABELS label of Gmail's INBOX, which is a label view of All Mail."""
_GMAIL_SPECIAL: Final = {
    "\\sent": "\\Sent",
    "\\drafts": "\\Draft",
    "\\flagged": "\\Starred",
    "\\important": "\\Important",
}
"""Gmail's special-use folders read from All Mail by the label X-GM-LABELS gives them."""
_GMAIL_LIVE: Final = frozenset({"\\all", "\\junk", "\\trash"})
"""All Mail is its own row; Spam and Trash, which All Mail leaves out, are never indexed."""
_STALE: Final = "The folder changed since the previous page; start over without a cursor."


def gmail_label(folder: Folder) -> str | None:
    """The X-GM-LABELS label that puts a message in this Gmail folder, None for All Mail, Spam,
    Trash and a folder that cannot be read."""
    if not folder.selectable:
        return None
    if folder.path.upper() == "INBOX":
        return INBOX_LABEL
    lowered = {flag.lower() for flag in folder.flags}
    if lowered & _GMAIL_LIVE:
        return None
    return next((_GMAIL_SPECIAL[f] for f in sorted(lowered) if f in _GMAIL_SPECIAL), folder.path)


def readable(query: SearchQuery | None) -> bool:
    """Whether the index can answer ``query``: not a body's text, nor Gmail's own syntax."""
    return query is None or (query.text is None and query.gmail_query is None)


@dataclass(frozen=True, slots=True)
class IndexSource:
    """An indexed folder a read takes rows from: all of them, or on Gmail those carrying one of
    ``labels``."""

    account: int
    folder: str
    """The indexed folder's path: All Mail's for a Gmail label."""
    folder_id: int
    uid_validity: int
    labels: tuple[str, ...] | None
    oldest: date | None
    """The folder's age-limit day: what arrived before it is read live."""
    live: tuple[str, ...]
    """The folders read live once the rows run out: the label folders, else this one."""
    listed: tuple[tuple[str, str], ...] = ()
    """With ``labels``, each label and the folder it lists, in the order the read named them: a
    row is listed under the first whose label it carries (``ListedSummary``)."""

    def json(self) -> list[object]:
        labels = None if self.labels is None else list(self.labels)
        return [
            self.account,
            self.folder,
            self.uid_validity,
            self.folder_id,
            labels,
            list(self.live),
            [list(pair) for pair in self.listed],
        ]


@dataclass(frozen=True, slots=True)
class ListedSummary(MessageSummary):
    """A summary of a Gmail All Mail row read for a label's folder."""

    listed_folder: str | None = None
    """The folder it was listed under (INBOX or a label's), where an action on the row from
    that list acts (``service.organize``)."""


def _ready(user_id: int) -> list[ColumnElement[bool]]:
    return [
        MailIndexFolder.user_id == user_id,
        MailIndexFolder.enabled.is_(True),
        MailIndexFolder.state == "ready",
        MailIndexFolder.uid_validity.is_not(None),
    ]


def ready_rows(
    db: Session, user_id: int, accounts: Sequence[int]
) -> dict[tuple[int, str], MailIndexFolder]:
    """The user's readable folder rows of ``accounts``, by account and path."""
    if not accounts:
        return {}
    query = select(MailIndexFolder).where(
        *_ready(user_id), MailIndexFolder.account_id.in_(sorted(set(accounts)))
    )
    return {(row.account_id, row.path): row for row in db.scalars(query)}


def index_sources(
    db: Session,
    user_id: int,
    sources: Sequence[Source],
    *,
    gmail: Callable[[int], bool],
    listing: Callable[[int], Sequence[Folder]],
    every_folder: bool = False,
    rows: Mapping[tuple[int, str], MailIndexFolder] | None = None,
) -> list[IndexSource] | None:
    """``sources`` as the index reads them, None unless every one is indexed and ready.
    ``gmail`` and ``listing`` are asked only of an account with a readable row and a source
    without one of its own (a Gmail label's folder), or, with ``every_folder`` (``sources`` are
    a read of all folders), of each account with a readable row (``every_folder_sources``).
    ``rows``: ``ready_rows`` of the sources' accounts when the caller has read them already."""
    if not sources:
        return None
    if rows is None:
        rows = ready_rows(db, user_id, [source.account for source in sources])
    indexed = {account for account, _ in rows}
    if every_folder:
        sources = every_folder_sources(db, user_id, sources, indexed, gmail=gmail, listing=listing)
    picked: dict[int, tuple[MailIndexFolder, set[str] | None, list[str]]] = {}
    listed: dict[int, dict[str, str]] = {}
    for source in sources:
        path = "INBOX" if source.folder.upper() == "INBOX" else source.folder
        row = rows.get((source.account, path))
        label: str | None = None
        if row is None:
            if source.account not in indexed or not gmail(source.account):
                return None
            folders = listing(source.account)
            every = next((f for f in folders if f.role == "all"), None)
            here = next((f for f in folders if f.path == source.folder), None)
            label = None if here is None else gmail_label(here)
            row = None if every is None else rows.get((source.account, every.path))
            if row is None or label is None:
                return None
        _, labels, live = picked.setdefault(row.id, (row, set(), []))
        if label is None or labels is None:
            picked[row.id] = (row, None, live)
        else:
            labels.add(label)
            listed.setdefault(row.id, {}).setdefault(label, source.folder)
        live.append(source.folder)
    return [
        IndexSource(
            account=row.account_id,
            folder=row.path,
            folder_id=row.id,
            uid_validity=row.uid_validity or 0,
            labels=None if labels is None else tuple(sorted(labels)),
            oldest=row.oldest_indexed,
            live=tuple(dict.fromkeys(live)),
            listed=() if labels is None else tuple(listed.get(row.id, {}).items()),
        )
        for row, labels, live in picked.values()
    ]


def every_folder_sources(
    db: Session,
    user_id: int,
    sources: Sequence[Source],
    indexed: set[int],
    *,
    gmail: Callable[[int], bool],
    listing: Callable[[int], Sequence[Folder]],
) -> list[Source]:
    """``sources`` of a read of every folder with, for each indexed account but Gmail's, its
    folders picked again as mailroom-core's ``searchable_folders`` picks them, leaving out each
    virtual folder (``settings.virtual``) that is off the index: what the index's read takes."""
    query = select(MailIndexFolder.account_id, MailIndexFolder.path).where(
        MailIndexFolder.user_id == user_id,
        MailIndexFolder.enabled.is_(True),
        MailIndexFolder.account_id.in_(sorted(indexed)),
    )
    on = {(account, path) for account, path in db.execute(query)}
    out: list[Source] = []
    for account in dict.fromkeys(source.account for source in sources):
        if account not in indexed or gmail(account):
            out.extend(source for source in sources if source.account == account)
            continue
        kept = [
            f
            for f in listing(account)
            if f.selectable and (not index_settings.virtual(f) or (account, f.path) in on)
        ]
        every = next((f for f in kept if f.role == "all"), None)
        paths = (
            [every.path]
            if every is not None
            else [f.path for f in kept if f.role not in ("junk", "trash")]
        )
        out.extend(core.Source(account, path) for path in paths)
    return out


def resumed_sources(
    db: Session, user_id: int, cursor: IndexCursor, accounts: Sequence[int]
) -> list[IndexSource]:
    """The cursor's sources of ``accounts`` as the index holds them now: ``stale_reference``
    when one is no longer readable, or its folder changed, or none of them is of ``accounts``
    (another account's cursor)."""
    rows = ready_rows(db, user_id, accounts)
    out: list[IndexSource] = []
    for source in cursor.sources:
        if source.account not in accounts:
            continue
        row = rows.get((source.account, source.folder))
        if row is None or row.id != source.folder_id or row.uid_validity != source.uid_validity:
            raise MailError("stale_reference", _STALE)
        out.append(replace(source, oldest=row.oldest_indexed))
    if cursor.sources and not out:
        raise MailError("stale_reference", _STALE)
    return out


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(), UTC)


def _has(column: InstrumentedAttribute[list[str]], flag: str) -> ColumnElement[bool]:
    return literal(flag) == any_(column)


def _criteria(
    query: SearchQuery | None, message: type[MailIndexMessage] = MailIndexMessage
) -> list[ColumnElement[bool]]:
    """The query's criteria on the rows (``message``, or an alias of it): substrings of From, To
    and the subject (any case), the arrival day (UTC), the flags, an attachment."""
    if query is None:
        return []
    out: list[ColumnElement[bool]] = []
    for column, term in (
        (message.sender_text, query.sender),
        (message.to_text, query.to),
        (message.subject, query.subject),
    ):
        if term is not None:
            out.append(column.icontains(term, autoescape=True))
    if query.since is not None:
        out.append(message.received >= _midnight(query.since))
    if query.before is not None:
        out.append(message.received < _midnight(query.before))
    marks: list[tuple[str, bool | None, InstrumentedAttribute[list[str]]]] = [
        (_SEEN, None if query.unread is None else not query.unread, message.flags),
        (_FLAGGED, query.flagged, message.flags),
        (TO_SORT, query.to_sort, message.keywords),
    ]
    for flag, wanted, kept in marks:
        if wanted is not None:
            held = _has(kept, flag)
            out.append(held if wanted else not_(held))
    if query.has_attachment is True:
        out.append(message.has_attachments.is_(True))
    return out


type Key = tuple[datetime, int, int]
"""Arrival time, folder id and UID: a row's place, newest first."""


def _of_sources(
    user_id: int,
    sources: Sequence[IndexSource],
    query: SearchQuery | None,
    message: type[MailIndexMessage] = MailIndexMessage,
) -> list[ColumnElement[bool]]:
    """The user's rows (``message``, or an alias of it) of ``sources`` (on Gmail, those carrying
    one of a source's labels) matching ``query``."""
    where = []
    for source in sources:
        own = message.folder_id == source.folder_id
        if source.labels is not None:
            own = and_(own, message.labels.overlap(literal(list(source.labels), ARRAY(Text))))
        where.append(own)
    return [message.user_id == user_id, or_(*where), *_criteria(query, message)]


def _after(after: Key | None) -> list[ColumnElement[bool]]:
    if after is None:
        return []
    message = MailIndexMessage
    received, folder_id, uid = after
    key = tuple_(message.received, message.folder_id, message.uid)
    return [
        key
        < tuple_(
            literal(received),
            literal(folder_id, BigInteger),
            literal(uid, BigInteger),
        )
    ]


_NEWEST: Final = (
    MailIndexMessage.received.desc(),
    MailIndexMessage.folder_id.desc(),
    MailIndexMessage.uid.desc(),
)


def read_rows(
    db: Session,
    user_id: int,
    sources: Sequence[IndexSource],
    query: SearchQuery | None,
    *,
    limit: int,
    after: Key | None = None,
) -> list[tuple[MessageSummary, Key]]:
    """Up to ``limit`` of the sources' rows matching ``query``, newest first, after ``after``;
    each as a page's summary, with its key."""
    if not sources:
        return []
    message = MailIndexMessage
    by_folder = {source.folder_id: source for source in sources}
    statement = (
        select(message)
        .where(*_of_sources(user_id, sources, query), *_after(after))
        .order_by(*_NEWEST)
        .limit(limit)
    )
    return [
        (_summary(by_folder[row.folder_id], row), (row.received, row.folder_id, row.uid))
        for row in db.scalars(statement)
    ]


def _listed_folder(source: IndexSource, row: MailIndexMessage) -> str | None:
    """The folder ``row`` was listed under: the first of the source's labels it carries."""
    carried = set(row.labels or ())
    return next((path for label, path in source.listed if label in carried), None)


def _summary(source: IndexSource, row: MailIndexMessage) -> MessageSummary:
    ref = MessageRef(source.account, source.folder, source.uid_validity, row.uid)
    return ListedSummary(
        ref=core.format_ref(ref),
        account=source.account,
        folder=source.folder,
        uid=row.uid,
        uid_validity=source.uid_validity,
        date=row.date or row.received,
        received=row.received,
        sender=_addresses(row.sender),
        subject=row.subject,
        flags=frozenset((*row.flags, *row.keywords)),
        has_attachments=row.has_attachments,
        preview=row.snippet,
        size=row.size,
        listed_folder=_listed_folder(source, row),
    )


MAX_PARTICIPANTS: Final = 4
"""The senders a conversation names, in the order they first wrote."""


_OWN_LABELS: Final = frozenset({"\\Sent", "\\Draft"})
"""The Gmail labels of the user's own copies: what they sent, what they are writing."""
_OWN_ROLES: Final = frozenset({"sent", "drafts"})


@dataclass(frozen=True, slots=True)
class Conversation:
    """A conversation of a page: its messages in the source read (its latest is the page's
    message), how many are unread, the first senders' display names and whether one has an
    attachment or is flagged, all of them counted; the refs an action on it takes, newest
    first, which leave out the user's own copies (``own_copy``) unless it holds nothing else;
    the folder each of those was listed under where it is not its own (``ListedSummary``); and
    whether its latest message's sender is new to it: neither the sender nor a recipient of
    any other of its messages."""

    count: int
    unread: int
    participants: tuple[str, ...]
    has_attachments: bool
    flagged: bool
    refs: tuple[str, ...]
    listed_folders: Mapping[str, str] = field(default_factory=dict)
    latest_from_new_sender: bool = False

    @classmethod
    def single(cls, summary: MessageSummary) -> Conversation:
        """A message alone: one the index cannot group (read live)."""
        listed = summary.listed_folder if isinstance(summary, ListedSummary) else None
        return cls(
            count=1,
            unread=0 if _SEEN in summary.flags else 1,
            participants=_participants([summary.sender]),
            has_attachments=summary.has_attachments,
            flagged=_FLAGGED in summary.flags,
            refs=(summary.ref,),
            listed_folders={} if listed is None else {summary.ref: listed},
        )


@dataclass(frozen=True, slots=True)
class ConversationPage(Page):
    """A page whose messages are each the latest of its conversation, ``conversations`` in the
    same order."""

    conversations: tuple[Conversation, ...] = ()


def _participants(senders: Iterable[Sequence[Address]]) -> tuple[str, ...]:
    names: dict[str, None] = {}
    for sender in senders:
        for person in sender[:1]:
            name = (person.name or person.address or "").strip()
            if name:
                names.setdefault(name)
    return tuple(names)[:MAX_PARTICIPANTS]


def read_conversations(
    db: Session,
    user_id: int,
    sources: Sequence[IndexSource],
    query: SearchQuery | None,
    *,
    limit: int,
    after: Key | None = None,
    listing: Callable[[int], Sequence[Folder]] | None = None,
) -> list[tuple[MessageSummary, Key, Conversation]]:
    """Up to ``limit`` of the conversations among the sources' rows matching ``query``, within
    one account each, newest first by their latest row, that row after ``after``: each as its
    latest row's summary, that row's key, and the conversation of the matching rows. A row's
    conversation is its ``thread_key`` (``mailroom.mail.index.threads``); a row without one is
    alone. A row is a conversation's latest when no newer
    matching row of its account has its key, which follows the newest-first index and stops at
    ``limit``, rather than ranking every row of the sources. ``listing`` gives an account's
    folders, asked only of a conversation across several folders not read by Gmail label, for
    which of them are its Sent and Drafts (``own_copy``)."""
    if not sources:
        return []
    message, folder = MailIndexMessage, MailIndexFolder
    newer, newer_folder = aliased(MailIndexMessage), aliased(MailIndexFolder)
    by_folder = {source.folder_id: source for source in sources}
    later = (
        select(newer.uid)
        .join(
            newer_folder,
            and_(newer_folder.id == newer.folder_id, newer_folder.user_id == newer.user_id),
        )
        .where(
            *_of_sources(user_id, sources, query, newer),
            newer_folder.account_id == folder.account_id,
            newer.thread_key == message.thread_key,
            tuple_(newer.received, newer.folder_id, newer.uid)
            > tuple_(message.received, message.folder_id, message.uid),
        )
    )
    latest = db.execute(
        select(message, folder.account_id)
        .join(folder, and_(folder.id == message.folder_id, folder.user_id == message.user_id))
        .where(*_of_sources(user_id, sources, query), *_after(after), ~later.exists())
        .order_by(*_NEWEST)
        .limit(limit)
    ).all()
    if not latest:
        return []
    threads = {(account, own.thread_key) for own, account in latest if own.thread_key is not None}
    members: dict[tuple[int, str | None], list[MailIndexMessage]] = {key: [] for key in threads}
    if threads:
        rows = db.execute(
            select(message, folder.account_id)
            .join(folder, and_(folder.id == message.folder_id, folder.user_id == message.user_id))
            .where(
                *_of_sources(user_id, sources, query),
                tuple_(folder.account_id, message.thread_key).in_(list(threads)),
            )
            .order_by(*_NEWEST)
        )
        for row, account in rows:
            members[(account, row.thread_key)].append(row)
    own_folders = _OwnFolders(sources, listing)
    out: list[tuple[MessageSummary, Key, Conversation]] = []
    for own, account in latest:
        held = [own] if own.thread_key is None else members[(account, own.thread_key)]
        if len({row.folder_id for row in held}) > 1:
            theirs = [row for row in held if not own_copy(row, own_folders())]
        else:
            # One folder: all the user's own, or none of them.
            theirs = [row for row in held if not own_copy(row, frozenset())]
        acted = theirs or held
        listed = {
            _ref(by_folder[row.folder_id], row.uid): path
            for row in acted
            if (path := _listed_folder(by_folder[row.folder_id], row)) is not None
        }
        found = Conversation(
            count=len(held),
            unread=sum(_SEEN not in row.flags for row in held),
            participants=_participants(_addresses(row.sender) for row in reversed(held)),
            has_attachments=any(row.has_attachments for row in held),
            flagged=any(_FLAGGED in row.flags for row in held),
            refs=tuple(_ref(by_folder[row.folder_id], row.uid) for row in acted),
            listed_folders=listed,
            latest_from_new_sender=_new_sender(own, held),
        )
        place = (own.received, own.folder_id, own.uid)
        out.append((_summary(by_folder[own.folder_id], own), place, found))
    return out


def own_copy(row: MailIndexMessage, own_folders: frozenset[int]) -> bool:
    """A copy of the user's own: on Gmail, labelled Sent or Draft; elsewhere, in one of
    ``own_folders`` (Sent and Drafts)."""
    if row.labels:
        return not _OWN_LABELS.isdisjoint(row.labels)
    return row.folder_id in own_folders


class _OwnFolders:
    """The ids of the sources' Sent and Drafts folders, read once asked (``listing``); none known
    without it, or when an account cannot say."""

    def __init__(
        self, sources: Sequence[IndexSource], listing: Callable[[int], Sequence[Folder]] | None
    ) -> None:
        self._sources, self._listing = sources, listing
        self._ids: frozenset[int] | None = None

    def __call__(self) -> frozenset[int]:
        if self._ids is None:
            ids: set[int] = set()
            whole = [source for source in self._sources if source.labels is None]
            for account in dict.fromkeys(source.account for source in whole):
                try:
                    listed = [] if self._listing is None else self._listing(account)
                except MailError:
                    listed = []
                paths = {f.path for f in listed if f.role in _OWN_ROLES}
                ids.update(s.folder_id for s in whole if s.account == account and s.folder in paths)
            self._ids = frozenset(ids)
        return self._ids


def _people_of(row: MailIndexMessage) -> set[str]:
    recipients = row.recipients or {}
    people = [*row.sender, *recipients.get("to", ()), *recipients.get("cc", ())]
    return {
        address.strip().casefold()
        for person in people
        if isinstance(address := person.get("address"), str) and address.strip()
    }


def _new_sender(latest: MailIndexMessage, held: Sequence[MailIndexMessage]) -> bool:
    """Whether ``latest``'s sender wrote or was written to in none of the other messages of
    ``held`` (copies of ``latest`` itself, by Message-ID, are not others); False for a message
    alone or without a sender's address."""
    others = [
        row
        for row in held
        if (row.folder_id, row.uid) != (latest.folder_id, latest.uid)
        and (latest.message_id is None or row.message_id != latest.message_id)
    ]
    [sender, *_] = [*_addresses(latest.sender), None]
    address = "" if sender is None else (sender.address or "").strip().casefold()
    if not others or not address:
        return False
    return all(address not in _people_of(row) for row in others)


def _ref(source: IndexSource, uid: int) -> str:
    return core.format_ref(MessageRef(source.account, source.folder, source.uid_validity, uid))


type Row = tuple[MessageSummary, Key, Conversation | None]
"""A page's message, its key, and its conversation when grouped."""


def read_page_rows(
    db: Session,
    user_id: int,
    sources: Sequence[IndexSource],
    query: SearchQuery | None,
    *,
    limit: int,
    after: Key | None = None,
    group: bool = False,
    listing: Callable[[int], Sequence[Folder]] | None = None,
) -> list[Row]:
    """``read_rows``, or with ``group`` ``read_conversations`` (``listing`` as its)."""
    out: list[Row] = []
    if group:
        out.extend(
            read_conversations(
                db, user_id, sources, query, limit=limit, after=after, listing=listing
            )
        )
    else:
        found = read_rows(db, user_id, sources, query, limit=limit, after=after)
        out.extend((summary, key, None) for summary, key in found)
    return out


def page_of(
    messages: Sequence[MessageSummary],
    conversations: Sequence[Conversation] | None,
    next_cursor: str | None,
    failures: Sequence[PageFailure],
) -> Page:
    """A page of ``messages``; grouped (``ConversationPage``) with ``conversations``, one for
    each message, the latest of it."""
    if conversations is None:
        return Page(tuple(messages), next_cursor, tuple(failures))
    return ConversationPage(tuple(messages), next_cursor, tuple(failures), tuple(conversations))


def _addresses(people: Sequence[Mapping[str, object]]) -> tuple[Address, ...]:
    def text(value: object) -> str | None:
        return value if isinstance(value, str) else None

    return tuple(
        Address(name=text(person.get("name")), address=text(person.get("address")))
        for person in people
    )


def tail_of(
    sources: Sequence[IndexSource], query: SearchQuery | None
) -> list[tuple[int, str, date]]:
    """What is read live once the rows run out: each folder indexed under an age limit, with its
    day, unless the query's own ``since`` leaves nothing before it."""
    since = None if query is None else query.since
    out: dict[tuple[int, str], date] = {}
    for source in sources:
        if source.oldest is None or (since is not None and since >= source.oldest):
            continue
        for path in source.live:
            out[(source.account, path)] = source.oldest
    return [(account, path, day) for (account, path), day in out.items()]


@dataclass(frozen=True, slots=True)
class IndexCursor:
    """A page's place in an index read (``index``: the sources and the last row's key) or in its
    live tail (``tail``: the folders read live, each with its day, and mailroom-core's cursor of
    that read, None before it began). ``carried``: the failures carried on, as mailroom-core
    encodes them, None for none."""

    mode: str
    sources: tuple[IndexSource, ...] = ()
    key: Key | None = None
    tail: tuple[tuple[int, str, date], ...] = ()
    live: str | None = None
    carried: str | None = None

    @classmethod
    def index(
        cls,
        sources: Sequence[IndexSource],
        key: Key,
        failures: Sequence[PageFailure],
        host_codes: Mapping[str, str],
    ) -> IndexCursor:
        return cls(INDEX, tuple(sources), key, carried=_carried(failures, host_codes))

    @classmethod
    def tail_from(
        cls,
        tail: Sequence[tuple[int, str, date]],
        live: str | None,
        failures: Sequence[PageFailure],
        host_codes: Mapping[str, str],
    ) -> IndexCursor:
        carried = None if live is not None else _carried(failures, host_codes)
        return cls(TAIL, tail=tuple(tail), live=live, carried=carried)

    def failures(self, host_codes: Mapping[str, str]) -> list[PageFailure]:
        """The failures the cursor carries (the live tail's cursor carries its own)."""
        carried = self.live if self.live is not None else self.carried
        return [] if carried is None else core.cursor_failures(carried, host_codes)

    def accounts(self) -> set[int]:
        if self.mode == INDEX:
            return {source.account for source in self.sources}
        return {account for account, _, _ in self.tail}

    def encode(self) -> str:
        if self.mode == INDEX:
            assert self.key is not None
            received, folder_id, uid = self.key
            at = (received - _EPOCH) // _MICROSECOND
            body: list[object] = [
                [source.json() for source in self.sources],
                [at, folder_id, uid],
                self.carried,
            ]
        else:
            tail = [[account, path, day.isoformat()] for account, path, day in self.tail]
            body = [tail, self.live, self.carried]
        text = json.dumps([CURSOR_VERSION, self.mode, *body], separators=(",", ":"))
        return base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode("ascii")


def _carried(failures: Sequence[PageFailure], host_codes: Mapping[str, str]) -> str | None:
    return core.encode_cursor([], failures, host_codes) if failures else None


def decode_cursor(text: str | None) -> IndexCursor | None:
    """The index's cursor ``text`` holds; None for none, or for another version (mailroom-core's
    to read, or refuse). ``invalid_argument`` for a version 3 that is not one."""
    if text is None:
        return None
    cleaned = text.strip().rstrip("=")
    try:
        raw = base64.urlsafe_b64decode(cleaned + "=" * (-len(cleaned) % 4))
        value = json.loads(raw.decode("utf-8"))
    except binascii.Error, ValueError:
        return None
    if not (isinstance(value, list) and value and value[0] == CURSOR_VERSION):
        return None
    try:
        return _cursor(value[1:])
    except TypeError, ValueError, KeyError, OverflowError:
        raise MailError(
            "invalid_argument", "Not a cursor; use the one from the previous page."
        ) from None


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _cursor(value: list[object]) -> IndexCursor:
    mode, *body = value
    if mode == INDEX and len(body) == 3:
        sources, key, carried = body
        if not isinstance(sources, list) or not isinstance(key, list) or len(key) != 3:
            raise TypeError
        at, folder_id, uid = (_integer(part) for part in key)
        return IndexCursor(
            INDEX,
            tuple(_source(source) for source in sources),
            (_EPOCH + at * _MICROSECOND, folder_id, uid),
            carried=_optional_text(carried),
        )
    if mode == TAIL and len(body) == 3:
        tail, live, carried = body
        if not isinstance(tail, list):
            raise TypeError
        return IndexCursor(
            TAIL,
            tail=tuple(_tail_entry(entry) for entry in tail),
            live=_optional_text(live),
            carried=_optional_text(carried),
        )
    raise ValueError


def _source(value: object) -> IndexSource:
    # A cursor given before rows named the folder they were listed under has no `listed`.
    if not isinstance(value, list) or len(value) not in (6, 7):
        raise TypeError
    account, folder, uid_validity, folder_id, labels, live, *more = value
    listed = more[0] if more else []
    if labels is not None and not isinstance(labels, list):
        raise TypeError
    if not isinstance(live, list) or not isinstance(listed, list):
        raise TypeError
    if not all(isinstance(pair, list) and len(pair) == 2 for pair in listed):
        raise TypeError
    return IndexSource(
        account=_integer(account),
        folder=_text(folder),
        folder_id=_integer(folder_id),
        uid_validity=_integer(uid_validity),
        labels=None if labels is None else tuple(_text(label) for label in labels),
        oldest=None,
        live=tuple(_text(path) for path in live),
        listed=tuple((_text(label), _text(path)) for label, path in listed),
    )


def _tail_entry(value: object) -> tuple[int, str, date]:
    if not isinstance(value, list) or len(value) != 3:
        raise TypeError
    account, path, day = value
    return _integer(account), _text(path), date.fromisoformat(_text(day))


def gmail_ids(db: Session, user_id: int, refs: Sequence[MessageRef]) -> dict[MessageRef, int]:
    """For each of ``refs`` that names a row of an indexed folder carrying Gmail's message id
    (All Mail), that id."""
    if not refs:
        return {}
    folder, message = MailIndexFolder, MailIndexMessage
    wanted = [(ref.account, ref.folder, ref.uid_validity, ref.uid) for ref in refs]
    query = (
        select(folder.account_id, folder.path, folder.uid_validity, message.uid, message.gm_msgid)
        .join(message, and_(message.folder_id == folder.id, message.user_id == folder.user_id))
        .where(
            folder.user_id == user_id,
            folder.enabled,
            message.gm_msgid.is_not(None),
            tuple_(folder.account_id, folder.path, folder.uid_validity, message.uid).in_(wanted),
        )
    )
    found: dict[MessageRef, int] = {}
    for account, path, validity, uid, gm_msgid in db.execute(query):
        if validity is not None and gm_msgid is not None:
            found[MessageRef(account, path, validity, uid)] = gm_msgid
    return found


def counted_folders(db: Session, user_id: int, account_id: int) -> dict[str, Folder]:
    """The account's readable folders that the index holds whole (no age limit), by path, with
    the index's counts standing for STATUS's: UIDNEXT is past the highest UID indexed when the
    app's own writes put rows beyond the one last synced, so a mark read from it reaches them."""
    highest = (
        select(func.max(MailIndexMessage.uid))
        .where(
            MailIndexMessage.folder_id == MailIndexFolder.id,
            MailIndexMessage.user_id == MailIndexFolder.user_id,
        )
        .scalar_subquery()
    )
    query = select(MailIndexFolder, highest).where(
        *_ready(user_id),
        MailIndexFolder.account_id == account_id,
        MailIndexFolder.oldest_indexed.is_(None),
    )
    counted: dict[str, Folder] = {}
    for row, top in db.execute(query):
        uid_next = max(row.uid_next or 1, (top or 0) + 1)
        counted[row.path] = core.Folder(
            path=row.path,
            delimiter=None,
            role=None,
            flags=(),
            messages=row.message_count,
            unseen=row.unread_count,
            uid_validity=row.uid_validity,
            uid_next=uid_next,
            highest_modseq=row.highest_modseq,
        )
    return counted
