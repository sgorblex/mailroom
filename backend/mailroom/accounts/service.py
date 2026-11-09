"""A user's mail accounts as every front end handles them: listed, added, edited and removed. Only
the user's own: another user's account id is ``not_found``. Raises ``mailroom.errors.Refusal``."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, Field

from mailroom.accounts import app_data
from mailroom.accounts.mail import mailbox_account
from mailroom.accounts.store import (
    AccountError,
    AccountNotFound,
    accounts_with_password,
    load_accounts,
    password_account,
    save_account,
)
from mailroom.errors import SECRETS_KEY_MISSING, Refusal
from mailroom_core.store import RevisionConflict, SettingsBusy

if TYPE_CHECKING:
    from collections.abc import Mapping

    from imap_thingy.accounts import Account
    from sqlalchemy.orm import Session

    from mailroom.context import AppContext
    from mailroom_core.config import AccountConfig, Limits
    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.store import AccountStore, Json

logger = logging.getLogger(__name__)

Via = Literal["web", "mcp"]
"""The front end a change came through, as its log line names it."""
ATTACHMENT_MAX_MB: Final = 25.0
"""The most a written message's attachments may total, whatever the user's limit: the upload
route's body limit (``client_max_body_size`` in ``web/nginx-default.conf``, and the host's proxy)
is fixed and must hold them."""


class StoredEndpoint(BaseModel):
    host: str
    port: int
    tls: Literal["implicit", "starttls"]


class StoredIdentity(BaseModel):
    address: str
    name: str | None = None
    default: bool = False


class StoredAccount(BaseModel):
    """What mailroom-core keeps of an account but its id and password, as listed: no limits, so
    an account written by mailroom-mcp or imported lists whatever its sizes."""

    login: str
    send_mode: Literal["off", "drafts", "approval", "direct"]
    imap: StoredEndpoint
    smtp: StoredEndpoint
    identities: Sequence[StoredIdentity]
    auto_approve: list[str] = []


class ListedAccount(StoredAccount):
    """Never the password: only whether one is stored."""

    id: str
    has_password: bool
    run_filters: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    """None, and left out, where the instance does not run filters."""
    attachment_max_mb: float
    """The user's limit on a written message's attachments, at most ``ATTACHMENT_MAX_MB``."""
    max_recipients: int
    """The user's limit on To, Cc and Bcc together."""


def attachment_limit_mb(limits: Limits) -> float:
    return min(limits.attachment_max_mb, ATTACHMENT_MAX_MB)


def account_not_found(*, coded: bool = False) -> Refusal:
    """An account id that is not the user's, the same for every front end and service (the mail
    routes keep the code: ``coded``)."""
    return Refusal("not_found", "not_found", "No such account.", coded=coded)


def require_subkey(subkey: SecretsSubkey | None) -> SecretsSubkey:
    if subkey is None:
        msg = "MAILROOM_SECRETS_KEY is not set: accounts cannot be added or used."
        raise Refusal("unavailable", SECRETS_KEY_MISSING, msg)
    return subkey


def require_account(store: AccountStore, account_id: str) -> AccountConfig:
    """The store user's account ``account_id``, or ``not_found``."""
    try:
        return load_accounts(store).account(account_id)
    except AccountNotFound:
        raise account_not_found() from None


def _account_refusal(error: AccountError) -> Refusal:
    if error.code == SECRETS_KEY_MISSING:
        return Refusal("unavailable", error.code, str(error))
    issues = [{"path": list(issue.path), "message": issue.message} for issue in error.issues]
    return Refusal("invalid", error.code, str(error), issues=issues)


def _store_refusal(error: RevisionConflict | SettingsBusy) -> Refusal:
    if isinstance(error, RevisionConflict):
        msg = "The accounts changed meanwhile: reload and try again."
        return Refusal("conflict", "accounts_changed", msg)
    msg = "The accounts are being saved elsewhere: try again."
    return Refusal("unavailable", "accounts_busy", msg)


def _listed(
    account: AccountConfig, limits: Limits, *, has_password: bool, run_filters: bool | None
) -> ListedAccount:
    return ListedAccount.model_validate(
        {
            **account.model_dump(mode="json", exclude={"auth"}),
            "has_password": has_password,
            "run_filters": run_filters,
            "attachment_max_mb": attachment_limit_mb(limits),
            "max_recipients": limits.max_recipients,
        }
    )


