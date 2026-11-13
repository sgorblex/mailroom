"""Reading a user's mail: folders, pages of messages (a list, a search), one message, its thread,
its HTML and its attachments."""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from typing import TYPE_CHECKING, Final, cast

from mailroom.accounts.service import require_subkey
from mailroom.errors import mail_refusal
from mailroom.mail.connections import PASSWORD_MISSING, PASSWORD_MISSING_TEXT
from mailroom.mail.service._shared import (
    _accounts,
    _call,
    _message_ref,
    _missing_password,
    _usable,
)
from mailroom_core import mail as core
from mailroom_core.mail import MESSAGES, MailError, Page, PageFailure, SearchQuery, Source

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mailroom.accounts.service import Via
    from mailroom.context import AppContext
    from mailroom_core.mail import (
        Attachment,
        Criteria,
        Folder,
        MailboxContext,
        MailConnections,
        MailErrorCode,
        MessageView,
    )
    from mailroom_core.render import Marker, SafeHtml

logger = logging.getLogger(__name__)

ALL_ACCOUNTS: Final = "*"
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
ACCOUNT_SECONDS: Final = 25.0
"""When an all-accounts search cuts the accounts still reading their mailbox (``timeout``
failures), inside the pool's 30 s deadline, so the others still answer."""


def folders(
    ctx: AppContext,
    user_id: int,
    account_id: str,
    *,
    counts: bool = True,
    every: bool = False,
    via: Via = "web",
) -> list[Folder]:
    """The account's selectable folders in mailroom-core's order (special-use first, then the rest
    by path), with their counts (STATUS: one command per folder) unless ``counts`` is off.
    ``every``: the ``\\Noselect`` ones too (a tree's parents, without counts)."""
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
    return listed if every else [folder for folder in listed if folder.selectable]


def folder_segments(folder: Folder) -> list[str]:
    """``folder``'s path split on its delimiter: how a rule set names a folder."""
    return folder.path.split(folder.delimiter) if folder.delimiter else [folder.path]


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


def _password_missing_failure(account_id: str, folder: str | None) -> PageFailure:
    code = cast("MailErrorCode", PASSWORD_MISSING)  # the app's own code
    return PageFailure(account_id, folder, code, PASSWORD_MISSING_TEXT)


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
