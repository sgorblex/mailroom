"""A user's mail as every front end reads and writes it (the REST routes, the MCP tools): folders,
pages of messages, one message, its HTML and its attachments, over mailroom-core; moves, archiving,
trashing and flag changes of batches of refs; drafts and sends; creating, renaming and deleting
folders. Reading changes nothing, not even ``\\Seen``. Mail is deleted only deliberately: nothing
is expunged beyond what a move takes away and a draft mailroom wrote (*The own-draft exception*),
and a folder's delete takes its mail for good only with the count the user confirmed (*Folder
management*; both in ``docs/requirements/mail-client.md``).

Only the user's own accounts: an account id, or the account a ref names, that is not theirs is
``not_found`` exactly as one that does not exist. A mailbox failure is a ``Refusal`` of kind
``mail`` carrying mailroom-core's code and fixed message (never the server's words); a call still
waiting on the server after ``MailPool.deadline_seconds`` is ``timeout``. Every function blocks:
an async caller runs it in a worker thread. The log names the call, the account and the front end
(``via``), never a subject, an address, a folder or a body; an organize call's and a send's add
only counts and codes.

A send has two halves. ``send_draft`` admits it under the pool's deadline, in the request; delivery
runs on a ``SendRunner`` thread, past the end of the request, and writes its outcome to the send's
row. From the moment the server has taken the message nothing can make that a failure: filing it,
removing its draft and marking what it answered are later calls, each of which at worst sets
``filed`` to false or adds a note. A draft that did not reach Sent stays where it is: it is the only
copy the account has.

An agent's direct send (``send_message``, ``send_reply``, ``send_forward``) goes the same way, by
``agent``, without a key: its Message-ID is fresh, so a repeat is a second message. Where the send
mode only drafts it, the gate saves a draft and the answer says so (``Dispatched``).
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Final, Literal, cast

from mailroom.accounts.service import account_not_found, attachment_limit_mb, require_subkey
from mailroom.accounts.store import AccountNotFound, account_password, load_accounts
from mailroom.audit import WEB, AuditTrail, Origin, kept_nowhere
from mailroom.errors import Refusal, mail_refusal
from mailroom.filters.folders import Rewritten, naming_rule_sets, rewrite_folder
from mailroom.mail.connections import PASSWORD_MISSING, PASSWORD_MISSING_TEXT, PasswordMissing
from mailroom.mail.pool import DeadlineExceeded
from mailroom_core import mail as core
from mailroom_core import send as gate
from mailroom_core.mail import (
    MESSAGES,
    AuditItem,
    FolderChangedError,
    MailError,
    Page,
    PageFailure,
    SearchQuery,
    Source,
)
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from imapclient import IMAPClient

    from mailroom.accounts.service import Via
    from mailroom.accounts.store import UserAccounts
    from mailroom.context import AppContext
    from mailroom.filters.folders import RuleSetRef
    from mailroom.mail.sends import SendRunner
    from mailroom_core.config import AccountConfig, Limits
    from mailroom_core.mail import (
        Attachment,
        AttachmentSource,
        AuditResult,
        Authoriser,
        Criteria,
        Draft,
        Folder,
        FolderChange,
        Mailbox,
        MailboxContext,
        MailConnections,
        MailErrorCode,
        MessageInput,
        MessageRef,
        MessageView,
        OrganizeResult,
        OutgoingAttachment,
        SavedDraft,
    )
    from mailroom_core.render import Marker, PartInfo, SafeHtml
    from mailroom_core.send import Transfer
    from mailroom_core.store import Json, SendRecord, SendState

logger = logging.getLogger(__name__)

MAX_FOLDER: Final = 1000
MAX_CURSOR: Final = 65536
"""A cursor holds a position per folder still being read: a search over every folder of
several accounts makes long ones (mailroom-core checks their structure)."""
MAX_TERM: Final = 500
"""The bounds every front end puts on what it takes (a folder path, a cursor, a search term)."""
MAX_FOLDER_NAME: Final = core.MAX_FOLDER_NAME
"""Of the name a create or a rename gives a folder (one segment of its path)."""
MAX_MESSAGE_COUNT: Final = 2**32 - 1
"""An IMAP count's bound: of the ``messages`` a folder's delete takes."""
MAX_IN_USE_LISTED: Final = 10
"""The rule sets a folder delete's ``folder_in_use`` refusal names."""
ALL_ACCOUNTS: Final = "*"
ALL_FOLDERS: Final = core.ALL_FOLDERS
MAX_REFS: Final = core.MAX_REFS
"""The most refs one organize call takes."""
MAX_REF_LENGTH: Final = 4096
"""The longest ref a front end takes (a ref holds its folder's path)."""
INLINE_IMAGE_BYTES: Final = 5 * 1024 * 1024
"""The ``cid:`` images inlined into one HTML view, in total; past it an image keeps only its
alt text."""
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
UNDELIVERABLE: Final = (
    "A recipient is not a deliverable address, or the mail server refused the message; "
    "nothing was sent."
)
UPLOAD_OVERHEAD: Final = 2 * 1024 * 1024
"""What a save's request may hold beyond its attachments: the text, the recipients and the
multipart framing."""
TIME_ZONE: Final = "UTC"
"""Of the dates quoted in a reply or a forward: the user has no time zone yet."""
NOT_FILED: Final = "Sent, but not filed in Sent."
SEND_HOLDS_THE_DRAFT: Final = frozenset({"sending", "unknown", "sent"})
"""Its delivery is going, unresolved or done: a new version could slip past ``delivery_unknown``
with a new key, or make a replayed "sent" name recipients who never got it."""
DRAFT_NOT_EDITABLE: Final = "This draft was sent, or may have been, so it cannot be changed."
DRAFT_GONE: Final = frozenset({"not_found", "stale_reference"})
"""What reading a draft answers once its ref no longer holds it."""
_SEND_ID: Final = re.compile(r"[0-9]{1,18}")
"""What fits the column: anything else names no send."""


def unknown_account() -> Refusal:
    """An account id that is not the user's (theirs or nobody's: the same answer), as the
    accounts service words it, its code kept for the mail client."""
    return account_not_found(coded=True)


def unknown_message() -> Refusal:
    """A ref naming an account that is not the user's: answered as a missing message."""
    return Refusal("not_found", "not_found", MESSAGES["not_found"], coded=True)