def _log(event: str, user_id: int, via: Via, **extra: object) -> None:
    logger.info(event, extra={"event": event, "user_id": user_id, "via": via, **extra})


def _run_filters_by_account(ctx: AppContext, db: Session, user_id: int) -> dict[str, bool] | None:
    """Each account's Run filters switch by id, or None where the instance does not run
    filters."""
    if not ctx.features.filters:
        return None
    return app_data.run_filters_by_account(db, user_id)


def _switch(by_account: dict[str, bool] | None, account_id: str) -> bool | None:
    """``account_id``'s Run filters switch from ``by_account``, off when it has no row; None
    where the instance does not run filters."""
    return None if by_account is None else by_account.get(account_id, False)


def listed_accounts(ctx: AppContext, db: Session, user_id: int) -> list[ListedAccount]:
    store = ctx.store(user_id)
    passwords = accounts_with_password(store)
    by_account = _run_filters_by_account(ctx, db, user_id)
    settings = load_accounts(store).settings
    return [
        _listed(
            account,
            settings.limits,
            has_password=account.id in passwords,
            run_filters=_switch(by_account, account.id),
        )
        for account in settings.account
    ]


def add_account(
    ctx: AppContext,
    user_id: int,
    account_id: str,
    fields: Mapping[str, Json],
    password: str,
    *,
    via: Via = "web",
) -> ListedAccount:
    """Add ``account_id`` with ``fields`` (mailroom-core's account but its id and auth) and its
    password, sealed."""
    key = require_subkey(ctx.subkey)
    raw = password_account(account_id, fields)
    store = ctx.store(user_id)
    try:
        account = save_account(store, key, raw, password, new=True)
    except AccountError as exc:
        raise _account_refusal(exc) from None
    except (RevisionConflict, SettingsBusy) as exc:
        raise _store_refusal(exc) from None
    _log("account_added", user_id, via, account_id=account.id)
    limits = load_accounts(store).settings.limits
    run_filters = False if ctx.features.filters else None
    return _listed(account, limits, has_password=True, run_filters=run_filters)


def change_account(
    ctx: AppContext,
    db: Session,
    user_id: int,
    account_id: str,
    fields: Mapping[str, Json],
    password: str | None,
    *,
    via: Via = "web",
) -> ListedAccount:
    """Replace ``account_id``'s ``fields``; ``password`` None keeps the stored one."""
    key = ctx.subkey if password is None else require_subkey(ctx.subkey)
    store = ctx.store(user_id)
    raw = password_account(account_id, fields)
    try:
        account = save_account(store, key, raw, password, new=False)
    except AccountNotFound:
        raise account_not_found() from None
    except AccountError as exc:
        raise _account_refusal(exc) from None
    except (RevisionConflict, SettingsBusy) as exc:
        raise _store_refusal(exc) from None
    ctx.drop_mail_connections(user_id, account_id)
    _log(
        "account_changed",
        user_id,
        via,
        account_id=account_id,
        password_changed=password is not None,
    )
    by_account = _run_filters_by_account(ctx, db, user_id)
    return _listed(
        account,
        load_accounts(store).settings.limits,
        has_password=account_id in accounts_with_password(store),
        run_filters=_switch(by_account, account_id),
    )


def remove_account(
    ctx: AppContext, db: Session, user_id: int, account_id: str, *, via: Via = "web"
) -> None:
    """Remove the account with its rule sets and the app's rows about it."""
    try:
        app_data.forget_account(ctx.store(user_id), db, user_id, account_id)
    except AccountNotFound:
        raise account_not_found() from None
    except (RevisionConflict, SettingsBusy) as exc:
        raise _store_refusal(exc) from None
    ctx.drop_mail_connections(user_id, account_id)
    _log("account_removed", user_id, via, account_id=account_id)


def imap_account(ctx: AppContext, user_id: int, account_id: str) -> Account:
    """imap-thingy's account for one of the user's, password opened. Refuses ``not_found`` and
    without the secrets key; raises ``MailFailure`` when there is no usable password or IMAP
    endpoint."""
    key = require_subkey(ctx.subkey)
    try:
        return mailbox_account(ctx.store(user_id), key, ctx.ssl_context, account_id)
    except AccountNotFound:
        raise account_not_found() from None
