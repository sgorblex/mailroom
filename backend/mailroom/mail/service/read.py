"""Reading a user's mail: folders, pages of messages (a list, a search), one message, its thread,
its HTML, its attachments, its source and its printable form, an account's quota and whether its
server keeps keywords."""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import dataclass, replace
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, cast

from mailroom.accounts.service import require_subkey
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail.connections import PASSWORD_MISSING, PASSWORD_MISSING_TEXT, PasswordMissing
from mailroom.mail.index import reads as index_reads
from mailroom.mail.index.reads import Conversation
from mailroom.mail.service._shared import (
    _accounts,
    _call,
    _index_read_failed,
    _message_ref,
    _missing_password,
    _run,
    _usable,
)
from mailroom_core import mail as core
from mailroom_core.mail import MESSAGES, MailError, Page, PageFailure, SearchQuery, Source

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import date

    from mailroom.accounts.service import Via
    from mailroom.context import AppContext
    from mailroom_core.config import AccountConfig
    from mailroom_core.mail import (
        Attachment,
        Criteria,
        Folder,
        MailboxContext,
        MailConnections,
        MailErrorCode,
        MessageRef,
        MessageSummary,
        MessageView,
        Quota,
        RawMessage,
    )
    from mailroom_core.render import Marker, SafeHtml

logger = logging.getLogger(__name__)

type AllAccounts = Literal["*"]
ALL_ACCOUNTS: Final[AllAccounts] = "*"
ALL_FOLDERS: Final = core.ALL_FOLDERS
INLINE_IMAGE_BYTES: Final = 5 * 1024 * 1024
"""The ``cid:`` images inlined into one HTML view, in total; past it an image keeps only its
alt text."""
SOURCES_SECONDS: Final = 12.0
"""How long an all-accounts search waits for an account's folders and, when the query needs them
(``has_attachment``, ``gmail_query``), its capabilities before it is a ``timeout`` failure (a
blackholed server costs two 10 s connects). The capabilities can log in, so such a query logs in
within this bound even when it reads one folder; mailroom-core's ``search_messages`` does the
same."""
KEYWORDS_SECONDS: Final = 5.0
"""How long the accounts listing waits for its accounts' keyword answers (``account_keywords``):
an account still asked then is listed as not known, so one slow server holds no listing up."""
ACCOUNT_SECONDS: Final = 25.0
"""When an all-accounts search cuts the accounts still reading their mailbox (``timeout``
failures), inside the pool's 30 s deadline, so the others still answer."""
HOST_CODES: Final = MappingProxyType({PASSWORD_MISSING: PASSWORD_MISSING_TEXT})
"""The app's own codes an account of an all-accounts search fails with that its cursor carries,
each with its fixed message."""


def folders(
    ctx: AppContext,
    user_id: int,
    account_id: int,
    *,
    counts: bool = True,
    every: bool = False,
    via: Via = "web",
) -> list[Folder]:
    """The account's selectable folders in mailroom-core's order (special-use first, then the rest
    by path), with their counts unless ``counts`` is off: a folder the index holds whole
    (ready, no age limit) has the index's counts, UIDVALIDITY and UIDNEXT
    (``counted_folders``); on Gmail, with All Mail held so, INBOX and each label's folder have
    the counts of All Mail's rows carrying its label (``label_counts``), without UIDVALIDITY or
    UIDNEXT (a mark read reads them then), or STATUS's when those cannot be read; each other one
    STATUS's (one command per folder). ``every``: the ``\\Noselect`` ones too (a tree's parents,
    without counts)."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    held = _index_counts(ctx, user_id, account_id) if counts else {}

    def read(connections: MailConnections) -> list[Folder]:
        if not held:
            return connections.folders(account_id, with_status=counts)
        gmail = connections.capabilities(account_id).gmail

        def live(folder: Folder) -> bool:
            return folder.path not in held and not (
                gmail and index_reads.gmail_label(folder) is not None
            )

        listed = connections.folders(account_id, with_status=live)
        labels = _label_counts(ctx, user_id, account_id, listed, held) if gmail else None
        if gmail and labels is None:
            listed = connections.folders(account_id, with_status=lambda f: f.path not in held)
        return [_counted(folder, held.get(folder.path), labels) for folder in listed]

    listed = _call(ctx, user_id, accounts, "mail_folders", via, account_id, read)
    return listed if every else [folder for folder in listed if folder.selectable]


def _counted(
    folder: Folder, held: Folder | None, labels: Mapping[str, tuple[int, int]] | None
) -> Folder:
    """``folder`` with the index's counts ``held``, if any; on Gmail (``labels``: All Mail's
    counts per label, None when they could not be read), a label's folder with its label's."""
    if not folder.selectable:
        return folder
    if held is None:
        label = None if labels is None else index_reads.gmail_label(folder)
        if labels is None or label is None:
            return folder
        messages, unseen = labels.get(label, (0, 0))
        return replace(folder, messages=messages, unseen=unseen)
    return replace(
        folder,
        messages=held.messages,
        unseen=held.unseen,
        uid_validity=held.uid_validity,
        uid_next=held.uid_next,
        highest_modseq=held.highest_modseq,
    )


def indexable_folders(
    ctx: AppContext,
    user_id: int,
    account_id: int,
    *,
    via: Via = "web",
    counted: Callable[[Folder], bool] | None = None,
) -> tuple[bool, list[Folder]]:
    """Whether the account's server is Gmail (``X-GM-EXT-1``), and its selectable folders with
    their counts (with ``counted``, those of the folders it holds true of only): what the mail
    index's folders follow."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)

    def read(connections: MailConnections) -> tuple[bool, list[Folder]]:
        gmail = connections.capabilities(account_id).gmail
        listed = connections.folders(account_id, with_status=counted or True)
        return gmail, [folder for folder in listed if folder.selectable]

    return _call(ctx, user_id, accounts, "mail_index_folders", via, account_id, read)