def _runner(ctx: AppContext) -> SendRunner:
    if ctx.send_runner is None:
        msg = "The mail layer is off: nothing that sends mail is served."
        raise RuntimeError(msg)
    return ctx.send_runner


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

    @property
    def write_limits(self) -> Limits:
        """``limits`` with the cap on a written message's attachments the app keeps to."""
        cap = attachment_limit_mb(self.limits)
        return self.limits.model_copy(update={"attachment_max_mb": cap})

    def config(self, account_id: str) -> AccountConfig:
        return self.loaded.account(account_id)

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
    return PageFailure(account_id, folder, code, PASSWORD_MISSING_TEXT)


def _usable(ctx: AppContext, user_id: int, accounts: _Accounts, account_id: str) -> None:
    """Refuse an account that is not the user's, or that cannot log in: no secrets key
    (``secrets_key_missing``), no password that opens (``password_missing``)."""
    if not accounts.has(account_id):
        raise unknown_account()
    require_subkey(ctx.subkey)
    if _missing_password(ctx, user_id, account_id):
        raise mail_refusal(PASSWORD_MISSING, PASSWORD_MISSING_TEXT)


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
    pool = ctx.mail_pool
    try:
        with pool.lease(ctx, user_id, accounts.revision) as connections:
            return pool.call(
                connections, user_id, used, lambda: work(connections), on_deadline=on_deadline
            )
    except MailError as error:
        raise mail_refusal(error.code, error.message) from None
    except DeadlineExceeded:
        raise mail_refusal("timeout", MESSAGES["timeout"]) from None
    except PasswordMissing:
        raise mail_refusal(PASSWORD_MISSING, PASSWORD_MISSING_TEXT) from None
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
    on_deadline: Callable[[], None] | None = None,
) -> T:
    """``work`` as ``_run`` does it, logged. ``on``: the accounts it uses, when not just
    ``account_id``."""
    started = time.monotonic()
    outcome = "ok"
    try:
        used = [account_id] if on is None else list(on)
        return _run(ctx, user_id, accounts, event, used, work, on_deadline=on_deadline)
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
    """``connections`` for a writing call that refuse every mailbox, session without one and
    folder list (``timeout``) once ``stop`` is set: the steps after the call's deadline are
    refused, not written. A mailbox already selected, or a session already open, is checked again
    before its step acts. ``confirming``: a folder list still answers after the stop, since it
    writes nothing and decides whether a folder's write that lost its answer happened
    (mailroom-core's ``create_folder``…), so that what follows it (a rename's rule sets) does."""

    def __init__(
        self, connections: MailConnections, stop: threading.Event, *, confirming: bool = False
    ) -> None:
        self._connections = connections
        self._stop = stop
        self._confirming = confirming

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

    def with_connection[T](self, account_id: str, fn: Callable[[IMAPClient], T]) -> T:
        self._check()

        def checked(client: IMAPClient) -> T:
            self._check()
            return fn(client)

        return self._connections.with_connection(account_id, checked)

    def folders(self, account_id: str, *, with_status: bool = False) -> list[Folder]:
        if not self._confirming:
            self._check()
        return self._connections.folders(account_id, with_status=with_status)

    def __getattr__(self, name: str) -> object:
        return getattr(self._connections, name)


def _organize(
    ctx: AppContext,
    user_id: int,
    event: str,
    action: str,
    origin: Origin,
    refs: Sequence[str],
    work: Callable[[MailConnections], OrganizeResult],
) -> OrganizeResult:
    """``work`` on the batch ``refs`` once each ref is checked (``_ref_accounts``), logged with
    its accounts, counts and failure codes only: never a folder, a ref or a message's data, which
    go to the audit trail as ``action`` (the result's items), recorded by the thread that did the
    work. A call cut at the deadline writes no group after the cut (``_Stoppable``); the ones done
    before it stay done, and are recorded once that thread finishes."""
    via = origin.via
    trail = AuditTrail(ctx.session_maker, user_id, origin, action)
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
            lambda connections: _recorded(
                trail, work(cast("MailConnections", _Stoppable(connections, stop)))
            ),
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
    ctx: AppContext, user_id: int, refs: Sequence[str], folder: str, *, origin: Origin = WEB
) -> OrganizeResult:
    """Moves the messages ``refs`` name (1 to ``MAX_REFS``, any folders and accounts) to
    ``folder`` of each one's account, never Drafts or Sent: mailroom-core's ``move_messages``,
    one result per source folder."""
    return _organize(
        ctx,
        user_id,
        "mail_moved",
        "move",
        origin,
        refs,
        lambda connections: core.move_messages(connections, refs, folder),
    )


def archive(
    ctx: AppContext, user_id: int, refs: Sequence[str], *, origin: Origin = WEB
) -> OrganizeResult:
    """Moves them to each account's folder with role ``archive``: ``unsupported`` for an
    account's group when it has none."""
    return _organize(
        ctx,
        user_id,
        "mail_archived",
        "archive",
        origin,
        refs,
        lambda connections: core.archive_messages(connections, refs),
    )


def trash(
    ctx: AppContext, user_id: int, refs: Sequence[str], *, origin: Origin = WEB
) -> OrganizeResult:
    """Moves them to each account's Trash; never a permanent delete, so a group already in Trash
    is refused."""
    return _organize(
        ctx,
        user_id,
        "mail_trashed",
        "trash",
        origin,
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
    origin: Origin = WEB,
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
        "flag",
        origin,
        refs,
        lambda connections: core.set_flags(connections, refs, seen=seen, flagged=flagged),
    )


@dataclass(frozen=True, slots=True)
class FolderRenamed:
    """A rename: the folder's old path and its new one (``change``), and the rule sets that
    followed it."""

    change: FolderChange
    rule_sets: tuple[int, ...]
    """Every rule set changed, by its own folder or a filter's action."""
    filters: tuple[int, ...]
    rule_sets_updated: bool
    """False when the database step failed: the folder is renamed, the rule sets still name its
    old path."""


