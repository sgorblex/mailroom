"""Each user's mail accounts: list, add, edit, remove, test the login, list the folders, and the
Run filters switch. Only the signed-in user's own: another user's account id is a 404."""

from __future__ import annotations

import logging
import ssl
from collections.abc import Sequence
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from imap_thingy.accounts import Account
from mailroom_core.config import AccountConfig
from mailroom_core.crypto import SecretsSubkey
from mailroom_core.store import AccountStore, Json, RevisionConflict, SettingsBusy
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from mailroom.accounts.app_data import forget_account, run_filters_by_account, set_run_filters
from mailroom.accounts.mail import MailFailure, check_login, list_folders, mailbox_account
from mailroom.accounts.store import (
    AccountError,
    AccountNotFound,
    accounts_with_password,
    load_accounts,
    password_account,
    save_account,
    user_store,
)
from mailroom.api.deps import CurrentUser, DbSession
from mailroom.api.refusals import mail_refusal, refused

logger = logging.getLogger(__name__)

accounts_router = APIRouter(prefix="/accounts")

# Long enough for an app password or an OAuth token pasted in.
MAX_SECRET_LENGTH = 4096


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


class EndpointBody(StoredEndpoint):
    host: str = Field(max_length=255)


class IdentityBody(StoredIdentity):
    address: str = Field(max_length=320)
    name: str | None = Field(default=None, max_length=200)


class AccountFields(StoredAccount):
    """An account as the user sends it: the same, bounded; mailroom-core's models check it."""

    login: str = Field(max_length=320)
    imap: EndpointBody
    smtp: EndpointBody
    identities: list[IdentityBody] = Field(max_length=50)
    auto_approve: list[str] = Field(default=[], max_length=200)


class NewAccount(AccountFields):
    id: str = Field(max_length=64)
    password: str = Field(min_length=1, max_length=MAX_SECRET_LENGTH)


class AccountUpdate(AccountFields):
    password: str | None = Field(default=None, min_length=1, max_length=MAX_SECRET_LENGTH)
    """Absent: keep the stored one."""


class ListedAccount(StoredAccount):
    """Never the password: only whether one is stored."""

    id: str
    has_password: bool
    run_filters: bool


class LoginTest(BaseModel):
    ok: bool
    code: str | None = None
    message: str | None = None


class Folder(BaseModel):
    path: list[str]
    """Segments, split on the server's delimiter: ``["INBOX"]``, ``["Archive", "2026"]``."""


class FilterSettings(BaseModel):
    run_filters: bool


AccountId = Annotated[str, Path(min_length=1, max_length=64)]


def _engine(request: Request) -> Engine:
    engine: Engine | None = request.app.state.database_engine
    if engine is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return engine


def _store(user: CurrentUser, engine: Annotated[Engine, Depends(_engine)]) -> AccountStore:
    return user_store(engine, user.id)


def _subkey(request: Request) -> SecretsSubkey | None:
    subkey: SecretsSubkey | None = getattr(request.app.state, "secrets_subkey", None)
    return subkey


Store = Annotated[AccountStore, Depends(_store)]
Subkey = Annotated[SecretsSubkey | None, Depends(_subkey)]


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="No such account")


def _require_subkey(subkey: SecretsSubkey | None) -> SecretsSubkey:
    if subkey is None:
        msg = "MAILROOM_SECRETS_KEY is not set: accounts cannot be added or used."
        raise refused(503, "secrets_key_missing", msg)
    return subkey


def _account_error(error: AccountError) -> HTTPException:
    if error.code == "secrets_key_missing":
        return refused(503, error.code, str(error))
    issues = [{"path": list(issue.path), "message": issue.message} for issue in error.issues]
    return HTTPException(
        status_code=400,
        detail={"code": error.code, "message": str(error), "issues": issues},
    )


def _store_refusal(error: RevisionConflict | SettingsBusy) -> HTTPException:
    if isinstance(error, RevisionConflict):
        msg = "The accounts changed meanwhile: reload and try again."
        return refused(409, "accounts_changed", msg)
    return refused(503, "accounts_busy", "The accounts are being saved elsewhere: try again.")


def _listed(account: AccountConfig, *, has_password: bool, run_filters: bool) -> ListedAccount:
    return ListedAccount.model_validate(
        {
            **account.model_dump(mode="json", exclude={"auth"}),
            "has_password": has_password,
            "run_filters": run_filters,
        }
    )


def _raw(fields: AccountFields, account_id: str) -> dict[str, Json]:
    dumped: dict[str, Json] = fields.model_dump(
        mode="json", exclude={"id", "password"}, exclude_none=True
    )
    return password_account(account_id, dumped)


def checked_account(store: AccountStore, account_id: str) -> AccountConfig:
    try:
        return load_accounts(store).account(account_id)
    except AccountNotFound:
        raise _not_found() from None