def folder_segments(folder: Folder) -> list[str]:
    """``folder``'s path split on its delimiter: how a rule set names a folder."""
    return folder.path.split(folder.delimiter) if folder.delimiter else [folder.path]


def list_messages(
    ctx: AppContext,
    user_id: int,
    account_id: int,
    folder: str = "INBOX",
    *,
    limit: int = 25,
    cursor: str | None = None,
    subfolders: bool = False,
    group: bool = False,
    via: Via = "web",
) -> Page:
    """One folder, newest first. A folder that cannot be read is refused with its error, not
    reported in the page. With ``subfolders``, the folder and its subfolders merged
    (mailroom-core's ``read_folders``): each one that cannot be read is a failure of the page.
    Read from the index when every folder is indexed and ready (``_from_index``), else live.
    ``group``: by conversation, when read from the index (a ``ConversationPage``)."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)

    def read(connections: MailConnections) -> Page:
        def sources() -> list[Source]:
            paths = core.read_folders(connections, account_id, folder, subfolders=subfolders)
            return [Source(account_id, path) for path in paths]

        indexed = _from_index(
            ctx,
            connections,
            user_id,
            [account_id],
            sources,
            None,
            limit=limit,
            cursor=cursor,
            group=group,
        )
        if indexed is not None:
            return indexed
        return core.list_messages(
            connections, account_id, folder, limit=limit, cursor=cursor, subfolders=subfolders
        )

    page = _call(ctx, user_id, accounts, "mail_listed", via, account_id, read)
    page = _with_password_failures(ctx, user_id, page)
    if not subfolders:
        _first_failure(page)
    return page


def search(
    ctx: AppContext,
    user_id: int,
    account: int | AllAccounts,
    folder: str,
    query: SearchQuery,
    *,
    limit: int = 25,
    cursor: str | None = None,
    subfolders: bool = False,
    group: bool = False,
    via: Via = "web",
) -> Page:
    """``query`` over one account or all (``ALL_ACCOUNTS``), in one folder (with
    ``subfolders``, its subfolders too; refused with ``ALL_FOLDERS``) or all
    (``ALL_FOLDERS``: not Junk or Trash; only the ``\\All`` folder where there is one). Over all
    accounts, one that cannot be searched is a failure of the page with no folder (an account
    without a usable password: code ``password_missing``; one too slow: ``timeout``) and the
    others still answer, however many there are; later pages report it again without asking it
    again (nor checking its password), until paging ends. Over one account and one folder, its
    error is refused, as for a list, unless it reads subfolders too. Without ``text`` and
    ``gmail_query``, read from the index when every folder is indexed and ready
    (``_from_index``), else live; ``group`` as for a list."""
    check_search(folder, query, subfolders=subfolders)
    accounts = _accounts(ctx, user_id)
    skipped: list[PageFailure] = []
    if account == ALL_ACCOUNTS:
        try:
            resumed = index_reads.decode_cursor(cursor)
            if resumed is not None:
                carried = resumed.failures(HOST_CODES)
            else:
                carried = [] if cursor is None else core.cursor_failures(cursor, HOST_CODES)
        except MailError as error:
            raise mail_refusal(error.code, error.message) from None
        failed_before = {f.account for f in carried if f.folder is None}
        wanted = accounts.ids()
        if wanted:
            require_subkey(ctx.subkey)
        usable = []
        for account_id in wanted:
            if account_id in failed_before:
                continue
            if _missing_password(ctx, user_id, account_id):
                skipped.append(_password_missing_failure(account_id, None))
            else:
                usable.append(account_id)
        if not usable:
            before = [f for f in carried if f.folder is None]
            return index_reads.page_of((), [] if group else None, None, [*before, *skipped])
        page = _call(
            ctx,
            user_id,
            accounts,
            "mail_searched",
            via,
            account,
            lambda connections: _search_accounts(
                connections,
                usable,
                folder,
                query,
                limit=limit,
                cursor=cursor,
                subfolders=subfolders,
                skipped=skipped,
                index=partial(
                    _from_index,
                    ctx,
                    connections,
                    user_id,
                    usable,
                    query=query,
                    limit=limit,
                    every_folder=folder == ALL_FOLDERS,
                    group=group,
                ),
            ),
            on=usable,
        )
        return _with_password_failures(ctx, user_id, page)
    _usable(ctx, user_id, accounts, account)
    one = account

    def read(connections: MailConnections) -> Page:
        def sources() -> list[Source]:
            paths, _ = core.search_sources(connections, one, folder, query, subfolders=subfolders)
            return [Source(one, path) for path in paths]

        indexed = _from_index(
            ctx,
            connections,
            user_id,
            [one],
            sources,
            query,
            limit=limit,
            cursor=cursor,
            every_folder=folder == ALL_FOLDERS,
            group=group,
        )
        if indexed is not None:
            return indexed
        return core.search_messages(
            connections, [one], folder, query, limit=limit, cursor=cursor, subfolders=subfolders
        )

    page = _call(ctx, user_id, accounts, "mail_searched", via, account, read)
    page = _with_password_failures(ctx, user_id, page)
    if folder != ALL_FOLDERS and not subfolders:
        _first_failure(page)
    return page


def check_search(folder: str, query: SearchQuery, *, subfolders: bool) -> None:
    """Refuses, before anything connects, a term with a line break and ``subfolders`` with
    ``ALL_FOLDERS``."""
    try:
        core.search_arguments(query.criteria())
        core.check_subfolders(folder, subfolders=subfolders)
    except MailError as error:
        raise mail_refusal(error.code, error.message) from None


def _password_missing_failure(account_id: int, folder: str | None) -> PageFailure:
    code = cast("MailErrorCode", PASSWORD_MISSING)  # the app's own code
    return PageFailure(account_id, folder, code, PASSWORD_MISSING_TEXT)


def _with_password_failures(ctx: AppContext, user_id: int, page: Page) -> Page:
    """``page`` with each ``server_error`` of an account whose password no longer opens as
    ``password_missing``: mailroom-core reports any failure to open a connection, the pool's
    ``PasswordMissing`` included, as a server error of the source."""
    if not page.failures:
        return page
    return replace(page, failures=password_checked(ctx, user_id, page.failures))


def password_checked(
    ctx: AppContext, user_id: int, failures: Sequence[PageFailure]
) -> tuple[PageFailure, ...]:
    """``failures`` with each ``server_error`` of an account whose password no longer opens as
    ``password_missing`` (``_with_password_failures``)."""
    missing: dict[int, bool] = {}

    def checked(failure: PageFailure) -> PageFailure:
        if failure.code != "server_error":
            return failure
        if failure.account not in missing:
            missing[failure.account] = _missing_password(ctx, user_id, failure.account)
        if missing[failure.account]:
            return _password_missing_failure(failure.account, failure.folder)
        return failure

    return tuple(checked(f) for f in failures)


def _first_failure(page: Page) -> None:
    """Refuse with the page's first failure, if any: a single source answers its error."""
    if page.failures:
        failure = page.failures[0]
        raise mail_refusal(failure.code, failure.message)


