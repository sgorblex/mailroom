"""A user's mail as every front end reads and organizes it (the REST routes, the MCP tools):
folders, pages of messages, one message, its HTML and its attachments, over mailroom-core; and
moves, archiving, trashing and flag changes of batches of refs. Reading changes nothing, not even
``\\Seen``; only the organize calls write, and nothing ever deletes or expunges mail beyond what a
move takes away.

Only the user's own accounts: an account id, or the account a ref names, that is not theirs is
``not_found`` exactly as one that does not exist. A mailbox failure is a ``Refusal`` of kind
``mail`` carrying mailroom-core's code and fixed message (never the server's words); a call still
waiting on the server after ``MailPool.deadline_seconds`` is ``timeout``. Every function blocks:
an async caller runs it in a worker thread. The log names the call, the account and the front end
(``via``), never a subject, an address, a folder or a body; an organize call's adds only counts
and codes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from mailroom_core import mail as core
from mailroom_core.mail import MESSAGES, MailError, Page, PageFailure, SearchQuery, Source

from mailroom.accounts.service import account_not_found, require_subkey
from mailroom.accounts.store import account_password, load_accounts
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail.pool import DeadlineExceeded, PasswordMissing

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mailroom_core.config import Limits
    from mailroom_core.mail import (
        Attachment,
        Criteria,
        Folder,
        MailboxContext,
        MailConnections,
        MailErrorCode,
        MessageRef,
        MessageView,
        OrganizeResult,
    )
    from mailroom_core.render import Marker
    from mailroom_core.render.safe_html import SafeHtml

    from mailroom.accounts.service import Via
    from mailroom.accounts.store import UserAccounts
    from mailroom.context import AppContext

logger = logging.getLogger(__name__)

MAX_FOLDER: Final = 1000
MAX_CURSOR: Final = 65536
"""A cursor holds a position per folder still being read: a search over every folder of
several accounts makes long ones (mailroom-core checks their structure)."""
MAX_TERM: Final = 500
"""The bounds every front end puts on what it takes (a folder path, a cursor, a search term)."""
ALL_ACCOUNTS: Final = "*"
ALL_FOLDERS: Final = core.ALL_FOLDERS
MAX_REFS: Final = core.MAX_REFS
"""The most refs one organize call takes."""
MAX_REF_LENGTH: Final = 4096
"""The longest ref a front end takes (a ref holds its folder's path)."""
INLINE_IMAGE_BYTES: Final = 5 * 1024 * 1024
"""The ``cid:`` images inlined into one HTML view, in total; past it an image keeps only its
alt text."""
PASSWORD_MISSING: Final = "password_missing"
_PASSWORD_MISSING_TEXT: Final = "No stored password opens for this account: enter it again."
_FAILED_TEXT: Final = "Reading the mail failed unexpectedly."
SOURCES_SECONDS: Final = 12.0
"""How long an all-accounts search waits for an account's folders and, when the query needs them
(``has_attachment``, ``gmail_query``), its capabilities before it is a ``timeout`` failure (a
blackholed server costs two 10 s connects). The capabilities can log in, so such a query logs in
within this bound even when it reads one folder; mailroom-core's ``search_messages`` does the
same."""
ACCOUNT_SECONDS: Final = 25.0
"""When an all-accounts search cuts the accounts still reading their mailbox (``timeout``
failures), inside the pool's 30 s deadline, so the others still answer."""


def unknown_account() -> Refusal:
    """An account id that is not the user's (theirs or nobody's: the same answer), as the
    accounts service words it, its code kept for the mail client."""
    return account_not_found(coded=True)


def unknown_message() -> Refusal:
    """A ref naming an account that is not the user's: answered as a missing message."""
    return Refusal("not_found", "not_found", MESSAGES["not_found"], coded=True)


@dataclass(frozen=True, slots=True)
class _Accounts:
    """The user's accounts as one call found them."""

    loaded: UserAccounts

    @property
    def revision(self) -> int:
        return self.loaded.revision

    @property
    def limits(self) -> Limits:
        return self.loaded.settings.limits

    def ids(self) -> list[str]:
        return [account.id for account in self.loaded.settings.account]

    def has(self, account_id: str) -> bool:
        return account_id in self.ids()


def _accounts(ctx: AppContext, user_id: int) -> _Accounts:
    return _Accounts(load_accounts(ctx.store(user_id)))


def _missing_password(ctx: AppContext, user_id: int, account_id: str) -> bool:
    return account_password(ctx.store(user_id), ctx.subkey, account_id) is None


def _password_missing_failure(account_id: str, folder: str | None) -> PageFailure:
    code = cast("MailErrorCode", PASSWORD_MISSING)  # the app's own code
    return PageFailure(account_id, folder, code, _PASSWORD_MISSING_TEXT)


def _usable(ctx: AppContext, user_id: int, accounts: _Accounts, account_id: str) -> None:
    """Refuse an account that is not the user's, or that cannot log in: no secrets key
    (``secrets_key_missing``), no password that opens (``password_missing``)."""
    if not accounts.has(account_id):
        raise unknown_account()
    require_subkey(ctx.subkey)
    if _missing_password(ctx, user_id, account_id):
        raise mail_refusal(PASSWORD_MISSING, _PASSWORD_MISSING_TEXT)


def _message_ref(ctx: AppContext, user_id: int, accounts: _Accounts, ref: str) -> MessageRef:
    try:
        message = core.parse_ref(ref)
    except MailError as error:
        raise mail_refusal(error.code, error.message) from None
    if not accounts.has(message.account):
        raise unknown_message()
    _usable(ctx, user_id, accounts, message.account)
    return message


def _run[T](
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    event: str,
    used: Sequence[str],
    work: Callable[[MailConnections], T],
    *,
    on_deadline: Callable[[], None] | None = None,
) -> T:
    """``work`` on the user's connections, on the accounts ``used``, under the pool's gates and
    deadline (``on_deadline`` called when it passes); every failure a ``Refusal``."""
    try:
        with ctx.mail_pool.lease(ctx, user_id, accounts.revision) as connections:
            return ctx.mail_pool.call(
                connections, user_id, used, lambda: work(connections), on_deadline=on_deadline
            )
    except MailError as error:
        raise mail_refusal(error.code, error.message) from None
    except DeadlineExceeded:
        raise mail_refusal("timeout", MESSAGES["timeout"]) from None
    except PasswordMissing:
        raise mail_refusal(PASSWORD_MISSING, _PASSWORD_MISSING_TEXT) from None
    except Refusal:
        raise
    except Exception:
        logger.exception("mail_call_failed", extra={"event": "mail_call_failed", "call": event})
        raise Refusal("failed", "failed", _FAILED_TEXT) from None


def _call[T](
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    event: str,
    via: Via,
    account_id: str,
    work: Callable[[MailConnections], T],
    *,
    on: Sequence[str] | None = None,
) -> T:
    """``work`` as ``_run`` does it, logged. ``on``: the accounts it uses, when not just
    ``account_id``."""
    started = time.monotonic()
    outcome = "ok"
    try:
        used = [account_id] if on is None else list(on)
        return _run(ctx, user_id, accounts, event, used, work)
    except Refusal as refusal:
        outcome = refusal.code
        raise
    finally:
        logger.info(
            event,
            extra={
                "event": event,
                "user_id": user_id,
                "via": via,
                "account_id": account_id,
                "outcome": outcome,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )


def _with_password_failures(ctx: AppContext, user_id: int, page: Page) -> Page:
    """``page`` with each ``server_error`` of an account whose password no longer opens as
    ``password_missing``: mailroom-core reports any failure to open a connection, the pool's
    ``PasswordMissing`` included, as a server error of the source."""
    missing: dict[str, bool] = {}

    def checked(failure: PageFailure) -> PageFailure:
        if failure.code != "server_error":
            return failure
        if failure.account not in missing:
            missing[failure.account] = _missing_password(ctx, user_id, failure.account)
        if missing[failure.account]:
            return _password_missing_failure(failure.account, failure.folder)
        return failure

    if not page.failures:
        return page
    return Page(page.messages, page.next_cursor, tuple(checked(f) for f in page.failures))


def _first_failure(page: Page) -> None:
    """Refuse with the page's first failure, if any: a single source answers its error."""
    if page.failures:
        failure = page.failures[0]
        raise mail_refusal(failure.code, failure.message)


def folders(
    ctx: AppContext, user_id: int, account_id: str, *, counts: bool = True, via: Via = "web"
) -> list[Folder]:
    """The account's selectable folders in mailroom-core's order (special-use first, then the rest
    by path), with their counts (STATUS: one command per folder) unless ``counts`` is off."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    listed = _call(
        ctx,
        user_id,
        accounts,
        "mail_folders",
        via,
        account_id,
        lambda connections: connections.folders(account_id, with_status=counts),
    )
    return [folder for folder in listed if folder.selectable]


def list_messages(
    ctx: AppContext,
    user_id: int,
    account_id: str,
    folder: str = "INBOX",
    *,
    limit: int = 25,
    cursor: str | None = None,
    via: Via = "web",
) -> Page:
    """One folder, newest first. A folder that cannot be read is refused with its error, not
    reported in the page."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    page = _call(
        ctx,
        user_id,
        accounts,
        "mail_listed",
        via,
        account_id,
        lambda connections: core.list_messages(
            connections, account_id, folder, limit=limit, cursor=cursor
        ),
    )
    page = _with_password_failures(ctx, user_id, page)
    _first_failure(page)
    return page


def search(
    ctx: AppContext,
    user_id: int,
    account: str,
    folder: str,
    query: SearchQuery,
    *,
    limit: int = 25,
    cursor: str | None = None,
    via: Via = "web",
) -> Page:
    """``query`` over one account or all (``ALL_ACCOUNTS``), in one folder or all
    (``ALL_FOLDERS``: not Junk or Trash; only the ``\\All`` folder where there is one). Over all
    accounts, one that cannot be searched is a failure of the page with no folder (an account
    without a usable password: code ``password_missing``; one too slow: ``timeout``) and the
    others still answer, however many there are. Over one account and one folder, its error is
    refused, as for a list."""
    accounts = _accounts(ctx, user_id)
    try:
        core.search_arguments(query.criteria())  # a line break in a term: refused up front
    except MailError as error:
        raise mail_refusal(error.code, error.message) from None
    skipped: list[PageFailure] = []
    if account == ALL_ACCOUNTS:
        wanted = accounts.ids()
        if wanted:
            require_subkey(ctx.subkey)
        usable = []
        for account_id in wanted:
            if _missing_password(ctx, user_id, account_id):
                skipped.append(_password_missing_failure(account_id, None))
            else:
                usable.append(account_id)
        if not usable:
            return Page((), None, tuple(skipped))
        page = _call(
            ctx,
            user_id,
            accounts,
            "mail_searched",
            via,
            account,
            lambda connections: _search_accounts(
                connections, usable, folder, query, limit=limit, cursor=cursor
            ),
            on=usable,
        )
        page = _with_password_failures(ctx, user_id, page)
        return Page(page.messages, page.next_cursor, (*skipped, *page.failures))
    _usable(ctx, user_id, accounts, account)
    page = _call(
        ctx,
        user_id,
        accounts,
        "mail_searched",
        via,
        account,
        lambda connections: core.search_messages(
            connections, [account], folder, query, limit=limit, cursor=cursor
        ),
    )
    page = _with_password_failures(ctx, user_id, page)
    if folder != ALL_FOLDERS:
        _first_failure(page)
    return page


def _sources(
    connections: MailConnections, account: str, folder: str, query: SearchQuery
) -> tuple[list[Source], Criteria]:
    """What mailroom-core's ``search_messages`` reads in ``account``, and with which criteria:
    as it does, a Gmail account's ``has_attachment`` goes into X-GM-RAW (Gmail ignores the
    ``Content-Type`` header search), so its capabilities are asked only when the query needs
    them."""
    gmail = (
        query.gmail_query is not None or query.has_attachment is True
    ) and connections.capabilities(account).gmail
    if query.gmail_query is not None and not gmail:
        raise MailError(
            "unsupported", f'gmail_query works only on Gmail accounts; "{account}" is not one.'
        )
    paths = core.searchable_folders(connections, account) if folder == ALL_FOLDERS else [folder]
    return [Source(account, path) for path in paths], query.criteria(gmail=gmail)


class _Reading:
    """``connections`` for ``read_page``, counting the mailboxes each account has open for it:
    the accounts that still read when the search's time is up, and only those, are cut."""

    def __init__(self, connections: MailConnections) -> None:
        self._connections = connections
        self._lock = threading.Lock()
        self._open: Counter[str] = Counter()

    def with_mailbox[T](
        self,
        account_id: str,
        path: str,
        read_only: bool,
        fn: Callable[[MailboxContext], T],
    ) -> T:
        with self._lock:
            self._open[account_id] += 1
        try:
            return self._connections.with_mailbox(account_id, path, read_only, fn)
        finally:
            with self._lock:
                self._open[account_id] -= 1

    def __getattr__(self, name: str) -> object:
        return getattr(self._connections, name)

    def reading(self) -> list[str]:
        with self._lock:
            return sorted(account for account, count in self._open.items() if count > 0)


def _search_accounts(
    connections: MailConnections,
    accounts: Sequence[str],
    folder: str,
    query: SearchQuery,
    *,
    limit: int,
    cursor: str | None,
) -> Page:
    """mailroom-core's ``search_messages`` over several accounts, with a deadline per account:
    their folders are found side by side, ``SOURCES_SECONDS`` at most, and the accounts still
    reading a mailbox at ``ACCOUNT_SECONDS`` are cut (one already done is left alone, so another
    call on it goes on). Each such account is a ``timeout`` failure; an account that fails is a
    failure too, whatever the number of accounts."""
    started = time.monotonic()
    failures: list[PageFailure] = []
    sources: list[Source] = []
    criteria: dict[str, Criteria] = {}
    finder = ThreadPoolExecutor(max_workers=min(len(accounts), 8), thread_name_prefix="mail-src")
    found = {
        account: finder.submit(_sources, connections, account, folder, query)
        for account in accounts
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
    cut: set[str] = set()
    reading = _Reading(connections)

    def expire() -> None:
        for account in reading.reading():
            if connections.interrupt(account):
                cut.add(account)

    timer = threading.Timer(max(0.0, started + ACCOUNT_SECONDS - time.monotonic()), expire)
    timer.daemon = True
    timer.start()
    try:
        page = core.read_page(
            cast("MailConnections", reading),
            sources,
            criteria,
            limit=limit,
            cursor=cursor,
        )
    finally:
        timer.cancel()
    read_failures = (
        PageFailure(f.account, f.folder, "timeout", MESSAGES["timeout"])
        if f.account in cut and f.code == "unreachable"
        else f
        for f in page.failures
    )
    return Page(page.messages, page.next_cursor, (*failures, *read_failures))


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
    """One message, its text body windowed by the user's ``body_chars`` from ``offset``;
    ``raw_headers``, ``remote_check`` and ``marker`` (by default none where quotes were left
    out) as for mailroom-core's ``read_message``."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    body_chars = accounts.limits.body_chars
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


def account_ids(ctx: AppContext, user_id: int) -> Sequence[str]:
    """The user's account ids, in their order."""
    return _accounts(ctx, user_id).ids()


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


def _ref_accounts(
    ctx: AppContext, user_id: int, accounts: _Accounts, refs: Sequence[str]
) -> list[str]:
    """The accounts ``refs`` name, in order, once every ref parses and names one of the user's
    usable accounts: else the whole batch is refused, before anything is written."""
    named: list[str] = []
    for ref in refs:
        try:
            account_id = core.parse_ref(ref).account
        except MailError as error:
            raise mail_refusal(error.code, error.message) from None
        if not accounts.has(account_id):
            raise unknown_message()
        if account_id not in named:
            named.append(account_id)
    for account_id in named:
        _usable(ctx, user_id, accounts, account_id)
    return named


class _Stoppable:
    """``connections`` for an organize call that refuse every mailbox and folder list
    (``timeout``) once ``stop`` is set: the groups after the call's deadline are refused, not
    written. A mailbox already selected is checked again before its group acts."""

    def __init__(self, connections: MailConnections, stop: threading.Event) -> None:
        self._connections = connections
        self._stop = stop

    def _check(self) -> None:
        if self._stop.is_set():
            raise MailError("timeout")

    def with_mailbox[T](
        self,
        account_id: str,
        path: str,
        read_only: bool,
        fn: Callable[[MailboxContext], T],
    ) -> T:
        self._check()

        def checked(context: MailboxContext) -> T:
            self._check()
            return fn(context)

        return self._connections.with_mailbox(account_id, path, read_only, checked)

    def folders(self, account_id: str, *, with_status: bool = False) -> list[Folder]:
        self._check()
        return self._connections.folders(account_id, with_status=with_status)

    def __getattr__(self, name: str) -> object:
        return getattr(self._connections, name)


def _organize(
    ctx: AppContext,
    user_id: int,
    event: str,
    via: Via,
    refs: Sequence[str],
    work: Callable[[MailConnections], OrganizeResult],
) -> OrganizeResult:
    """``work`` on the batch ``refs`` once each ref is checked (``_ref_accounts``), logged with
    its accounts, counts and failure codes only: never a folder, a ref or a message's data (the
    result's audit items, which the app does not keep). A call cut at the deadline writes no group
    after the cut (``_Stoppable``); the ones done before it stay done."""
    accounts = _accounts(ctx, user_id)
    used = _ref_accounts(ctx, user_id, accounts, refs)
    started = time.monotonic()
    result: OrganizeResult | None = None
    outcome: str = "ok"
    stop = threading.Event()
    try:
        result = _run(
            ctx,
            user_id,
            accounts,
            event,
            used,
            lambda connections: work(cast("MailConnections", _Stoppable(connections, stop))),
            on_deadline=stop.set,
        )
        outcome = result.outcome
        return result
    except Refusal as refusal:
        outcome = refusal.code
        raise
    finally:
        groups = () if result is None else result.groups
        logger.info(
            event,
            extra={
                "event": event,
                "user_id": user_id,
                "via": via,
                "account_ids": used,
                "ref_count": len(refs),
                "changed": sum(group.count for group in groups),
                "groups": len(groups),
                "failed_groups": sum(not group.ok for group in groups),
                "codes": sorted({group.error.code for group in groups if group.error is not None}),
                "outcome": outcome,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )


def move(
    ctx: AppContext, user_id: int, refs: Sequence[str], folder: str, *, via: Via = "web"
) -> OrganizeResult:
    """Moves the messages ``refs`` name (1 to ``MAX_REFS``, any folders and accounts) to
    ``folder`` of each one's account, never Drafts or Sent: mailroom-core's ``move_messages``,
    one result per source folder."""
    return _organize(
        ctx,
        user_id,
        "mail_moved",
        via,
        refs,
        lambda connections: core.move_messages(connections, refs, folder),
    )


def archive(
    ctx: AppContext, user_id: int, refs: Sequence[str], *, via: Via = "web"
) -> OrganizeResult:
    """Moves them to each account's folder with role ``archive``: ``unsupported`` for an
    account's group when it has none."""
    return _organize(
        ctx,
        user_id,
        "mail_archived",
        via,
        refs,
        lambda connections: core.archive_messages(connections, refs),
    )


def trash(
    ctx: AppContext, user_id: int, refs: Sequence[str], *, via: Via = "web"
) -> OrganizeResult:
    """Moves them to each account's Trash; never a permanent delete, so a group already in Trash
    is refused."""
    return _organize(
        ctx,
        user_id,
        "mail_trashed",
        via,
        refs,
        lambda connections: core.trash_messages(connections, refs),
    )


def flag(
    ctx: AppContext,
    user_id: int,
    refs: Sequence[str],
    *,
    seen: bool | None = None,
    flagged: bool | None = None,
    via: Via = "web",
) -> OrganizeResult:
    """Sets (True) or clears (False) ``\\Seen`` and ``\\Flagged``, at least one of them (else
    refused before anything connects), and reads them back; the refs stay valid."""
    if seen is None and flagged is None:
        neither = MailError("invalid_argument", "Give `seen`, `flagged` or both.")
        raise mail_refusal(neither.code, neither.message)
    return _organize(
        ctx,
        user_id,
        "mail_flagged",
        via,
        refs,
        lambda connections: core.set_flags(connections, refs, seen=seen, flagged=flagged),
    )
