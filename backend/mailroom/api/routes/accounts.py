"""Each user's mail accounts: list, add, edit, remove and test the login. Only the signed-in user's
own: another user's account id is a 404."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel, Field

from mailroom.accounts import service
from mailroom.accounts.mail import MailFailure, check_login
from mailroom.accounts.service import ListedAccount, StoredAccount, StoredEndpoint, StoredIdentity
from mailroom.api.deps import Context, CurrentUser, DbSession

if TYPE_CHECKING:
    from mailroom_core.store import Json

logger = logging.getLogger(__name__)

accounts_router = APIRouter(prefix="/accounts")

# Long enough for an app password or an OAuth token pasted in.
MAX_SECRET_LENGTH = 4096


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


class LoginTest(BaseModel):
    ok: bool
    code: str | None = None
    message: str | None = None


AccountId = Annotated[str, Path(min_length=1, max_length=64)]


def _fields(body: AccountFields) -> dict[str, Json]:
    fields: dict[str, Json] = body.model_dump(
        mode="json", exclude={"id", "password"}, exclude_none=True
    )
    return fields


@accounts_router.get("")
def accounts(ctx: Context, db: DbSession, user: CurrentUser) -> list[ListedAccount]:
    return service.listed_accounts(ctx, db, user.id)


@accounts_router.post("", status_code=201)
def add_account(body: NewAccount, ctx: Context, user: CurrentUser) -> ListedAccount:
    return service.add_account(ctx, user.id, body.id, _fields(body), body.password)


@accounts_router.put("/{account_id}")
def change_account(
    account_id: AccountId, body: AccountUpdate, ctx: Context, db: DbSession, user: CurrentUser
) -> ListedAccount:
    return service.change_account(ctx, db, user.id, account_id, _fields(body), body.password)


@accounts_router.delete("/{account_id}", status_code=204)
def delete_account(account_id: AccountId, ctx: Context, db: DbSession, user: CurrentUser) -> None:
    service.remove_account(ctx, db, user.id, account_id)


@accounts_router.post("/{account_id}/test")
def test_account(account_id: AccountId, ctx: Context, user: CurrentUser) -> LoginTest:
    """Log in to IMAP and out again. A refused login is an answer, not an error: ``ok`` false."""
    try:
        check_login(service.imap_account(ctx, user.id, account_id))
    except MailFailure as failure:
        _log_test(user.id, account_id, failure.code)
        return LoginTest(ok=False, code=failure.code, message=failure.message)
    _log_test(user.id, account_id, "ok")
    return LoginTest(ok=True)


def _log_test(user_id: int, account_id: str, outcome: str) -> None:
    logger.info(
        "account_tested",
        extra={
            "event": "account_tested",
            "user_id": user_id,
            "account_id": account_id,
            "outcome": outcome,
        },
    )