def _sources(
    connections: MailConnections, account: int, folder: str, query: SearchQuery, subfolders: bool
) -> tuple[list[Source], Criteria]:
    """What mailroom-core's ``search_messages`` reads in ``account``, and with which criteria
    (its ``search_sources``)."""
    paths, criteria = core.search_sources(
        connections, account, folder, query, subfolders=subfolders
    )
    return [Source(account, path) for path in paths], criteria


class _Reading:
    """``connections`` for ``read_page``, counting the mailboxes each account has open for it:
    the accounts that still read when the search's time is up, and only those, are cut, and
    what fails them then is a ``timeout``."""

    def __init__(self, connections: MailConnections) -> None:
        self._connections = connections
        self._lock = threading.Lock()
        self._open: Counter[int] = Counter()
        self._cut: set[int] = set()

    def with_mailbox[T](
        self,
        account_id: int,
        path: str,
        read_only: bool,
        fn: Callable[[MailboxContext], T],
    ) -> T:
        with self._lock:
            self._open[account_id] += 1
        try:
            return self._connections.with_mailbox(account_id, path, read_only, fn)
        except MailError as error:
            with self._lock:
                cut = account_id in self._cut
            if cut and error.code == "unreachable":
                raise MailError("timeout") from None
            raise
        finally:
            with self._lock:
                self._open[account_id] -= 1

    def __getattr__(self, name: str) -> object:
        return getattr(self._connections, name)

    def cut_reading(self) -> None:
        """Cut the accounts still reading a mailbox."""
        with self._lock:
            reading = sorted(account for account, count in self._open.items() if count > 0)
            self._cut.update(reading)
        for account in reading:
            self._connections.interrupt(account)


