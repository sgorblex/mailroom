"""The consent screen's calls: the pending authorization request ``/connect?request=<id>`` shows,
and the signed-in user's decision on it; and the user's connected apps (their grants), to list
and revoke. Same-origin requests with the session cookie only; a POST or DELETE is a state change
under ``/api/``, so a cross-site one is refused before it gets here."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict

from mailroom.api.deps import CurrentUser, DbSession, SessionCookie
from mailroom.errors import Refusal
from mailroom.oauth import consent
from mailroom.oauth.grants import user_grants
from mailroom.oauth.server import OAuthServer
from mailroom.oauth.tokens import revoke_grant

if TYPE_CHECKING:
    from mailroom.mcp.settings import McpSettings
    from mailroom.mcp.verifier import AuthMode

consent_router = APIRouter(prefix="/oauth/requests")
grants_router = APIRouter(prefix="/oauth/grants")

GrantId = Annotated[int, Path(ge=1, le=2**63 - 1)]
RequestId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]

_NO_SUCH_REQUEST = Refusal("not_found", "request_not_found", "No such authorization request")
_SSO_REQUIRED = Refusal(
    "conflict", "sso_required", "Sign in through single sign-on to answer this request"
)


class ConsentRequestOut(BaseModel):
    client_name: str | None
    """What the client calls itself: its own claim, not checked."""
    client_host: str | None
    """For a client metadata document, the host serving it (an allowed one); else ``None``."""
    redirect_host: str
    """Where the browser goes back to, approved or not."""
    scopes: list[str]
    expires_at: datetime


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "deny"]


class Decided(BaseModel):
    redirect_to: str
    """The client's redirect URL, carrying the code or ``access_denied``: the screen goes there."""


def _oauth_server(request: Request) -> OAuthServer:
    """The built-in authorization server; with it off no request can exist, so every one is a
    404."""
    server: OAuthServer | None = getattr(request.app.state, "oauth_server", None)
    if server is None:
        raise _NO_SUCH_REQUEST
    return server


BuiltinOAuthServer = Annotated[OAuthServer, Depends(_oauth_server)]


def _host(url: str) -> str:
    return urlsplit(url).netloc


@consent_router.get("/{request_id}")
def consent_request(
    request_id: RequestId,
    response: Response,
    server: BuiltinOAuthServer,
    db: DbSession,
    user: CurrentUser,
    session: SessionCookie = None,
) -> ConsentRequestOut:
    """The request, claimed for this user on first sight; 404 when it is unknown, expired, or
    another user's. In ``oidc`` mode only the SSO callback claims it, for the session it starts:
    any other session gets 409 ``sso_required``."""
    try:
        pending = consent.claim_pending_authorization(
            db, request_id, user.id, require_sso=server.settings.sso_sign_in, session=session
        )
    except consent.SsoRequiredError:
        raise _SSO_REQUIRED from None
    if pending is None:
        raise _NO_SUCH_REQUEST
    response.headers["Cache-Control"] = "no-store"
    return ConsentRequestOut(
        client_name=pending.client_name,
        client_host=_host(pending.client_id) if pending.client_kind == "cimd" else None,
        redirect_host=_host(pending.redirect_origin),
        scopes=pending.scopes,
        expires_at=pending.expires_at,
    )


@consent_router.post("/{request_id}")
def decide(
    request_id: RequestId,
    body: Decision,
    server: BuiltinOAuthServer,
    db: DbSession,
    user: CurrentUser,
    session: SessionCookie = None,
) -> Decided:
    """Approve (a new connected app) or deny; either way the request is used up. 409
    ``sso_required`` as for the GET."""
    decide_with = consent.approve if body.decision == "approve" else consent.deny
    try:
        redirect_to = decide_with(db, server.settings, request_id, user.id, session=session)
    except consent.SsoRequiredError:
        raise _SSO_REQUIRED from None
    if redirect_to is None:
        raise _NO_SUCH_REQUEST
    return Decided(redirect_to=redirect_to)


class ConnectedAppOut(BaseModel):
    id: int
    client_name: str | None
    client_host: str | None
    """As on the consent screen: the host serving a metadata document, else ``None``."""
    created_at: datetime
    last_used_at: datetime | None


class ConnectedAppsOut(BaseModel):
    auth: Literal["builtin", "oidc", "external", "off"]
    """How MCP clients get tokens: ``oidc`` is the built-in server signing users in through SSO;
    ``external`` means the identity provider manages access, and the grants listed are left from
    the built-in server."""
    grants: list[ConnectedAppOut]


def _auth_mode(request: Request) -> AuthMode:
    settings: McpSettings | None = getattr(request.app.state, "mcp_settings", None)
    return "off" if settings is None else settings.auth


@grants_router.get("")
def connected_apps(request: Request, db: DbSession, user: CurrentUser) -> ConnectedAppsOut:
    """The user's grants, whatever the mode: ones left from an earlier mode can still be revoked."""
    return ConnectedAppsOut(
        auth=_auth_mode(request),
        grants=[
            ConnectedAppOut(
                id=app.grant_id,
                client_name=app.client_name,
                client_host=_host(app.client_id) if app.client_kind == "cimd" else None,
                created_at=app.created_at,
                last_used_at=app.last_used_at,
            )
            for app in user_grants(db, user.id)
        ],
    )


@grants_router.delete("/{grant_id}", status_code=204)
def revoke_connected_app(grant_id: GrantId, db: DbSession, user: CurrentUser) -> None:
    """End the grant and its tokens: the app's next ``/mcp`` call is a 401."""
    if not revoke_grant(db, grant_id, user_id=user.id):
        raise Refusal("not_found", "grant_not_found", "No such connected app")