@accounts_router.get("")
def accounts(store: Store, db: DbSession, user: CurrentUser) -> list[ListedAccount]:
    passwords = accounts_with_password(store)
    run_filters = run_filters_by_account(db, user.id)
    return [
        _listed(
            account,
            has_password=account.id in passwords,
            run_filters=run_filters.get(account.id, False),
        )
        for account in load_accounts(store).settings.account
    ]


@accounts_router.post("", status_code=201)
def add_account(body: NewAccount, store: Store, subkey: Subkey, user: CurrentUser) -> ListedAccount:
    key = _require_subkey(subkey)
    try:
        account = save_account(store, key, _raw(body, body.id), body.password, new=True)
    except AccountError as exc:
        raise _account_error(exc) from None
    except (RevisionConflict, SettingsBusy) as exc:
        raise _store_refusal(exc) from None
    logger.info(
        "account_added",
        extra={"event": "account_added", "user_id": user.id, "account_id": account.id},
    )
    return _listed(account, has_password=True, run_filters=False)


@accounts_router.put("/{account_id}")
def change_account(
    account_id: AccountId,
    body: AccountUpdate,
    store: Store,
    subkey: Subkey,
    db: DbSession,
    user: CurrentUser,
) -> ListedAccount:
    key = subkey if body.password is None else _require_subkey(subkey)
    try:
        account = save_account(store, key, _raw(body, account_id), body.password, new=False)
    except AccountNotFound:
        raise _not_found() from None
    except AccountError as exc:
        raise _account_error(exc) from None
    except (RevisionConflict, SettingsBusy) as exc:
        raise _store_refusal(exc) from None
    logger.info(
        "account_changed",
        extra={
            "event": "account_changed",
            "user_id": user.id,
            "account_id": account_id,
            "password_changed": body.password is not None,
        },
    )
    return _listed(
        account,
        has_password=account_id in accounts_with_password(store),
        run_filters=run_filters_by_account(db, user.id).get(account_id, False),
    )


@accounts_router.delete("/{account_id}", status_code=204)
def delete_account(account_id: AccountId, store: Store, db: DbSession, user: CurrentUser) -> None:
    try:
        forget_account(store, db, user.id, account_id)
    except AccountNotFound:
        raise _not_found() from None
    except (RevisionConflict, SettingsBusy) as exc:
        raise _store_refusal(exc) from None
    logger.info(
        "account_removed",
        extra={"event": "account_removed", "user_id": user.id, "account_id": account_id},
    )


def _imap(
    store: AccountStore, subkey: SecretsSubkey | None, request: Request, account_id: str
) -> Account:
    """imap-thingy's account for one of the user's, password opened. Raises ``MailFailure``
    when there is no usable password or IMAP endpoint."""
    ssl_context: ssl.SSLContext = request.app.state.mail_ssl_context
    try:
        return mailbox_account(store, _require_subkey(subkey), ssl_context, account_id)
    except AccountNotFound:
        raise _not_found() from None


@accounts_router.post("/{account_id}/test")
def test_account(
    account_id: AccountId, request: Request, store: Store, subkey: Subkey, user: CurrentUser
) -> LoginTest:
    """Log in to IMAP and out again. A refused login is an answer, not an error: ``ok`` false."""
    try:
        check_login(_imap(store, subkey, request, account_id))
    except MailFailure as failure:
        logger.info(
            "account_tested",
            extra={
                "event": "account_tested",
                "user_id": user.id,
                "account_id": account_id,
                "outcome": failure.code,
            },
        )
        return LoginTest(ok=False, code=failure.code, message=failure.message)
    logger.info(
        "account_tested",
        extra={
            "event": "account_tested",
            "user_id": user.id,
            "account_id": account_id,
            "outcome": "ok",
        },
    )
    return LoginTest(ok=True)


@accounts_router.get("/{account_id}/folders")
def folders(account_id: AccountId, request: Request, store: Store, subkey: Subkey) -> list[Folder]:
    try:
        paths = list_folders(_imap(store, subkey, request, account_id))
    except MailFailure as failure:
        raise mail_refusal(failure) from None
    return [Folder(path=path) for path in paths]


@accounts_router.get("/{account_id}/filter-settings")
def filter_settings(
    account_id: AccountId, store: Store, db: DbSession, user: CurrentUser
) -> FilterSettings:
    checked_account(store, account_id)
    return FilterSettings(run_filters=run_filters_by_account(db, user.id).get(account_id, False))


@accounts_router.put("/{account_id}/filter-settings")
def change_filter_settings(
    account_id: AccountId,
    body: FilterSettings,
    store: Store,
    db: DbSession,
    user: CurrentUser,
) -> FilterSettings:
    checked_account(store, account_id)
    set_run_filters(db, user.id, account_id, run_filters=body.run_filters)
    logger.info(
        "run_filters_changed",
        extra={
            "event": "run_filters_changed",
            "user_id": user.id,
            "account_id": account_id,
            "run_filters": body.run_filters,
        },
    )
    return body