def _search_accounts(
    connections: MailConnections,
    accounts: Sequence[int],
    folder: str,
    query: SearchQuery,
    *,
    limit: int,
    cursor: str | None,
    subfolders: bool = False,
    skipped: Sequence[PageFailure] = (),
    index: Callable[..., Page | None] | None = None,
) -> Page:
    """mailroom-core's ``search_messages`` over several accounts, with a deadline per account:
    their folders are found side by side, ``SOURCES_SECONDS`` at most, and the accounts still
    reading a mailbox at ``ACCOUNT_SECONDS`` are cut (one already done is left alone, so another
    call on it goes on). Each such account is a ``timeout`` failure; an account that fails is a
    failure too, whatever the number of accounts. An account that failed before its folders were
    found, on an earlier page, is not asked again: the cursor carries it, and each page reports
    it. ``skipped`` are the accounts the caller did not pass, failed with the app's own codes
    (``HOST_CODES``): the cursor carries them likewise. ``index``: ``_from_index`` for these
    accounts and query, given the sources, the cursor, the failures and the live read: a page
    from the index when it can answer for every folder found; a cursor of the index's goes on
    there, without finding the folders again."""
    started = time.monotonic()

    def read_live(
        sources: Sequence[Source],
        criteria: Mapping[Source, Criteria] | Mapping[int, Criteria],
        *,
        limit: int,
        cursor: str | None,
        account_failures: Sequence[PageFailure],
    ) -> Page:
        return _cut_read(
            connections,
            started,
            sources,
            criteria,
            limit=limit,
            cursor=cursor,
            account_failures=account_failures,
        )

    if index is not None and index_reads.decode_cursor(cursor) is not None:
        indexed = index(list, cursor=cursor, failures=skipped, read_live=read_live)
        if indexed is not None:
            return indexed
    failures = list(skipped)
    sources: list[Source] = []
    criteria: dict[int, Criteria] = {}
    failed_before = core.failed_accounts(cursor, HOST_CODES)
    asked = [account for account in accounts if account not in failed_before]
    finder = ThreadPoolExecutor(
        max_workers=max(1, min(len(asked), 8)), thread_name_prefix="mail-src"
    )
    found = {
        account: finder.submit(_sources, connections, account, folder, query, subfolders)
        for account in asked
    }
    wait(found.values(), timeout=SOURCES_SECONDS)
    finder.shutdown(wait=False, cancel_futures=True)
    for account, future in found.items():
        if not future.done():
            connections.interrupt(account)
            failures.append(PageFailure(account, None, "timeout", MESSAGES["timeout"]))
            continue
        try:
            read, criteria[account] = future.result()
            sources.extend(read)
        except MailError as error:
            failures.append(PageFailure.of(account, None, error))
        except Exception:
            logger.exception(
                "mail_search_sources_failed",
                extra={"event": "mail_search_sources_failed", "account_id": account},
            )
            failures.append(PageFailure(account, None, "server_error", MESSAGES["server_error"]))
    if index is not None and cursor is None:
        indexed = index(lambda: sources, cursor=None, failures=failures, read_live=read_live)
        if indexed is not None:
            return indexed
    return read_live(sources, criteria, limit=limit, cursor=cursor, account_failures=failures)


def _cut_read(
    connections: MailConnections,
    started: float,
    sources: Sequence[Source],
    criteria: Mapping[Source, Criteria] | Mapping[int, Criteria],
    *,
    limit: int,
    cursor: str | None,
    account_failures: Sequence[PageFailure],
) -> Page:
    """mailroom-core's ``read_page``, cutting the accounts still reading a mailbox
    ``ACCOUNT_SECONDS`` after ``started`` (``_Reading``)."""
    reading = _Reading(connections)
    timer = threading.Timer(
        max(0.0, started + ACCOUNT_SECONDS - time.monotonic()), reading.cut_reading
    )
    timer.daemon = True
    timer.start()
    try:
        return core.read_page(
            cast("MailConnections", reading),
            sources,
            criteria,
            limit=limit,
            cursor=cursor,
            account_failures=account_failures,
            host_codes=HOST_CODES,
        )
    finally:
        timer.cancel()


