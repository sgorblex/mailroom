"""A user's mail accounts as every front end handles them: listed, added, edited, removed, their
folders, and the Run filters switch. Only the user's own: another user's account id is
``not_found``. Raises ``mailroom.errors.Refusal``."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from mailroom_core.store import RevisionConflict, SettingsBusy
from pydantic import BaseModel

from mailroom.accounts import app_data
from mailroom.accounts.mail import MailFailure, list_folders, mailbox_account
from mailroom.accounts.store import (
    AccountError,
    AccountNotFound,
    accounts_with_password,
    load_accounts,
    password_account,
    save_account,
)
from mailroom.errors import SECRETS_KEY_MISSING, Refusal, mail_refusal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from imap_thingy.accounts import Account
    from mailroom_core.config import AccountConfig
    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.store import AccountStore, Json
    from sqlalchemy.orm import Session

    from mailroom.context import AppContext

logger = logging.getLogger(__name__)

Via = Literal["web", "mcp"]
"""The front end a change came through, as its log line names it."""


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
    run_filters: bool


def account_not_found() -> Refusal:
    return Refusal("not_found", "account_not_found", "No such account")


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


def _listed(account: AccountConfig, *, has_password: bool, run_filters: bool) -> ListedAccount:
    return ListedAccount.model_validate(
        {
            **account.model_dump(mode="json", exclude={"auth"}),
            "has_password": has_password,
            "run_filters": run_filters,
        }
    )


def _log(event: str, user_id: int, via: Via, **extra: object) -> None:
    logger.info(event, extra={"event": event, "user_id": user_id, "via": via, **extra})


def listed_accounts(ctx: AppContext, db: Session, user_id: int) -> list[ListedAccount]:
    store = ctx.store(user_id)
    passwords = accounts_with_password(store)
    run_filters = app_data.run_filters_by_account(db, user_id)
    return [
        _listed(
            account,
            has_password=account.id in passwords,
            run_filters=run_filters.get(account.id, False),
        )
        for account in load_accounts(store).settings.account
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
    try:
        account = save_account(ctx.store(user_id), key, raw, password, new=True)
    except AccountError as exc:
        raise _account_refusal(exc) from None
    except (RevisionConflict, SettingsBusy) as exc:
        raise _store_refusal(exc) from None
    _log("account_added", user_id, via, account_id=account.id)
    return _listed(account, has_password=True, run_filters=False)


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
    ctx.mail_pool.invalidate(user_id, account_id)
    _log(
        "account_changed",
        user_id,
        via,
        account_id=account_id,
        password_changed=password is not None,
    )
    return _listed(
        account,
        has_password=account_id in accounts_with_password(store),
        run_filters=app_data.run_filters_by_account(db, user_id).get(account_id, False),
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
    ctx.mail_pool.invalidate(user_id, account_id)
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


def account_folders(
    ctx: AppContext, user_id: int, account_id: str, *, seconds: float | None = None
) -> list[list[str]]:
    """The account's folders, each as its path segments (``["Archive", "2026"]``), within
    ``seconds`` when given (``mailroom.accounts.mail.list_folders``)."""
    try:
        return list_folders(imap_account(ctx, user_id, account_id), seconds=seconds)
    except MailFailure as failure:
        raise mail_refusal(failure.code, failure.message) from None


def run_filters(ctx: AppContext, db: Session, user_id: int, account_id: str) -> bool:
    """The account's Run filters switch (off until turned on)."""
    require_account(ctx.store(user_id), account_id)
    return app_data.run_filters_by_account(db, user_id).get(account_id, False)


def set_run_filters(
    ctx: AppContext,
    db: Session,
    user_id: int,
    account_id: str,
    *,
    on: bool,
    via: Via = "web",
) -> bool:
    """Turn the account's Run filters switch ``on`` or off; the new state."""
    require_account(ctx.store(user_id), account_id)
    app_data.set_run_filters(db, user_id, account_id, run_filters=on)
    _log("run_filters_changed", user_id, via, account_id=account_id, run_filters=on)
    return on
