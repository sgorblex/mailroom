"""Web SSO's routes: whether it is on, its start and callback (browser navigations, so they answer
with redirects), and each user's linked sign-ins."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from mailroom.api.csrf import is_cross_site
from mailroom.api.deps import CurrentUser, DbSession, SessionCookie
from mailroom.auth.oidc import OidcError
from mailroom.auth.sessions import (
    SESSION_COOKIE_NAME,
    create_session,
    session_cookie_attributes,
    session_ttl,
    session_user,
)
from mailroom.auth.sso import (
    BINDING_COOKIE_NAME,
    BINDING_COOKIE_PATH,
    MAX_CODE_LENGTH,
    MAX_RETURN_TO_LENGTH,
    MAX_STATE_LENGTH,
    STATE_TTL,
    Sso,
    SsoError,
    begin,
    complete,
    safe_return_to,
    unlink,
    user_identities,
    with_query,
)
from mailroom.auth.throttle import RateLimit, client_key
from mailroom.errors import Refusal
from mailroom.models import ExternalIdentity, Intent

logger = logging.getLogger(__name__)

sso_router = APIRouter(prefix="/auth/oidc")
identities_router = APIRouter(prefix="/me/identities")


class SsoConfig(BaseModel):
    enabled: bool
    name: str | None
    """The sign-in button's label, when SSO is on."""


def _sso(request: Request) -> Sso | None:
    sso: Sso | None = getattr(request.app.state, "sso", None)
    return sso


def _binding_cookie_attributes() -> dict[str, Any]:
    # Lax, not Strict: it has to come back on the provider's cross-site redirect to the callback.
    return {**session_cookie_attributes(), "samesite": "lax", "path": BINDING_COOKIE_PATH}


def _redirect(location: str) -> RedirectResponse:
    response = RedirectResponse(location, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


def _failed(failure: SsoError) -> RedirectResponse:
    response = _redirect(with_query(failure.return_to, sso_error=failure.code))
    response.delete_cookie(BINDING_COOKIE_NAME, **_binding_cookie_attributes())
    return response


@sso_router.get("/config")
def sso_config(request: Request) -> SsoConfig:
    sso = _sso(request)
    return SsoConfig(enabled=sso is not None, name=sso.settings.name if sso else None)


@sso_router.get("/start")
def sso_start(
    request: Request,
    db: DbSession,
    session_cookie: SessionCookie = None,
    intent: Intent = "login",
    return_to: Annotated[str | None, Query(max_length=MAX_RETURN_TO_LENGTH)] = None,
) -> RedirectResponse:
    """Send the browser to the provider. ``link`` links a sign-in to the signed-in user, so it
    needs their session, from this site (the cookie is ``SameSite=Strict`` anyway). Anyone may
    call it, so each client starts at most ``STARTS_PER_MINUTE`` flows a minute (``busy``)."""
    target = safe_return_to(return_to, "/profile" if intent == "link" else "/")
    sso = _sso(request)
    if sso is None:
        return _failed(SsoError("not_configured", target))
    client = client_key(request)
    limit: RateLimit = request.app.state.sso_start_limit
    if not limit.allow(client):
        logger.info("sso_refused", extra={"event": "sso_refused", "outcome": "busy"})
        return _failed(SsoError("busy", target))
    link_user_id: int | None = None
    if intent == "link":
        user = session_user(db, session_cookie)
        if user is None or is_cross_site(request):
            return _failed(SsoError("signed_out", target))
        link_user_id = user.id
    try:
        started = begin(
            db, sso, intent=intent, return_to=target, client=client, link_user_id=link_user_id
        )
    except OidcError as exc:
        logger.warning(
            "sso_failed: %s", exc.message, extra={"event": "sso_failed", "outcome": exc.code}
        )
        return _failed(SsoError("unavailable", target))
    except SsoError as failure:
        logger.info("sso_refused", extra={"event": "sso_refused", "outcome": failure.code})
        return _failed(failure)
    response = _redirect(started.authorize_url)
    response.set_cookie(
        BINDING_COOKIE_NAME,
        started.binding,
        max_age=int(STATE_TTL.total_seconds()),
        **_binding_cookie_attributes(),
    )
    return response


@sso_router.get("/callback")
def sso_callback(
    request: Request,
    db: DbSession,
    state: Annotated[str | None, Query(max_length=MAX_STATE_LENGTH)] = None,
    code: Annotated[str | None, Query(max_length=MAX_CODE_LENGTH)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
    binding: Annotated[str | None, Cookie(alias=BINDING_COOKIE_NAME)] = None,
) -> RedirectResponse:
    """Where the provider sends the browser back: signs in (or links) and goes to ``return_to``,
    or goes there with ``sso_error``."""
    sso = _sso(request)
    if sso is None:
        return _failed(SsoError("not_configured"))
    try:
        done = complete(db, sso, state=state, code=code, error=error, binding=binding)
    except SsoError as failure:
        logger.info("sso_refused", extra={"event": "sso_refused", "outcome": failure.code})
        return _failed(failure)
    response = _redirect(done.return_to)
    response.delete_cookie(BINDING_COOKIE_NAME, **_binding_cookie_attributes())
    if done.intent == "login":
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=create_session(db, done.user),
            max_age=int(session_ttl().total_seconds()),
            **session_cookie_attributes(),
        )
        logger.info(
            "login_succeeded",
            extra={"event": "login_succeeded", "user_id": done.user.id, "via": "sso"},
        )
    return response


class IdentityOut(BaseModel):
    id: int
    issuer: str
    subject: str
    name: str | None
    """The provider's label when it is the one SSO is configured with."""
    created_at: datetime
    last_used_at: datetime | None

    @classmethod
    def of(cls, identity: ExternalIdentity, sso: Sso | None) -> IdentityOut:
        name = None
        if sso is not None and identity.issuer.rstrip("/") == sso.settings.issuer.rstrip("/"):
            name = sso.settings.name
        return cls(
            id=identity.id,
            issuer=identity.issuer,
            subject=identity.subject,
            name=name,
            created_at=identity.created_at,
            last_used_at=identity.last_used_at,
        )


@identities_router.get("")
def list_identities(request: Request, db: DbSession, user: CurrentUser) -> list[IdentityOut]:
    sso = _sso(request)
    return [IdentityOut.of(identity, sso) for identity in user_identities(db, user.id)]


@identities_router.delete("/{identity_id}", status_code=204)
def unlink_identity(identity_id: int, db: DbSession, user: CurrentUser) -> None:
    if not unlink(db, user.id, identity_id):
        raise Refusal("not_found", "identity_not_found", "No such linked sign-in")