type _LiveRead = Callable[..., Page]
"""``read_page`` on the call's connections with ``HOST_CODES``: sources and criteria, then
``limit``, ``cursor`` and ``account_failures`` by keyword."""


def _from_index(
    ctx: AppContext,
    connections: MailConnections,
    user_id: int,
    accounts: Sequence[int],
    sources: Callable[[], list[Source]],
    query: SearchQuery | None,
    *,
    limit: int,
    cursor: str | None,
    failures: Sequence[PageFailure] = (),
    read_live: _LiveRead | None = None,
    every_folder: bool = False,
    group: bool = False,
) -> Page | None:
    """A list (``query`` None) or a search of the ``sources`` of ``accounts`` read from the mail
    index (``mailroom.mail.index.reads``); None to read it live: the query needs a body's text or
    Gmail's syntax, ``cursor`` is a live one, or a source is not indexed and ready. A cursor of
    the index's goes on in the index, or in the live read of what its age limit leaves out
    (``_tail_page``); one whose folder is no longer readable, or changed, is
    ``stale_reference``. ``failures``: accounts failed before a folder was chosen, carried on as
    a live search carries them. ``every_folder``: the sources are a read of all folders
    (``index_reads.index_sources``). ``group``: a page of conversations
    (``index_reads.ConversationPage``), what is read live each message alone."""
    if not index_reads.readable(query):
        return None
    resumed = index_reads.decode_cursor(cursor)
    if cursor is not None and resumed is None:
        return None
    live: _LiveRead = read_live or partial(core.read_page, connections, host_codes=HOST_CODES)
    if resumed is not None and resumed.mode == index_reads.TAIL:
        tail = [entry for entry in resumed.tail if entry[0] in accounts]
        before = [] if resumed.live is not None else resumed.failures(HOST_CODES)
        return _tail_page(
            connections,
            live,
            tail,
            query,
            (),
            limit,
            cursor=resumed.live,
            failures=[*before, *failures],
            conversations=() if group else None,
        )
    if resumed is None:
        if not _indexed(ctx, user_id, accounts):
            return None
        wanted = sources()
        with ctx.session_maker() as db:
            picked = index_reads.index_sources(
                db,
                user_id,
                wanted,
                gmail=lambda account: connections.capabilities(account).gmail,
                listing=connections.folders,
                every_folder=every_folder,
            )
            if picked is None:
                return None
            rows = index_reads.read_page_rows(
                db,
                user_id,
                picked,
                query,
                limit=limit + 1,
                group=group,
                listing=connections.folders,
            )
        carried = list(failures)
    else:
        with ctx.session_maker() as db:
            picked = index_reads.resumed_sources(db, user_id, resumed, accounts)
            rows = index_reads.read_page_rows(
                db,
                user_id,
                picked,
                query,
                limit=limit + 1,
                after=resumed.key,
                group=group,
                listing=connections.folders,
            )
        carried = [*resumed.failures(HOST_CODES), *failures]
    messages = tuple(summary for summary, _, _ in rows[:limit])
    conversations = _conversations(rows[:limit]) if group else None
    if len(rows) > limit:
        following = index_reads.IndexCursor.index(picked, rows[limit - 1][1], carried, HOST_CODES)
        return index_reads.page_of(messages, conversations, following.encode(), carried)
    tail = index_reads.tail_of(picked, query)
    if not tail:
        return index_reads.page_of(messages, conversations, None, carried)
    remaining = limit - len(messages)
    return _tail_page(
        connections,
        live,
        tail,
        query,
        messages,
        remaining,
        cursor=None,
        failures=carried,
        conversations=conversations,
    )


def _conversations(rows: Sequence[index_reads.Row]) -> tuple[Conversation, ...]:
    return tuple(conversation for _, _, conversation in rows if conversation is not None)


def _indexed(ctx: AppContext, user_id: int, accounts: Sequence[int]) -> bool:
    """Whether the index holds a readable folder of ``accounts``; False when it cannot be read
    (``_index_read_failed``), so that a read goes live."""
    try:
        with ctx.session_maker() as db:
            return bool(index_reads.ready_rows(db, user_id, accounts))
    except Exception as exc:
        _index_read_failed(user_id, exc)
        return False


def _index_counts(ctx: AppContext, user_id: int, account_id: int) -> dict[str, Folder]:
    """The folders whose counts the index gives (``counted_folders``); none when it cannot be
    read (``_index_read_failed``), so that STATUS gives them all."""
    try:
        with ctx.session_maker() as db:
            return index_reads.counted_folders(db, user_id, account_id)
    except Exception as exc:
        _index_read_failed(user_id, exc)
        return {}


