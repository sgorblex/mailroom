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
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, cast

from mailroom.accounts.service import require_subkey
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail.connections import PASSWORD_MISSING, PASSWORD_MISSING_TEXT, PasswordMissing
from mailroom.mail.service._shared import (
    _accounts,
    _call,
    _message_ref,
    _missing_password,
    _run,
    _usable,
)
from mailroom_core import mail as core
from mailroom_core.mail import MESSAGES, MailError, Page, PageFailure, SearchQuery, Source

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

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


def indexable_folders(
    ctx: AppContext, user_id: int, account_id: int, *, via: Via = "web"
) -> tuple[bool, list[Folder]]:
    """Whether the account's server is Gmail (``X-GM-EXT-1``), and its selectable folders with
    their counts: what the mail index's folders follow."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)

    def read(connections: MailConnections) -> tuple[bool, list[Folder]]:
        gmail = connections.capabilities(account_id).gmail
        listed = connections.folders(account_id, with_status=True)
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
    via: Via = "web",
) -> Page:
    """One folder, newest first. A folder that cannot be read is refused with its error, not
    reported in the page. With ``subfolders``, the folder and its subfolders merged
    (mailroom-core's ``read_folders``): each one that cannot be read is a failure of the page."""
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
            connections, account_id, folder, limit=limit, cursor=cursor, subfolders=subfolders
        ),
    )
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
    via: Via = "web",
) -> Page:
    """``query`` over one account or all (``ALL_ACCOUNTS``), in one folder (with
    ``subfolders``, its subfolders too; refused with ``ALL_FOLDERS``) or all
    (``ALL_FOLDERS``: not Junk or Trash; only the ``\\All`` folder where there is one). Over all
    accounts, one that cannot be searched is a failure of the page with no folder (an account
    without a usable password: code ``password_missing``; one too slow: ``timeout``) and the
    others still answer, however many there are; later pages report it again without asking it
    again (nor checking its password), until paging ends. Over one account and one folder, its
    error is refused, as for a list, unless it reads subfolders too."""
    check_search(folder, query, subfolders=subfolders)
    accounts = _accounts(ctx, user_id)
    skipped: list[PageFailure] = []
    if account == ALL_ACCOUNTS:
        try:
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
            return Page((), None, (*before, *skipped))
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
            ),
            on=usable,
        )
        return _with_password_failures(ctx, user_id, page)
    _usable(ctx, user_id, accounts, account)
    page = _call(
        ctx,
        user_id,
        accounts,
        "mail_searched",
        via,
        account,
        lambda connections: core.search_messages(
            connections,
            [account],
            folder,
            query,
            limit=limit,
            cursor=cursor,
            subfolders=subfolders,
        ),
    )
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
    return Page(page.messages, page.next_cursor, password_checked(ctx, user_id, page.failures))


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
) -> Page:
    """mailroom-core's ``search_messages`` over several accounts, with a deadline per account:
    their folders are found side by side, ``SOURCES_SECONDS`` at most, and the accounts still
    reading a mailbox at ``ACCOUNT_SECONDS`` are cut (one already done is left alone, so another
    call on it goes on). Each such account is a ``timeout`` failure; an account that fails is a
    failure too, whatever the number of accounts. An account that failed before its folders were
    found, on an earlier page, is not asked again: the cursor carries it, and each page reports
    it. ``skipped`` are the accounts the caller did not pass, failed with the app's own codes
    (``HOST_CODES``): the cursor carries them likewise."""
    started = time.monotonic()
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
            account_failures=failures,
            host_codes=HOST_CODES,
        )
    finally:
        timer.cancel()


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
