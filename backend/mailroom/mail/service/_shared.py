"""What more than one of the mail service's modules uses: the bounds every front end puts on what
it takes (``MAX_FOLDER``, ``MAX_CURSOR``, ``MAX_TERM``, ``MAX_REF_LENGTH``); the user's accounts as
one call finds them (``_accounts``) and the checks on them (``unknown_account``, ``_usable``); the
checks on the refs that name them (``_message_ref``, ``_ref_accounts``, ``unknown_message``); the
runners of a call on their connections (``_run``, ``_call``, logged); and ``_Stoppable``, the
connections of a writing call that refuse every step once its deadline has passed."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mailroom.accounts.service import account_not_found, attachment_limit_mb, require_subkey
from mailroom.accounts.store import account_password, load_accounts
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail.connections import PASSWORD_MISSING, PASSWORD_MISSING_TEXT, PasswordMissing
from mailroom.mail.pool import DeadlineExceeded
from mailroom_core import mail as core
from mailroom_core.mail import MESSAGES, MailError

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Sequence

    from imapclient import IMAPClient

    from mailroom.accounts.service import Via
    from mailroom.accounts.store import UserAccounts
    from mailroom.context import AppContext
    from mailroom_core.config import AccountConfig, Limits
    from mailroom_core.mail import (
        Folder,
        MailboxContext,
        MailConnections,
        MessageRef,
    )

logger = logging.getLogger(__name__)

MAX_FOLDER: Final = 1000
MAX_CURSOR: Final = 65536
"""A cursor holds a position per folder still being read: a search over every folder of
several accounts makes long ones (mailroom-core checks their structure)."""
MAX_TERM: Final = 500
"""The bounds every front end puts on what it takes (a folder path, a cursor, a search term)."""
MAX_REF_LENGTH: Final = 4096
"""The longest ref a front end takes (a ref holds its folder's path)."""
_FAILED_TEXT: Final = "Reading the mail failed unexpectedly."


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