def _label_counts(
    ctx: AppContext,
    user_id: int,
    account_id: int,
    listed: Sequence[Folder],
    held: Mapping[str, Folder],
) -> dict[str, tuple[int, int]] | None:
    """A Gmail account's counts per label from All Mail's rows (``label_counts``), when the
    index holds All Mail whole; None when it does not or cannot be read
    (``_index_read_failed``)."""
    every = next((f for f in listed if f.role == "all" and f.path in held), None)
    if every is None:
        return None
    try:
        with ctx.session_maker() as db:
            return index_reads.label_counts(db, user_id, account_id, every.path)
    except Exception as exc:
        _index_read_failed(user_id, exc)
        return None


def _tail_page(
    connections: MailConnections,
    live: _LiveRead,
    tail: Sequence[tuple[int, str, date]],
    query: SearchQuery | None,
    messages: Sequence[MessageSummary],
    remaining: int,
    *,
    cursor: str | None,
    failures: Sequence[PageFailure],
    conversations: Sequence[Conversation] | None = None,
) -> Page:
    """``messages`` from the index, then up to ``remaining`` more read live from the ``tail``:
    the folders whose mail before their age-limit day the index leaves out (``BEFORE`` that
    day), from ``cursor``, the live read's; ``failures`` as ``read_page``'s
    ``account_failures``. With ``conversations`` (those of ``messages``), a page of
    conversations, each message read live alone."""
    if remaining <= 0:
        start = index_reads.IndexCursor.tail_from(tail, None, failures, HOST_CODES)
        return index_reads.page_of(messages, conversations, start.encode(), failures)
    criteria: dict[Source, Criteria] = {}
    for account, path, day in tail:
        own = _live_criteria(connections, account, query)
        criteria[Source(account, path)] = [*own, "BEFORE", day]
    page = live(list(criteria), criteria, limit=remaining, cursor=cursor, account_failures=failures)
    after = page.next_cursor
    following = index_reads.IndexCursor.tail_from(tail, after, (), HOST_CODES)
    grouped = (
        None
        if conversations is None
        else [*conversations, *(Conversation.single(summary) for summary in page.messages)]
    )
    return index_reads.page_of(
        [*messages, *page.messages],
        grouped,
        None if after is None else following.encode(),
        page.failures,
    )


def _live_criteria(
    connections: MailConnections, account: int, query: SearchQuery | None
) -> list[object]:
    """``query``'s criteria as a live search of ``account`` sends them; none for a list."""
    if query is None:
        return []
    gmail = query.has_attachment is True and connections.capabilities(account).gmail
    criteria = query.criteria(gmail=gmail)
    return [] if criteria == ["ALL"] else criteria


def read_headers(
    ctx: AppContext, user_id: int, ref: str, *, event: str, via: Via = "web"
) -> tuple[MessageView, AccountConfig]:
    """The message ``ref`` names for its headers (a one-character body window), and its
    account's config, for a call that acts on what the headers say; logged as ``event``."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    account = accounts.config(message.account)
    view = _call(
        ctx,
        user_id,
        accounts,
        event,
        via,
        message.account,
        lambda connections: core.read_message(connections, message, body_chars=1, account=account),
    )
    return view, account


def read_message(
    ctx: AppContext,
    user_id: int,
    ref: str,
    *,
    offset: int = 0,
    include_quoted: bool = False,
    raw_headers: bool = False,
    remote_check: bool = False,
    marker: Marker | None = None,
    via: Via = "web",
) -> MessageView:
    """One message, its text body windowed by the user's ``body_chars`` from ``offset``, checked
    against its account's identities and authentication server; ``raw_headers``,
    ``remote_check`` and ``marker`` (by default none where quotes were left out) as for
    mailroom-core's ``read_message``."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    body_chars = accounts.limits.body_chars
    account = accounts.config(message.account)
    return _call(
        ctx,
        user_id,
        accounts,
        "mail_read",
        via,
        message.account,
        lambda connections: core.read_message(
            connections,
            message,
            offset=offset,
            include_quoted=include_quoted,
            body_chars=body_chars,
            raw_headers=raw_headers,
            remote_check=remote_check,
            marker=marker,
            account=account,
        ),
    )


def thread(ctx: AppContext, user_id: int, ref: str, *, limit: int = 30, via: Via = "web") -> Page:
    """The conversation of the message ``ref`` names, in its account."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    return _call(
        ctx,
        user_id,
        accounts,
        "mail_thread_read",
        via,
        message.account,
        lambda connections: core.thread_page(connections, message, limit=limit),
    )


def thread_key(
    ctx: AppContext, user_id: int, ref: str, *, via: Via = "web"
) -> tuple[MessageRef, core.ThreadKey]:
    """What ties the message ``ref`` names to its conversation (mailroom-core's ``thread_key``),
    with the ref parsed; reading it changes nothing."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    key = _call(
        ctx,
        user_id,
        accounts,
        "mail_thread_key_read",
        via,
        message.account,
        lambda connections: core.thread_key(connections, message),
    )
    return message, key


