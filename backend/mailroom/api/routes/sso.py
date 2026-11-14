"""Web SSO's routes: whether it is on, its start and callback (browser navigations, so they answer
with redirects, or a page that moves on by itself), and each user's linked sign-ins."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Cookie, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from mailroom.api.csrf import is_cross_site
from mailroom.api.deps import CurrentUser, DbSession, SessionCookie
from mailroom.auth.oidc import OidcError
from mailroom.auth.sessions import (
    SESSION_COOKIE_NAME,
    create_session,
    end_session,
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
    Completed,
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
from mailroom.oauth import consent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.oauth.config import OAuthSettings
    from mailroom.oauth.server import OAuthServer

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


def _continue_to(location: str) -> HTMLResponse:
    """A page that goes on to ``location``, a safe path, by itself. The callback answers a
    sign-in with it rather than a redirect: the browser then reaches ``location`` from this site,
    not at the end of a chain through the provider, so the ``SameSite=Strict`` session cookie it
    sets goes along (Firefox and Safari withhold it from such a chain's first page)."""
    target = html.escape(location, quote=True)
    page = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0;url={target}"><title>Mailroom</title></head>'
        f'<body><a href="{target}">Continue</a></body></html>'
    )
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            # The callback's address carries the provider's code and state.
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


def _failed(failure: SsoError) -> RedirectResponse:
    response = _redirect(with_query(failure.return_to, sso_error=failure.code))
    response.delete_cookie(BINDING_COOKIE_NAME, **_binding_cookie_attributes())
    return response


def _oauth_settings(request: Request) -> OAuthSettings | None:
    """The built-in authorization server's settings when it signs its users in through SSO
    (``MAILROOM_MCP_AUTH=oidc``); else ``None``, and an ``mcp`` flow has nothing to sign in for."""
    server: OAuthServer | None = getattr(request.app.state, "oauth_server", None)
    if server is None or not server.settings.sso_sign_in:
        return None
    return server.settings


_REQUEST_ID_MAX = 128
_REQUEST_ID_PATTERN = r"^[A-Za-z0-9_-]+$"


def _consent_request(request: Request, return_to: str) -> str | None:
    """The authorization request ``return_to`` names when it is the consent screen's address and
    the built-in server signs its users in through SSO; else ``None``."""
    oauth = _oauth_settings(request)
    parts = urlsplit(return_to)
    found = parse_qs(parts.query).get("request", [])
    if oauth is None or parts.path != oauth.consent_path or len(found) != 1:
        return None
    authorization = found[0]
    if len(authorization) > _REQUEST_ID_MAX or not re.match(_REQUEST_ID_PATTERN, authorization):
        return None
    return authorization


def _claim(request: Request, db: Session, done: Completed, session: str) -> bool:
    """Claim the authorization request the flow came back to the consent screen of, for its user
    and ``session``, the one the callback signs the browser in with; ``False`` when there is none,
    or it is gone (or another user's)."""
    authorization = _consent_request(request, done.return_to)
    if authorization is None:
        return False
    if not consent.claim_via_sso(db, authorization, done.user.id, session):
        return False
    logger.info(
        "oauth_sso_signed_in",
        extra={"event": "oauth_sso_signed_in", "user_id": done.user.id},
    )
    return True


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
    authorization: Annotated[
        str | None,
        Query(alias="request", max_length=_REQUEST_ID_MAX, pattern=_REQUEST_ID_PATTERN),
    ] = None,
) -> RedirectResponse:
    """Send the browser to the provider. ``link`` links a sign-in to the signed-in user, so it
    needs their session, from this site (the cookie is ``SameSite=Strict`` anyway). ``mcp`` signs
    in for the authorization request ``request`` names, which the built-in authorization server
    sent the browser here with in ``oidc`` mode; it comes back to that request's consent screen,
    whatever ``return_to`` says. Anyone may call it, so each client starts at most
    ``STARTS_PER_MINUTE`` flows a minute (``busy``)."""
    oauth = _oauth_settings(request)
    if intent == "mcp":
        consent_path = oauth.consent_path if oauth is not None else "/"
        if oauth is None or authorization is None:
            return _failed(SsoError("state_invalid", consent_path))
        target = with_query(consent_path, request=authorization)
    else:
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
    if intent == "mcp" and not consent.is_live(db, authorization or ""):
        return _failed(SsoError("request_gone", target))
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
) -> Response:
    """Where the provider sends the browser back: signs in (or links) and goes to ``return_to``,
    or goes there with ``sso_error``. A sign-in goes on through :func:`_continue_to`."""
    sso = _sso(request)
    if sso is None:
        return _failed(SsoError("not_configured"))
    try:
        done = complete(db, sso, state=state, code=code, error=error, binding=binding)
    except SsoError as failure:
        logger.info("sso_refused", extra={"event": "sso_refused", "outcome": failure.code})
        return _failed(failure)
    response: Response
    if done.intent in ("login", "mcp"):
        response = _continue_to(done.return_to)
    else:
        response = _redirect(done.return_to)
    response.delete_cookie(BINDING_COOKIE_NAME, **_binding_cookie_attributes())
    if done.intent in ("login", "mcp"):
        session = create_session(db, done.user)
        if done.intent == "mcp" and not _claim(request, db, done, session):
            end_session(db, session)
            return _failed(SsoError("request_gone", done.return_to))
        if done.intent == "login":
            # Signed in from the sign-in screen a signed-out browser sees on a request's consent
            # screen: claim it too, if it is still to be had, so one pass is enough.
            _claim(request, db, done, session)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session,
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
