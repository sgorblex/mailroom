"""Each user's mail accounts: list, add, edit, remove, test the login, read the quota and the
servers' keywords, keep each identity's signature and the account's vacation reply. Only the
signed-in user's own: another user's account id is a 404."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from mailroom.accounts import service
from mailroom.accounts.autoconfig import Autoconfig, AutoconfigAnswer, address_domain
from mailroom.accounts.service import (
    AGENT_ACCOUNT_FIELDS,
    ListedAccount,
    StoredAccount,
    StoredEndpoint,
    StoredIdentity,
)
from mailroom.api.deps import AccountId, Context, CurrentUser, DbSession
from mailroom.api.token_access import ACCOUNT_GROUPS, open_to_tokens
from mailroom.errors import Refusal
from mailroom.mail import service as mail
from mailroom.mail import signatures, vacation

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
    authserv_id: str | None = Field(default=None, max_length=253)
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


class AccountKeywords(BaseModel):
    account_id: int
    keywords: bool | None
    """Whether the account's server keeps keywords, so messages can be marked To sort (never on
    Gmail); null when not known: it cannot log in, failed lately or did not answer in time."""


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


class IdentitySignature(BaseModel):
    """Both empty for an identity without one."""

    address: str
    html: str
    """Sanitised as an outgoing message's HTML is, without images."""
    text: str
    """``html`` as a message's text part has it."""


class SignatureChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(max_length=320)
    html: str = Field(max_length=signatures.MAX_SIGNATURE_CHARS)
    """Without text: the signature is removed."""


class VacationReply(BaseModel):
    """An account's vacation reply; off, with an empty subject and text, until set."""

    enabled: bool
    starts_on: date | None
    ends_on: date | None
    """The first and last day it answers, in the user's time zone; null: open."""
    subject: str
    html: str
    """Sanitised as an outgoing message's HTML is, without images."""
    text: str
    """``html`` as the reply's text part has it."""
    identity: str | None
    """The identity it goes from; null: the one each message was addressed to."""
    answering: bool
    """Whether it answers today: on, and today is one of its days."""
    indexed: bool
    """Whether new mail reaches it: replies are queued from the index's sync of INBOX (on Gmail,
    All Mail), so that folder must be on the index and synced."""


class VacationChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    starts_on: date | None = None
    ends_on: date | None = None
    subject: str = Field(default="", max_length=vacation.MAX_SUBJECT_CHARS)
    html: str = Field(default="", max_length=vacation.MAX_VACATION_CHARS)
    identity: str | None = Field(default=None, max_length=320)


def _vacation_reply(
    settings: vacation.VacationSettings, today: date, *, indexed: bool
) -> VacationReply:
    return VacationReply(
        enabled=settings.enabled,
        starts_on=settings.starts_on,
        ends_on=settings.ends_on,
        subject=settings.subject,
        html=settings.html,
        text=settings.text,
        identity=settings.identity,
        answering=vacation.answers_on(settings, today),
        indexed=indexed,
    )


def _fields(body: AccountFields) -> dict[str, Json]:
    fields: dict[str, Json] = body.model_dump(mode="json", exclude={"password"}, exclude_none=True)
    return fields


@accounts_router.get("", response_model=list[ListedAccount])
@open_to_tokens(*ACCOUNT_GROUPS)
def accounts(ctx: Context, db: DbSession, user: CurrentUser) -> list[ListedAccount] | JSONResponse:
    """The user's accounts, without logging in to any of them: ``keywords`` is null, asked of the
    servers by ``/accounts/keywords``. A token gets what MCP's ``accounts_list`` gives of each."""
    listed = service.listed_accounts(ctx, db, user.id)
    if ctx.origin.agent:
        return JSONResponse(
            [a.model_dump(mode="json", include=AGENT_ACCOUNT_FIELDS) for a in listed]
        )
    return listed


@accounts_router.get("/keywords")
@open_to_tokens(*ACCOUNT_GROUPS)
def accounts_keywords(ctx: Context, user: CurrentUser) -> list[AccountKeywords]:
    """Whether each of the user's accounts keeps keywords, in the accounts' order: asked of its
    server (kept while the user's connections last), a few seconds at most."""
    found = mail.account_keywords(ctx, user.id)
    return [AccountKeywords(account_id=account, keywords=kept) for account, kept in found.items()]


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


@accounts_router.get("/{account_id}/signatures")
def account_signatures(
    account_id: AccountId, ctx: Context, db: DbSession, user: CurrentUser
) -> list[IdentitySignature]:
    """Each of the account's identities, in its order, with its signature."""
    account = service.require_account(ctx, user.id, account_id)
    kept = signatures.account_signatures(db, user.id, account_id)
    listed = []
    for identity in account.identities:
        signature = kept.get(identity.address.lower())
        html, text = ("", "") if signature is None else (signature.html, signature.text)
        listed.append(IdentitySignature(address=identity.address, html=html, text=text))
    return listed


@accounts_router.put("/{account_id}/signatures")
def change_signature(
    account_id: AccountId,
    body: SignatureChange,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
) -> IdentitySignature:
    """The identity's signature, sanitised; one without text removes it. ``identity_not_found``
    (404) for an address that is none of the account's identities."""
    account = service.require_account(ctx, user.id, account_id)
    identity = next(
        (i for i in account.identities if i.address.lower() == body.address.lower()), None
    )
    if identity is None:
        msg = "No such identity on this account."
        raise Refusal("not_found", "identity_not_found", msg, coded=True)
    signature = signatures.set_signature(db, user.id, account_id, identity.address, body.html)
    logger.info(
        "signature_changed",
        extra={
            "event": "signature_changed",
            "user_id": user.id,
            "account_id": account_id,
            "outcome": "removed" if signature is None else "kept",
        },
    )
    if signature is None:
        return IdentitySignature(address=identity.address, html="", text="")
    return IdentitySignature(address=identity.address, html=signature.html, text=signature.text)


@accounts_router.get("/{account_id}/vacation")
def account_vacation(
    account_id: AccountId, ctx: Context, db: DbSession, user: CurrentUser
) -> VacationReply:
    """The account's vacation reply."""
    service.require_account(ctx, user.id, account_id)
    settings = vacation.vacation_of(db, user.id, account_id)
    return _vacation_reply(
        settings,
        vacation.today_of(db, user.id),
        indexed=vacation.inbox_indexed(db, user.id, account_id),
    )


@accounts_router.put("/{account_id}/vacation")
def change_vacation(
    account_id: AccountId,
    body: VacationChange,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
) -> VacationReply:
    """Keep the account's vacation reply, its HTML sanitised. ``invalid_vacation`` (400) for an
    identity that is none of the account's, a last day before the first, or one switched on
    without a subject or a text."""
    account = service.require_account(ctx, user.id, account_id)
    settings = vacation.set_vacation(
        db,
        user.id,
        account,
        vacation.VacationSettings(
            enabled=body.enabled,
            starts_on=body.starts_on,
            ends_on=body.ends_on,
            subject=body.subject,
            html=body.html,
            identity=body.identity,
        ),
    )
    logger.info(
        "vacation_changed",
        extra={
            "event": "vacation_changed",
            "user_id": user.id,
            "account_id": account_id,
            "enabled": settings.enabled,
        },
    )
    return _vacation_reply(
        settings,
        vacation.today_of(db, user.id),
        indexed=vacation.inbox_indexed(db, user.id, account_id),
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