QUOTA_SECONDS: Final = 600.0
"""How long an account's quota is answered from ``Quotas`` before the server is asked again."""


@dataclass(frozen=True, slots=True)
class KeptQuota:
    expires: float
    quota: Quota | None


class Quotas:
    """The accounts' quotas as last read, each for ``QUOTA_SECONDS``, keyed by the account and
    the revision of its user's accounts, so that a changed account is read afresh. Failures are
    not kept."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._kept: dict[tuple[int, int], KeptQuota] = {}

    def get(self, account_id: int, revision: int) -> KeptQuota | None:
        """The kept quota; None when there is none still fresh."""
        with self._lock:
            kept = self._kept.get((account_id, revision))
            if kept is None or kept.expires <= self._clock():
                return None
            return kept

    def put(self, account_id: int, revision: int, quota: Quota | None) -> None:
        now = self._clock()
        with self._lock:
            self._kept = {key: kept for key, kept in self._kept.items() if kept.expires > now}
            self._kept[account_id, revision] = KeptQuota(now + QUOTA_SECONDS, quota)


def quota(
    ctx: AppContext, user_id: int, account_id: int, quotas: Quotas, *, via: Via = "web"
) -> Quota | None:
    """The quota the account's INBOX falls under, None when the server keeps none (no QUOTA);
    from ``quotas`` while it is fresh."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    kept = quotas.get(account_id, accounts.revision)
    if kept is not None:
        return kept.quota
    found = _call(
        ctx,
        user_id,
        accounts,
        "mail_quota",
        via,
        account_id,
        lambda connections: connections.quota(account_id),
    )
    quotas.put(account_id, accounts.revision, found)
    return found


def keywords(ctx: AppContext, user_id: int, account_id: int, *, via: Via = "web") -> bool:
    """Whether the account's server keeps keywords such as To sort: mailroom-core's
    ``MailConnections.keywords`` (a read-write SELECT of INBOX, asked once while the user's
    connections last; never on Gmail)."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    return _call(
        ctx,
        user_id,
        accounts,
        "mail_keywords",
        via,
        account_id,
        lambda connections: connections.keywords(account_id),
    )


KEYWORD_FAILURE_SECONDS: Final = 300.0
"""How long ``account_keywords`` leaves alone an account whose server refused the login or could
not be reached (``KeywordFailures``)."""
_KEPT_KEYWORD_FAILURES: Final = frozenset({"auth_failed", "unreachable"})


class KeywordFailures:
    """The accounts whose keywords could not be asked because their server refused the login or
    could not be reached, each for ``KEYWORD_FAILURE_SECONDS``, keyed by the account and the
    revision of its user's accounts: listings do not log in to them again meanwhile (a wrong
    password tried on each could get the account locked out), and a changed account is asked
    afresh."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._until: dict[tuple[int, int], float] = {}

    def failed(self, account_id: int, revision: int) -> bool:
        """Whether the account failed lately, under this revision."""
        with self._lock:
            return self._until.get((account_id, revision), 0.0) > self._clock()

    def put(self, account_id: int, revision: int) -> None:
        now = self._clock()
        with self._lock:
            self._until = {key: until for key, until in self._until.items() if until > now}
            self._until[account_id, revision] = now + KEYWORD_FAILURE_SECONDS


def account_keywords(ctx: AppContext, user_id: int) -> dict[int, bool | None]:
    """``keywords`` for each of the user's accounts, asked side by side, ``KEYWORDS_SECONDS`` at
    most: None for one that cannot log in (no secrets key, no password that opens), that failed
    (lately: ``ctx.keyword_failures``) or that has not answered by then, whose connection is cut.
    Never refuses."""
    found: dict[int, bool | None] = dict.fromkeys(account_ids(ctx, user_id))
    if ctx.subkey is None:
        return found
    accounts = _accounts(ctx, user_id)
    failures = ctx.keyword_failures
    asked = [
        account
        for account in found
        if not failures.failed(account, accounts.revision)
        and not _missing_password(ctx, user_id, account)
    ]
    if not asked:
        return found

    def ask(connections: MailConnections) -> dict[int, bool]:
        finder = ThreadPoolExecutor(max_workers=min(len(asked), 8), thread_name_prefix="mail-kw")
        answers = {account: finder.submit(connections.keywords, account) for account in asked}
        wait(answers.values(), timeout=KEYWORDS_SECONDS)
        finder.shutdown(wait=False, cancel_futures=True)
        known: dict[int, bool] = {}
        for account, answer in answers.items():
            if not answer.done():
                connections.interrupt(account)
                continue
            if answer.cancelled():
                continue
            error = answer.exception()
            if error is None:
                known[account] = answer.result()
            elif isinstance(error, MailError):
                if error.code in _KEPT_KEYWORD_FAILURES:
                    failures.put(account, accounts.revision)
            elif not isinstance(error, PasswordMissing):
                logger.error(
                    "mail_keywords_failed",
                    exc_info=error,
                    extra={"event": "mail_keywords_failed", "account_id": account},
                )
        return known

    with suppress(Refusal):
        found.update(_run(ctx, user_id, accounts, "mail_keywords", (), ask))
    return found