@contextmanager
def _logged_folder_call(
    event: str, user_id: int, via: Via, account_id: str
) -> Iterator[dict[str, object]]:
    """Logs a folder call as ``event`` once it ends: its outcome and duration, and the counts the
    caller puts in the dict it is given; never a folder's path or a rule set's name."""
    started = time.monotonic()
    logged: dict[str, object] = {"outcome": "ok"}
    try:
        yield logged
    except Refusal as refusal:
        logged["outcome"] = refusal.code
        raise
    except Exception:
        logged["outcome"] = "failed"
        raise
    finally:
        logger.info(
            event,
            extra={
                "event": event,
                "user_id": user_id,
                "via": via,
                "account_id": account_id,
                **logged,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )


def _folder_write[T](
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    event: str,
    account_id: str,
    work: Callable[[MailConnections], T],
) -> T:
    """``work`` on the account under the pool's gates and deadline, past which it writes nothing
    more (``_Stoppable``); a folder list that confirms a write whose answer was lost still runs,
    and so may the listing that reads an uncached delimiter, which writes nothing."""
    stop = threading.Event()
    return _run(
        ctx,
        user_id,
        accounts,
        event,
        [account_id],
        lambda connections: work(
            cast("MailConnections", _Stoppable(connections, stop, confirming=True))
        ),
        on_deadline=stop.set,
    )


def _segments(path: str, delimiter: str | None) -> list[str]:
    return path.split(delimiter) if delimiter else [path]


def create_folder(
    ctx: AppContext,
    user_id: int,
    account_id: str,
    parent: str | None,
    name: str,
    *,
    origin: Origin = WEB,
) -> FolderChange:
    """Creates the folder ``name`` under ``parent`` (None: the top level), subscribed:
    mailroom-core's ``create_folder``, recorded as ``folder_create``."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    trail = AuditTrail(ctx.session_maker, user_id, origin, "folder_create")
    with _logged_folder_call("mail_folder_created", user_id, origin.via, account_id):
        return _folder_write(
            ctx,
            user_id,
            accounts,
            "mail_folder_created",
            account_id,
            lambda connections: core.create_folder(
                connections, account_id, parent, name, trail.note
            ),
        )


def _rewrite_rule_sets(
    ctx: AppContext, user_id: int, account_id: str, change: FolderChange
) -> Rewritten | None:
    """Points the rule sets and filters naming the renamed folder at its new path; what it
    changed, or None, logged, when the database step failed (the rename stands)."""
    destination = change.destination if change.destination is not None else change.path
    try:
        with ctx.session_maker() as db:
            return rewrite_folder(
                db,
                user_id,
                account_id,
                _segments(change.path, change.delimiter),
                _segments(destination, change.delimiter),
            )
    except Exception as exc:
        logger.warning(
            "folder_rule_sets_failed",
            extra={
                "event": "folder_rule_sets_failed",
                "user_id": user_id,
                "account_id": account_id,
                "error": type(exc).__name__,
            },
        )
        return None


def rename_folder(
    ctx: AppContext,
    user_id: int,
    account_id: str,
    folder: str,
    parent: str | None,
    name: str,
    *,
    origin: Origin = WEB,
) -> FolderRenamed:
    """Renames ``folder`` to ``name`` under ``parent`` (None: the top level), its subfolders with
    it (mailroom-core's ``rename_folder``), then points the account's rule sets and filter
    actions that name it, or a folder under it, at the new path: enabled ones too. Both run on
    the pool's thread, so the rule sets follow a rename the server did after the call's deadline.
    A failure of that database step does not fail the call: ``rule_sets_updated`` is False.
    Recorded as ``folder_rename``, with the rule sets and filters changed."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    trail = AuditTrail(ctx.session_maker, user_id, origin, "folder_rename")

    def work(connections: MailConnections) -> FolderRenamed:
        noted: list[AuditItem] = []
        try:
            change = core.rename_folder(connections, account_id, folder, parent, name, noted.append)
        except Exception:
            for item in noted:
                trail.note(item)
            raise
        rewritten = _rewrite_rule_sets(ctx, user_id, account_id, change)
        extra: dict[str, object] = (
            {"rule_sets_error": True}
            if rewritten is None
            else {"rule_sets": rewritten.rule_sets, "filters": rewritten.filters}
        )
        for item in noted:
            trail.note(item, extra)
        return FolderRenamed(
            change=change,
            rule_sets=() if rewritten is None else tuple(rewritten.rule_sets),
            filters=() if rewritten is None else tuple(rewritten.filters),
            rule_sets_updated=rewritten is not None,
        )

    with _logged_folder_call("mail_folder_renamed", user_id, origin.via, account_id) as logged:
        renamed = _folder_write(ctx, user_id, accounts, "mail_folder_renamed", account_id, work)
        logged.update(
            rule_sets=len(renamed.rule_sets),
            filters=len(renamed.filters),
            rule_sets_updated=renamed.rule_sets_updated,
        )
        return renamed


def _in_use(named: Sequence[RuleSetRef]) -> Refusal:
    """``folder_in_use``, naming the first ``MAX_IN_USE_LISTED`` rule sets."""
    listed = named[:MAX_IN_USE_LISTED]
    names = ", ".join(f'"{clean_header(ref.name, 200)}"' for ref in listed)
    more = len(named) - len(listed)
    rest = f" and {more} more" if more else ""
    message = (
        f"Rule sets use this folder or one under it: {names}{rest}. "
        "Point them at another folder or remove them first."
    )
    return Refusal(
        "conflict",
        "folder_in_use",
        message,
        extra={"rule_sets": [{"id": ref.id, "name": ref.name} for ref in listed]},
    )


def delete_folder(
    ctx: AppContext,
    user_id: int,
    account_id: str,
    folder: str,
    *,
    messages: int,
    origin: Origin = WEB,
) -> FolderChange:
    """Deletes ``folder`` and its mail for good, never to Trash: mailroom-core's
    ``delete_folder``, with ``messages`` the count the user confirmed (``folder_changed``,
    carrying the count it holds now, when it differs). Refused first, before the folder is
    touched, while a rule set or a filter's action names it or a folder under it
    (``folder_in_use``, listing them). Recorded as ``folder_delete``, refusals too."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    trail = AuditTrail(ctx.session_maker, user_id, origin, "folder_delete")

    def noted(result: AuditResult) -> None:
        trail.note(AuditItem(account=account_id, folder=folder, messages=messages, result=result))

    def work(connections: MailConnections) -> FolderChange:
        try:
            segments = _segments(folder, connections.delimiter(account_id))
            with ctx.session_maker() as db:
                named = naming_rule_sets(db, user_id, account_id, segments)
        except Exception as error:
            noted(core.audit_result(error))
            raise
        if named:
            noted(cast("MailErrorCode", "folder_in_use"))  # the app's own code
            raise _in_use(named)
        try:
            return core.delete_folder(
                connections, account_id, folder, messages=messages, note=trail.note
            )
        except FolderChangedError as error:
            raise Refusal(
                "mail", "folder_changed", error.message, extra={"messages": error.messages}
            ) from None

    with _logged_folder_call("mail_folder_deleted", user_id, origin.via, account_id) as logged:
        deleted = _folder_write(ctx, user_id, accounts, "mail_folder_deleted", account_id, work)
        logged["messages"] = deleted.messages
        return deleted


@dataclass(frozen=True, slots=True, kw_only=True)
class Prefill:
    """The fields of the draft a reply or a forward starts as; nothing is written yet."""

    account: str
    sender: str
    to: tuple[Mailbox, ...]
    cc: tuple[Mailbox, ...]
    subject: str
    text: str
    answers: str | None
    """The message a send is to mark ``\\Answered``: the ref, for a reply."""
    source: str
    """The ref ``attachments`` are parts of."""
    attachments: tuple[PartInfo, ...]
    """For a forward, the original's attachments, offered for keeping."""


@dataclass(frozen=True, slots=True, kw_only=True)
class DraftFields:
    """A draft as the user wrote it. Its addresses are checked here; the caps on the text and on
    the attachments too (``core.check_fields``, ``core.check_uploads``)."""

    account: str
    compose_id: str
    ref: str | None = None
    answers: str | None = None
    sender: str | None = None
    to: Sequence[Mailbox] = ()
    cc: Sequence[Mailbox] = ()
    bcc: Sequence[Mailbox] = ()
    subject: str = ""
    text: str = ""
    keep: Sequence[tuple[str, str]] = ()
    """``(ref, part)`` of the existing parts it keeps, its previous version's included."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SendView:
    """A send as a client follows it; ``docs/requirements/mail-client.md``, *Send state machine*."""

    send_id: str
    state: SendState
    code: str | None = None
    filed: bool | None = None
    rejected: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    note_codes: tuple[str, ...] = ()
    """``gate.SendNoteCode``s of ``notes``, for a client that words them itself; ``filed`` says
    the same as ``NOT_FILED``, which has none."""
    settled: bool = False
    """The send has all its notes: filing, \\Answered and the draft's removal are done, or failed.
    ``sent`` comes earlier, the moment the server took the message."""


@dataclass(frozen=True, slots=True)
class DraftView:
    draft: Draft
    send: SendView | None
    """The send of this draft, so that reopening one shows how it went."""


@dataclass(frozen=True, slots=True)
class _Outgoing:
    """What a send takes to ``gate.admit``, and what is left once the server has it."""

    message: MessageInput
    prepared: gate.Prepared | None
    key: str | None
    after: Callable[[MailConnections, bool], tuple[gate.SendNote, ...]]
    """Marking what it answered, removing its draft, given whether it was filed: the notes of
    what failed."""
    unsettled: Callable[[bool], tuple[gate.SendNote, ...]]
    """The notes for when ``after`` did not run at all, given the same."""


def _recorded(trail: AuditTrail, result: OrganizeResult) -> OrganizeResult:
    """``result``, its items in ``trail``."""
    trail.notes(result.items)
    return result


def _sending(ctx: AppContext, user_id: int, origin: Origin) -> AuditTrail:
    """A send's audit trail: admission, delivery, filing and settling are all ``send``."""
    return AuditTrail(ctx.session_maker, user_id, origin, "send")


@contextmanager
def _as_refusals() -> Iterator[None]:
    try:
        yield
    except MailError as error:
        raise mail_refusal(error.code, error.message) from None


def _writable(account: AccountConfig) -> None:
    """``send_disabled`` on an account in send mode ``off``: no drafts or sends."""
    with _as_refusals():
        core.check_sendable(account)


def _deliverable(mailboxes: Iterable[Mailbox]) -> tuple[Mailbox, ...]:
    """``mailboxes`` in the form every hop agrees on; mailroom-core words a bad one
    ``invalid_argument``, and the REST contract ``recipient_refused``, with a fixed text."""
    try:
        return tuple(replace(m, address=core.deliverable_address(m.address)) for m in mailboxes)
    except MailError:
        raise mail_refusal("recipient_refused", UNDELIVERABLE) from None


def _can_be_sent_to(mailbox: Mailbox) -> bool:
    try:
        core.deliverable_address(mailbox.address)
    except MailError:
        return False
    return True


def _stoppable_call[T](
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
    """``_call``, writing nothing further once the deadline has passed (``_Stoppable``)."""
    stop = threading.Event()
    return _call(
        ctx,
        user_id,
        accounts,
        event,
        via,
        account_id,
        lambda connections: work(cast("MailConnections", _Stoppable(connections, stop))),
        on=on,
        on_deadline=stop.set,
    )


def prefill(
    ctx: AppContext,
    user_id: int,
    ref: str,
    mode: Literal["reply", "reply_all", "forward"],
    *,
    via: Via = "web",
) -> Prefill:
    """What answering or forwarding the message ``ref`` names starts from; writes nothing. A
    recipient no send could reach is left out: it would refuse every save of the draft."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    account = accounts.config(message.account)
    _writable(account)
    original = _call(
        ctx,
        user_id,
        accounts,
        "mail_prefilled",
        via,
        message.account,
        lambda connections: core.original_message(connections, message),
    )
    with _as_refusals():
        if mode == "forward":
            return Prefill(
                account=account.id,
                sender=core.from_identity(account).address,
                to=(),
                cc=(),
                subject=_subject_cut(core.forward_subject(original.subject)),
                text=f"\n\n{core.forwarded_original(original, TIME_ZONE)}",
                answers=None,
                source=ref,
                attachments=original.attachments,
            )
        answered = core.reply_recipients(original, account, reply_all=mode == "reply_all")
        return Prefill(
            account=account.id,
            sender=core.from_identity(account, None, original.addressed).address,
            to=tuple(filter(_can_be_sent_to, answered.to)),
            cc=tuple(filter(_can_be_sent_to, answered.cc)),
            subject=_subject_cut(core.reply_subject(original.subject)),
            text=f"\n\n{core.quoted_original(original, TIME_ZONE)}",
            answers=ref,
            source=ref,
            attachments=(),
        )


def _subject_cut(subject: str) -> str:
    """Within the cap a save holds it to: the prefix may have taken it over."""
    return clean_header(subject, core.MAX_SUBJECT_CHARS)


def _replaced(
    ctx: AppContext,
    user_id: int,
    connections: MailConnections,
    replaces: MessageRef | None,
    compose_id: str,
) -> Draft | None:
    """The version a save replaces. Its ``compose_id`` is the send key, so a new one would forget
    how the draft's send went and deliver it again. A draft written elsewhere has none: the
    client gives it one, which ``_not_held`` allows only while no send stands under its ref. That
    send is why such a draft is normally gone, so a ref that holds nothing is asked about it too."""
    if replaces is None:
        return None
    try:
        previous = core.read_draft(connections, replaces)
    except MailError as error:
        if error.code in DRAFT_GONE:
            _not_held(ctx, user_id, replaces)
        raise
    if previous.compose_id is None:
        _not_held(ctx, user_id, replaces)
    elif previous.compose_id != compose_id:
        raise MailError("invalid_argument", "A draft keeps its compose_id across versions.")
    return previous


def _holding_send(ctx: AppContext, user_id: int, ref: MessageRef) -> SendRecord | None:
    """The send that holds the draft written elsewhere at ``ref``, there or not by now."""
    send = ctx.store(user_id).send_state_by_key(_ref_key(ref))
    return send if send is not None and send.state in SEND_HOLDS_THE_DRAFT else None


def _not_held(ctx: AppContext, user_id: int, ref: MessageRef) -> None:
    """Refuses to replace a draft written elsewhere whose send holds it: the key of such a draft
    is its ref, so the next version would stand free of that send. Moving the state to the new key
    instead could strand it if the APPEND then failed. An own draft's key survives a save. The
    refusal says how the send stands, which the client could no longer read from a draft that
    is gone."""
    send = _holding_send(ctx, user_id, ref)
    if send is not None:
        held = {"send_id": str(send.id), "state": send.state, "code": None}
        raise Refusal("conflict", "draft_not_editable", DRAFT_NOT_EDITABLE, extra={"send": held})


def _threaded(
    connections: MailConnections, message: MessageInput, previous: Draft | None
) -> MessageInput:
    """``message`` in its thread, which the client does not know: from the version it replaces,
    which also keeps the thread of a draft written elsewhere, else from the message it answers.
    One that is gone by now cannot be answered, but the draft is still saved."""
    if previous is not None:
        same = message.answers is None or (
            previous.answers is not None and core.format_ref(previous.answers) == message.answers
        )
        if same and (previous.in_reply_to is not None or previous.references):
            return replace(
                message, in_reply_to=previous.in_reply_to, references=previous.references
            )
    if message.answers is None:
        return message
    try:
        original = core.original_message(connections, core.parse_ref(message.answers))
    except MailError as error:
        if error.code in ("not_found", "stale_reference", "mailbox_missing"):
            return replace(message, answers=None)
        raise
    thread = core.threading(original)
    return replace(message, in_reply_to=thread.in_reply_to, references=thread.references)


def upload_limit(ctx: AppContext, user_id: int) -> int:
    """The largest save request the user can have a reason to make, in bytes."""
    return core.attachment_cap(_accounts(ctx, user_id).write_limits) + UPLOAD_OVERHEAD


@contextmanager
def upload_place(ctx: AppContext, user_id: int) -> Iterator[None]:
    """The user's place for a save's body while it is held, or ``busy`` at once."""
    with _as_refusals(), ctx.mail_pool.upload(user_id):
        yield


def save_draft(
    ctx: AppContext,
    user_id: int,
    fields: DraftFields,
    uploads: Sequence[OutgoingAttachment] = (),
    *,
    origin: Origin = WEB,
) -> SavedDraft:
    """Saves the draft, as a first version or as the next version of ``fields.ref``:
    mailroom-core's ``save_draft``, so a failure leaves the last good version where it was.
    Every ref is the user's or the whole save is ``not_found``, before anything is written. An
    agent's save is recorded in the audit trail, the web's are not: they are the user typing."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, fields.account)
    named = [fields.ref, fields.answers, *(ref for ref, _part in fields.keep)]
    used = _ref_accounts(ctx, user_id, accounts, [ref for ref in named if ref is not None])
    account = accounts.config(fields.account)
    limits = accounts.write_limits
    _writable(account)
    with _as_refusals():
        core.check_fields(
            to=fields.to, cc=fields.cc, bcc=fields.bcc, subject=fields.subject, text=fields.text
        )
    to, cc, bcc = _deliverable(fields.to), _deliverable(fields.cc), _deliverable(fields.bcc)
    with _as_refusals():
        if to or cc or bcc:
            core.check_recipient_count((*to, *cc, *bcc), limits.max_recipients)
        sender = core.from_identity(account, fields.sender)
    message = core.MessageInput(
        sender=sender,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=fields.subject,
        text=fields.text,
        attachments=tuple(uploads),
        answers=fields.answers,
    )
    replaces = None if fields.ref is None else core.parse_ref(fields.ref)
    for named_ref in (fields.ref, fields.answers):
        if named_ref is not None and core.parse_ref(named_ref).account != account.id:
            elsewhere = "That message belongs to another account."
            raise mail_refusal("invalid_argument", elsewhere)
    keep = _attachment_sources(fields.keep)
    note = (
        kept_nowhere
        if origin.via == "web"
        else AuditTrail(ctx.session_maker, user_id, origin, "draft_save").note
    )

    def saved(connections: MailConnections) -> SavedDraft:
        previous = _replaced(ctx, user_id, connections, replaces, fields.compose_id)
        return core.save_draft(
            connections,
            account,
            _threaded(connections, message, previous),
            cap=core.attachment_cap(limits),
            note=note,
            compose_id=fields.compose_id,
            replaces=replaces,
            keep=keep,
        )

    return _stoppable_call(
        ctx,
        user_id,
        accounts,
        "mail_draft_saved",
        origin.via,
        account.id,
        saved,
        on=sorted({account.id, *used}),
    )


def _ref_key(ref: MessageRef) -> str:
    return f"ref-{hashlib.sha256(core.format_ref(ref).encode()).hexdigest()}"


def _send_key(draft: Draft) -> str:
    """What guards a draft's send against a second delivery: its account and ``compose_id``, since
    a key is claimed per user and a copy between accounts keeps the id. A draft written elsewhere
    has none, and is one message, so its ref says which send it is."""
    if draft.compose_id is not None:
        return f"{draft.ref.account}/{draft.compose_id}"
    return _ref_key(draft.ref)


def _texts(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _send_view(send: SendRecord) -> SendView:
    result = send.result if isinstance(send.result, dict) else {}
    code = result.get("code")
    filed = result.get("filed")
    return SendView(
        send_id=str(send.id),
        state=send.state,
        code=str(code) if send.state == "failed" and code is not None else None,
        filed=filed if isinstance(filed, bool) else None,
        rejected=_texts(result.get("rejected")),
        notes=_texts(result.get("notes")),
        note_codes=_texts(result.get("note_codes")),
        settled=isinstance(result.get("notes"), list),
    )


def read_draft(ctx: AppContext, user_id: int, ref: str, *, via: Via = "web") -> DraftView:
    """A draft for the composer: ``ref`` must be in the account's Drafts."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    draft = _call(
        ctx,
        user_id,
        accounts,
        "mail_draft_read",
        via,
        message.account,
        lambda connections: core.read_draft(connections, message),
    )
    send = ctx.store(user_id).send_state_by_key(_send_key(draft))
    return DraftView(draft, None if send is None else _send_view(send))


def find_draft(
    ctx: AppContext, user_id: int, account_id: str, compose_id: str, *, via: Via = "web"
) -> Draft:
    """The newest version saved under ``compose_id``, ``not_found`` without one: what recovers
    a save whose answer was lost."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)

    def newest(connections: MailConnections) -> Draft:
        versions = core.find_drafts(connections, account_id, compose_id)
        if not versions:
            raise MailError("not_found")
        return core.read_draft(connections, versions[0])

    return _call(ctx, user_id, accounts, "mail_draft_found", via, account_id, newest)


def delete_draft(ctx: AppContext, user_id: int, ref: str, *, origin: Origin = WEB) -> str:
    """Removes a draft: ``expunged`` for one mailroom wrote, by its UID alone; ``trashed`` for
    any other."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    _writable(accounts.config(message.account))
    removed = AuditTrail(ctx.session_maker, user_id, origin, "draft_remove")
    return _call(
        ctx,
        user_id,
        accounts,
        "mail_draft_deleted",
        origin.via,
        message.account,
        lambda connections: core.remove_draft(connections, message, removed.note),
    )


def _smtp(ctx: AppContext, user_id: int) -> gate.SmtpSender:
    store = ctx.store(user_id)
    subkey = ctx.subkey

    def account(account_id: str) -> AccountConfig | None:
        try:
            return load_accounts(store).account(account_id)
        except AccountNotFound:
            return None

    return gate.SmtpSender(
        account,
        lambda account_id: account_password(store, subkey, account_id),
        ssl_context=ctx.ssl_context,
    )


def _after_delivery[T](
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    event: str,
    account_id: str,
    work: Callable[[MailConnections], T],
    fallback: T,
) -> T:
    """One step after the server took a message, as its own call: cut at the deadline, refused
    by the gates, or broken, it comes back ``fallback``, which only ever words what did not
    happen. The message has gone whatever happens here."""
    stop = threading.Event()
    try:
        return _run(
            ctx,
            user_id,
            accounts,
            event,
            [account_id],
            lambda connections: work(cast("MailConnections", _Stoppable(connections, stop))),
            on_deadline=stop.set,
        )
    except Refusal as refusal:
        logger.warning(event, extra={"event": event, "user_id": user_id, "code": refusal.code})
        return fallback


def _run_send(
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    origin: Origin,
    deps: gate.GateDeps,
    admitted: gate.Admitted,
    outgoing: _Outgoing,
    transfer: Transfer,
) -> None:
    """A send's thread: delivers, and only then files and settles. ``gate.deliver`` writes down
    how the delivery went, so there is nothing to add when it fails."""
    started = time.monotonic()
    account_id = admitted.account.id
    trail = _sending(ctx, user_id, origin)

    def finished(
        state: SendState, outcome: str, *, filed: bool | None = None, rejected: int = 0
    ) -> None:
        logger.info(
            "mail_send_finished",
            extra={
                "event": "mail_send_finished",
                "user_id": user_id,
                "via": origin.via,
                "account_id": account_id,
                "send_id": admitted.send.id,
                "state": state,
                "outcome": outcome,
                "filed": filed,
                "recipient_count": len(admitted.composed.envelope.recipients),
                "rejected_count": rejected,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )

    try:
        sent = gate.deliver(deps, admitted, trail.note, transfer)
    except MailError as error:
        delivered: SendState = "unknown" if error.code == "delivery_unknown" else "failed"
        finished(delivered, error.code)
        return
    except Exception as error:
        logger.warning(
            "mail_send_broke",
            extra={"event": "mail_send_broke", "error_type": type(error).__name__},
        )
        finished("unknown", "delivery_unknown")
        return
    filed = _after_delivery(
        ctx,
        user_id,
        accounts,
        "mail_send_not_filed",
        account_id,
        lambda connections: gate.file_sent(
            replace(deps, connections=connections), sent, trail.note
        ),
        False,
    )
    notes = _after_delivery(
        ctx,
        user_id,
        accounts,
        "mail_send_not_settled",
        account_id,
        lambda connections: outgoing.after(connections, filed),
        outgoing.unsettled(filed),
    )
    texts = tuple(note.text for note in notes)
    result: Json = {
        "rejected": [*sent.rejected],
        "filed": filed,
        "notes": [*texts] if filed else [NOT_FILED, *texts],
        "note_codes": [note.code for note in notes],
    }
    try:
        deps.send_log.finish_send(admitted.send, "sent", result)
    except Exception as error:
        logger.warning(
            "mail_send_result_lost",
            extra={"event": "mail_send_result_lost", "error_type": type(error).__name__},
        )
    finished("sent", "ok", filed=filed, rejected=len(sent.rejected))


def _admit(
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    account: AccountConfig,
    origin: Origin,
    outgoing: Callable[[MailConnections], _Outgoing],
    *,
    by: Authoriser,
    confirm_resend: bool,
    on: Sequence[str] | None = None,
) -> SendView | gate.Drafted:
    """Takes a send to the gate under the pool's deadline, and on to the runner: a place first,
    so that ``busy`` comes before anything is claimed. The running send is started inside the
    pool's call, so an admission that outlives its caller's deadline cannot leave a claimed send
    with nobody to run it. That admission is not stopped either: past the deadline it can still
    claim and start the send, so a ``timeout`` refusal does not mean nothing went out. An own
    draft's key guards a retry only while the draft exists, and the send removes the draft, so a
    caller must treat that refusal as not confirmed."""
    with _as_refusals():
        slot = _runner(ctx).reserve(user_id)
    stop = threading.Event()
    limits = accounts.write_limits

    def admission(connections: MailConnections) -> SendView | gate.Drafted:
        guarded = cast("MailConnections", _Stoppable(connections, stop))
        request = outgoing(guarded)
        if stop.is_set():
            raise MailError("timeout")
        deps = gate.GateDeps(
            connections=guarded,
            smtp=_smtp(ctx, user_id),
            send_log=ctx.store(user_id),
            limits=limits,
            time_zone=TIME_ZONE,
        )
        admitted = gate.admit(
            deps,
            account,
            request.message,
            _sending(ctx, user_id, origin).note,
            by=by,
            prepared=request.prepared,
            key=request.key,
            confirm_resend=confirm_resend,
        )
        if isinstance(admitted, gate.Admitted):
            slot.start(partial(_run_send, ctx, user_id, accounts, origin, deps, admitted, request))
            return _send_view(admitted.send)
        if isinstance(admitted, gate.Earlier):
            return _send_view(admitted.send)
        return admitted

    try:
        return _call(
            ctx,
            user_id,
            accounts,
            "mail_send_requested",
            origin.via,
            account.id,
            admission,
            on=on,
            on_deadline=stop.set,
        )
    finally:
        slot.release()


def _draft_unsettled(draft: Draft, filed: bool) -> tuple[gate.SendNote, ...]:
    """What ``gate.settle_draft`` would have reported had none of it worked."""
    unmarked = () if draft.answers is None else (gate.ORIGINAL_NOT_MARKED,)
    return (*unmarked, gate.DRAFT_NOT_REMOVED if filed else gate.DRAFT_KEPT)


def send_holds(ctx: AppContext, user_id: int, ref: str) -> bool:
    """Whether a send holds the draft written elsewhere at ``ref``, there or not by now."""
    return _holding_send(ctx, user_id, core.parse_ref(ref)) is not None


def _send_of_gone_draft(
    ctx: AppContext, user_id: int, ref: MessageRef, *, confirm_resend: bool
) -> SendView | None:
    """What a retry for a draft that is gone reaches: the send of one written elsewhere, whose key
    needs only its ref. None when that leaves nothing to answer, ``confirm_resend`` included: the
    user has said to send again, and there is no draft to send."""
    send = _holding_send(ctx, user_id, ref)
    if send is None or (send.state == "unknown" and confirm_resend):
        return None
    if send.state == "unknown":
        raise mail_refusal("delivery_unknown", MESSAGES["delivery_unknown"])
    return _send_view(send)


def send_draft(
    ctx: AppContext,
    user_id: int,
    ref: str,
    *,
    by: Authoriser,
    confirm_resend: bool = False,
    origin: Origin = WEB,
) -> SendView:
    """Sends the draft ``ref`` as it is stored, and answers once it is admitted: ``sending``
    (follow it with ``read_send``), or ``sent`` when this draft was delivered before. Its key is
    claimed once, so asking twice delivers once. ``delivery_unknown`` when an earlier attempt
    may have gone out, unless ``confirm_resend`` says the user knows; ``by`` an agent, a send
    mode that would only draft it again is refused (``gate.admit``)."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    account = accounts.config(message.account)
    _writable(account)

    def stored(connections: MailConnections) -> _Outgoing:
        ready = gate.prepare_draft(connections, account, message, accounts.write_limits)
        _deliverable((*ready.message.to, *ready.message.cc, *ready.message.bcc))
        return _Outgoing(
            ready.message,
            ready.prepared,
            _send_key(ready.draft),
            lambda settling, filed: gate.settle_draft(
                settling, ready.draft, _sending(ctx, user_id, origin).note, filed=filed
            ),
            partial(_draft_unsettled, ready.draft),
        )

    try:
        started = _admit(
            ctx, user_id, accounts, account, origin, stored, by=by, confirm_resend=confirm_resend
        )
    except Refusal as refusal:
        if refusal.code not in DRAFT_GONE:
            raise
        earlier = _send_of_gone_draft(ctx, user_id, message, confirm_resend=confirm_resend)
        if earlier is None:
            raise
        return earlier
    if isinstance(started, gate.Drafted):  # a stored draft is refused, not drafted again
        raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"])
    return started


def read_send(ctx: AppContext, user_id: int, send_id: str) -> SendView:
    """The user's send ``send_id``. Another user's reads as one that does not exist."""
    send = None
    if _SEND_ID.fullmatch(send_id):
        send = ctx.store(user_id).send_state(int(send_id))
    if send is None:
        raise unknown_message()
    return _send_view(send)


@dataclass(frozen=True, slots=True, kw_only=True)
class Dispatched:
    """What an agent's direct send came to: ``send`` (follow it with ``read_send``) when the gate
    let it go, else it was saved as a draft (``draft``, None when it could not be read back)."""

    account: str
    send_mode: str
    message: MessageInput
    """As it goes out or was drafted, from and every recipient included."""
    send: SendView | None = None
    draft: MessageRef | None = None


def _attachment_sources(refs: Iterable[tuple[str, str]]) -> list[AttachmentSource]:
    return [core.AttachmentSource(core.parse_ref(ref), part) for ref, part in refs]


def _dispatch(
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    account: AccountConfig,
    origin: Origin,
    build: Callable[[MailConnections], _Outgoing],
    *,
    on: Sequence[str],
) -> Dispatched:
    """An agent's message to the gate. It has no key and a fresh Message-ID, so a repeat is a
    second message: a retry is the caller's to avoid. Where the send mode only drafts it, the
    gate saves it in Drafts."""
    built: list[MessageInput] = []

    def outgoing(connections: MailConnections) -> _Outgoing:
        request = build(connections)
        built.append(request.message)
        return request

    started = _admit(
        ctx, user_id, accounts, account, origin, outgoing, by="agent", confirm_resend=False, on=on
    )
    [message] = built
    if isinstance(started, gate.Drafted):
        return Dispatched(
            account=account.id, send_mode=account.send_mode, message=message, draft=started.ref
        )
    return Dispatched(
        account=account.id, send_mode=account.send_mode, message=message, send=started
    )


def _written(
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    account_id: str,
    refs: Sequence[str],
) -> tuple[AccountConfig, list[str]]:
    """The account that writes, once it may (not ``off``) and every ref is the user's."""
    if not accounts.has(account_id):
        raise unknown_account()
    _usable(ctx, user_id, accounts, account_id)
    used = _ref_accounts(ctx, user_id, accounts, refs)
    account = accounts.config(account_id)
    _writable(account)
    return account, sorted({account_id, *used})


def _plain(
    request: Callable[[MailConnections], MessageInput],
) -> Callable[[MailConnections], _Outgoing]:
    """``request`` as a message with nothing to do once it is delivered."""

    def build(connections: MailConnections) -> _Outgoing:
        return _Outgoing(
            request(connections), None, None, lambda _connections, _filed: (), lambda _filed: ()
        )

    return build


def send_message(
    ctx: AppContext,
    user_id: int,
    account_id: str,
    *,
    sender: str | None = None,
    to: Sequence[Mailbox] = (),
    cc: Sequence[Mailbox] = (),
    bcc: Sequence[Mailbox] = (),
    subject: str,
    text: str,
    attachments: Sequence[tuple[str, str]] = (),
    origin: Origin,
) -> Dispatched:
    """A new message from an agent, as far as the account's send mode lets it. ``attachments``
    are ``(ref, part)`` of existing messages."""
    accounts = _accounts(ctx, user_id)
    account, used = _written(ctx, user_id, accounts, account_id, [r for r, _ in attachments])
    with _as_refusals():
        core.check_fields(to=to, cc=cc, bcc=bcc, subject=subject, text=text)
    recipients = _deliverable(to), _deliverable(cc), _deliverable(bcc)
    limits = accounts.write_limits
    sources = _attachment_sources(attachments)
    with _as_refusals():
        from_ = core.from_identity(account, sender)

    def message(connections: MailConnections) -> MessageInput:
        files = core.attachments_from(connections, sources, core.attachment_cap(limits))
        return core.MessageInput(
            sender=from_,
            to=recipients[0],
            cc=recipients[1],
            bcc=recipients[2],
            subject=subject,
            text=text,
            attachments=files,
        )

    return _dispatch(ctx, user_id, accounts, account, origin, _plain(message), on=used)


def send_reply(
    ctx: AppContext,
    user_id: int,
    ref: str,
    *,
    text: str,
    reply_all: bool = False,
    sender: str | None = None,
    cc: Sequence[Mailbox] = (),
    bcc: Sequence[Mailbox] = (),
    attachments: Sequence[tuple[str, str]] = (),
    quote: bool = True,
    origin: Origin,
) -> Dispatched:
    """A reply from an agent: threaded, to the original's Reply-To or sender (everyone with
    ``reply_all``) and ``cc``, from the identity it was sent to, quoting it. It marks the
    original ``\\Answered`` once delivered."""
    accounts = _accounts(ctx, user_id)
    original_ref = _message_ref(ctx, user_id, accounts, ref)
    account, used = _written(
        ctx, user_id, accounts, original_ref.account, [ref, *(r for r, _ in attachments)]
    )
    with _as_refusals():
        core.check_fields(cc=cc, bcc=bcc, text=text)
    extra_cc, extra_bcc = _deliverable(cc), _deliverable(bcc)
    limits = accounts.write_limits
    sources = _attachment_sources(attachments)

    def build(connections: MailConnections) -> _Outgoing:
        original = core.original_message(connections, original_ref)
        answered = core.reply_recipients(original, account, reply_all=reply_all)
        thread = core.threading(original)
        files = core.attachments_from(connections, sources, core.attachment_cap(limits))
        message = core.MessageInput(
            sender=core.from_identity(account, sender, original.addressed),
            to=_deliverable(answered.to),
            cc=_deliverable((*answered.cc, *extra_cc)),
            bcc=extra_bcc,
            subject=_subject_cut(core.reply_subject(original.subject)),
            text=f"{text}\n\n{core.quoted_original(original, TIME_ZONE)}" if quote else text,
            answers=ref,
            in_reply_to=thread.in_reply_to,
            references=thread.references,
            attachments=files,
        )

        def mark(settling: MailConnections, _filed: bool) -> tuple[gate.SendNote, ...]:
            note = gate.mark_original(settling, original_ref, _sending(ctx, user_id, origin).note)
            return () if note is None else (note,)

        return _Outgoing(message, None, None, mark, lambda _filed: (gate.ORIGINAL_NOT_MARKED,))

    return _dispatch(ctx, user_id, accounts, account, origin, build, on=used)


def send_forward(
    ctx: AppContext,
    user_id: int,
    ref: str,
    *,
    to: Sequence[Mailbox],
    cc: Sequence[Mailbox] = (),
    bcc: Sequence[Mailbox] = (),
    text: str | None = None,
    sender: str | None = None,
    include_attachments: bool = True,
    origin: Origin,
) -> Dispatched:
    """A forward from an agent: the original inline, its attachments too by default, an optional
    note above it."""
    accounts = _accounts(ctx, user_id)
    original_ref = _message_ref(ctx, user_id, accounts, ref)
    account, used = _written(ctx, user_id, accounts, original_ref.account, [ref])
    with _as_refusals():
        core.check_fields(to=to, cc=cc, bcc=bcc, text=text or "")
    recipients = _deliverable(to), _deliverable(cc), _deliverable(bcc)
    limits = accounts.write_limits

    def message(connections: MailConnections) -> MessageInput:
        original = core.original_message(connections, original_ref)
        forwarded = core.forwarded_original(original, TIME_ZONE)
        sources = (
            [core.AttachmentSource(original_ref, part.part) for part in original.attachments]
            if include_attachments
            else []
        )
        return core.MessageInput(
            sender=core.from_identity(account, sender, original.addressed),
            to=recipients[0],
            cc=recipients[1],
            bcc=recipients[2],
            subject=_subject_cut(core.forward_subject(original.subject)),
            text=forwarded if text is None else f"{text}\n\n{forwarded}",
            attachments=core.attachments_from(connections, sources, core.attachment_cap(limits)),
        )

    return _dispatch(ctx, user_id, accounts, account, origin, _plain(message), on=used)
