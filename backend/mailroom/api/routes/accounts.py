"""Each user's mail accounts: list, add, edit, remove, test the login and read the quota. Only the
signed-in user's own: another user's account id is a 404."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from mailroom.accounts import service
from mailroom.accounts.autoconfig import Autoconfig, AutoconfigAnswer, address_domain
from mailroom.accounts.service import ListedAccount, StoredAccount, StoredEndpoint, StoredIdentity
from mailroom.api.deps import AccountId, Context, CurrentUser, DbSession
from mailroom.errors import Refusal
from mailroom.mail import service as mail

if TYPE_CHECKING:
    from mailroom_core.mail import QuotaUsage
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

    name: str | None = Field(default=None, max_length=64)
    login: str = Field(max_length=320)
    imap: EndpointBody
    smtp: EndpointBody
    identities: list[IdentityBody] = Field(max_length=50)
    auto_approve: list[str] = Field(default=[], max_length=200)


class NewAccount(AccountFields):
    password: str = Field(min_length=1, max_length=MAX_SECRET_LENGTH)


class AccountUpdate(AccountFields):
    password: str | None = Field(default=None, min_length=1, max_length=MAX_SECRET_LENGTH)
    """Absent: keep the stored one."""


class LoginTest(BaseModel):
    ok: bool
    code: str | None = None
    message: str | None = None


class Usage(BaseModel):
    used: int
    limit: int

    @classmethod
    def of(cls, usage: QuotaUsage | None) -> Usage | None:
        return None if usage is None else cls(used=usage.used, limit=usage.limit)


class AccountQuota(BaseModel):
    """Both null when the server keeps no quota (no QUOTA) or limits neither."""

    account_id: int
    storage: Usage | None
    """In bytes."""
    messages: Usage | None


def _fields(body: AccountFields) -> dict[str, Json]:
    fields: dict[str, Json] = body.model_dump(mode="json", exclude={"password"}, exclude_none=True)
    return fields


@accounts_router.get("")
def accounts(ctx: Context, db: DbSession, user: CurrentUser) -> list[ListedAccount]:
    return service.listed_accounts(ctx, db, user.id)


@accounts_router.get("/autoconfig")
async def autoconfig(
    request: Request, user: CurrentUser, address: Annotated[str, Query(max_length=320)]
) -> AutoconfigAnswer:
    """Suggested servers for ``address``, or ``suggestion`` null; looked up, never stored.
    ``invalid_address`` (400) for no address, ``autoconfig_busy`` (429) past the user's limit."""
    domain = address_domain(address)
    if domain is None:
        msg = "Not an email address."
        raise Refusal("invalid", "invalid_address", msg)
    lookups: Autoconfig = request.app.state.autoconfig
    if not lookups.limit.allow(f"user:{user.id}"):
        msg = "Too many lookups: try again in a minute."
        raise Refusal("busy", "autoconfig_busy", msg)
    suggestion = await lookups.lookup(domain)
    logger.info(
        "account_autoconfig",
        extra={
            "event": "account_autoconfig",
            "user_id": user.id,
            "outcome": "none" if suggestion is None else suggestion.source,
        },
    )
    return AutoconfigAnswer(domain=domain, suggestion=suggestion)


@accounts_router.post("", status_code=201)
def add_account(body: NewAccount, ctx: Context, user: CurrentUser) -> ListedAccount:
    return service.add_account(ctx, user.id, _fields(body), body.password)


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
    failure = service.try_login(ctx, user.id, account_id)
    if failure is not None:
        code, message = failure
        _log_test(user.id, account_id, code)
        return LoginTest(ok=False, code=code, message=message)
    _log_test(user.id, account_id, "ok")
    return LoginTest(ok=True)


@accounts_router.get("/{account_id}/quota")
def account_quota(
    account_id: AccountId, request: Request, ctx: Context, user: CurrentUser
) -> AccountQuota:
    """The quota the account's INBOX falls under, as its server said it at most 10 minutes ago;
    a mailbox failure is the mail client's refusal."""
    found = mail.quota(ctx, user.id, account_id, request.app.state.quotas)
    return AccountQuota(
        account_id=account_id,
        storage=None if found is None else Usage.of(found.storage),
        messages=None if found is None else Usage.of(found.messages),
    )


def _log_test(user_id: int, account_id: int, outcome: str) -> None:
    logger.info(
        "account_tested",
        extra={
            "event": "account_tested",
            "user_id": user_id,
            "account_id": account_id,
            "outcome": outcome,
        },
    )