def account_ids(ctx: AppContext, user_id: int) -> Sequence[int]:
    """The user's account ids, in their order."""
    return _accounts(ctx, user_id).ids()


def account_by_name(ctx: AppContext, user_id: int, written: str) -> int | AllAccounts:
    """The account a search query's ``account:`` names: ``*``, an id (its digits; whether it is
    the user's is the search's to check), or the one account whose name, else login, it is,
    ignoring case. ``not_found`` when none is, ``invalid_argument`` when several are."""
    if written == ALL_ACCOUNTS:
        return ALL_ACCOUNTS
    if written.isascii() and written.isdigit():
        return int(written)
    wanted = written.casefold()
    configs = _accounts(ctx, user_id).loaded.settings.account
    found = [a.id for a in configs if (a.name or "").casefold() == wanted]
    found = found or [a.id for a in configs if a.login.casefold() == wanted]
    if len(found) == 1:
        return found[0]
    if not found:
        raise Refusal("not_found", "not_found", "No account of yours has that name.", coded=True)
    message = "More than one account has that name: name it by its id."
    raise Refusal("invalid", "invalid_argument", message)


def attachment(
    ctx: AppContext, user_id: int, ref: str, part: str, *, via: Via = "web"
) -> Attachment:
    """An attachment, an inline part or the calendar part, at most the user's
    ``attachment_max_mb`` (``too_large`` beyond); ``not_found`` for any other part."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    max_bytes = int(accounts.limits.attachment_max_mb * 1024 * 1024)
    return _call(
        ctx,
        user_id,
        accounts,
        "mail_attachment_read",
        via,
        message.account,
        lambda connections: core.read_attachment(connections, message, part, max_bytes=max_bytes),
    )


def message_source(ctx: AppContext, user_id: int, ref: str, *, via: Via = "web") -> RawMessage:
    """The message's whole source, at most the user's ``attachment_max_mb`` (``too_large``
    beyond)."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    max_bytes = int(accounts.limits.attachment_max_mb * 1024 * 1024)
    return _call(
        ctx,
        user_id,
        accounts,
        "mail_source_read",
        via,
        message.account,
        lambda connections: core.read_source(connections, message, max_bytes=max_bytes),
    )


@dataclass(frozen=True, slots=True)
class Printable:
    """A message as its printable page shows it, with the zone its date is written in."""

    view: MessageView
    """Its whole text body, quotes included."""
    html: SafeHtml | None
    """Its HTML body sanitised, without remote images; None without one."""
    time_zone: str


def printable(ctx: AppContext, user_id: int, ref: str, *, via: Via = "web") -> Printable:
    """The message's headers and its whole body for printing: the HTML body sanitised as
    ``message_html`` does without ``remote``, else the text body."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    account = accounts.config(message.account)

    def work(connections: MailConnections) -> tuple[MessageView, SafeHtml | None]:
        view = core.read_message(
            connections,
            message,
            include_quoted=True,
            body_chars=core.MAX_BODY_BYTES,
            marker=None,
            account=account,
        )
        html = (
            core.html_view(connections, message, remote=False, max_inline_bytes=INLINE_IMAGE_BYTES)
            if view.has_html
            else None
        )
        return view, html

    view, html = _call(ctx, user_id, accounts, "mail_print_read", via, message.account, work)
    return Printable(view=view, html=html, time_zone=ctx.time_zone(user_id))


def message_html(
    ctx: AppContext, user_id: int, ref: str, *, remote: bool = False, via: Via = "web"
) -> SafeHtml:
    """The message's HTML body sanitised (mailroom-core's ``safe_html``), its ``cid:`` images
    inlined up to ``INLINE_IMAGE_BYTES``, remote images kept only with ``remote``;
    ``not_found`` without an HTML body."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    return _call(
        ctx,
        user_id,
        accounts,
        "mail_html_read",
        via,
        message.account,
        lambda connections: core.html_view(
            connections, message, remote=remote, max_inline_bytes=INLINE_IMAGE_BYTES
        ),
    )
