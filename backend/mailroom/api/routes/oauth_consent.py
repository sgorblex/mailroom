"""The consent screen's calls: the pending authorization request ``/connect?request=<id>`` shows,
and the signed-in user's decision on it; and the user's connected apps (their grants), to list
and revoke. Same-origin requests with the session cookie only; a POST or DELETE is a state change
under ``/api/``, so a cross-site one is refused before it gets here."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from mailroom.api.deps import Context, CurrentUser, DbSession, SessionCookie
from mailroom.api.routes._app_access import (
    MAX_GROUPS,
    AccountChoice,
    GroupNameIn,
    Limits,
    ToolGroupOut,
    account_choices,
    approvable_groups,
    group_out,
    limits_reach,
)
from mailroom.errors import Refusal
from mailroom.oauth import consent
from mailroom.oauth.grants import user_grants
from mailroom.oauth.server import OAuthServer
from mailroom.oauth.tokens import revoke_grant, set_grant_groups, set_grant_reach
from mailroom_core.config import SendMode

if TYPE_CHECKING:
    from mailroom.mcp.groups import ToolGroup
    from mailroom.mcp.settings import McpSettings
    from mailroom.mcp.verifier import AuthMode

consent_router = APIRouter(prefix="/oauth/requests")
grants_router = APIRouter(prefix="/oauth/grants")

GrantId = Annotated[int, Path(ge=1, le=2**63 - 1)]
RequestId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]

_NO_SUCH_REQUEST = Refusal("not_found", "request_not_found", "No such authorization request")
_NO_SUCH_GRANT = Refusal("not_found", "grant_not_found", "No such connected app")
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
    groups: list[ToolGroupOut]
    """The tool groups the instance serves, for the user to choose from."""
    accounts: list[AccountChoice]
    """The user's accounts, for them to choose from."""
    expires_at: datetime


class Decision(Limits):
    decision: Literal["approve", "deny"]
    groups: list[GroupNameIn] = Field(default_factory=list, max_length=MAX_GROUPS)
    """With ``approve``, the tool groups approved: at least one, each served, with what it
    requires. The limits count with ``approve`` only."""


class GroupsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[GroupNameIn] = Field(max_length=MAX_GROUPS)


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


def _served(request: Request) -> tuple[ToolGroup, ...]:
    """The tool groups the instance serves: none with the MCP server off."""
    settings: McpSettings | None = getattr(request.app.state, "mcp_settings", None)
    return () if settings is None else settings.groups


@consent_router.get("/{request_id}")
def consent_request(
    request: Request,
    request_id: RequestId,
    response: Response,
    server: BuiltinOAuthServer,
    ctx: Context,
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
        groups=[group_out(group) for group in _served(request)],
        accounts=account_choices(ctx, user.id),
        expires_at=pending.expires_at,
    )


@consent_router.post("/{request_id}")
def decide(
    request: Request,
    request_id: RequestId,
    body: Decision,
    server: BuiltinOAuthServer,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
    session: SessionCookie = None,
) -> Decided:
    """Approve (a new connected app, for the tool groups and within the limits chosen) or deny;
    either way the request is used up. 400 ``groups_not_approvable`` or
    ``accounts_not_approvable`` for a choice that cannot be approved, before the
    request is touched; 409 ``sso_required`` as for the GET."""
    try:
        if body.decision == "approve":
            groups = approvable_groups(body.groups, _served(request))
            redirect_to = consent.approve(
                db,
                server.settings,
                request_id,
                user.id,
                tool_groups=groups,
                reach=limits_reach(ctx, user.id, body),
                session=session,
            )
        else:
            redirect_to = consent.deny(db, server.settings, request_id, user.id, session=session)
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
    groups: list[str]
    """The served tool groups it is approved for; the others are added since, or left out at
    approval."""
    account_ids: list[int] | None
    """The accounts it is narrowed to (none, when they were all removed); null for every one."""
    send_ceiling: SendMode | None


class ConnectedAppsOut(BaseModel):
    auth: Literal["builtin", "oidc", "external", "off"]
    """How MCP clients get tokens: ``oidc`` is the built-in server signing users in through SSO;
    ``external`` means the identity provider manages access, and the grants listed are left from
    the built-in server."""
    grants: list[ConnectedAppOut]
    groups: list[ToolGroupOut]
    """The tool groups the instance serves, which an app's can be chosen from; none unless the
    built-in server grants access (``builtin`` or ``oidc``)."""
    accounts: list[AccountChoice]
    """The user's accounts, which an app can be narrowed to."""


def _auth_mode(request: Request) -> AuthMode:
    settings: McpSettings | None = getattr(request.app.state, "mcp_settings", None)
    return "off" if settings is None else settings.auth


@grants_router.get("")
def connected_apps(
    request: Request, ctx: Context, db: DbSession, user: CurrentUser
) -> ConnectedAppsOut:
    """The user's grants, whatever the mode: ones left from an earlier mode can still be revoked."""
    served = _served(request)
    auth = _auth_mode(request)
    return ConnectedAppsOut(
        auth=auth,
        grants=[
            ConnectedAppOut(
                id=app.grant_id,
                client_name=app.client_name,
                client_host=_host(app.client_id) if app.client_kind == "cimd" else None,
                created_at=app.created_at,
                last_used_at=app.last_used_at,
                groups=[group.name for group in served if group.name in app.tool_groups],
                account_ids=app.reach.account_ids,
                send_ceiling=app.reach.ceiling,
            )
            for app in user_grants(db, user.id)
        ],
        groups=[group_out(group) for group in served] if auth in ("builtin", "oidc") else [],
        accounts=account_choices(ctx, user.id),
    )


@grants_router.put("/{grant_id}/groups", status_code=204)
def set_connected_app_groups(
    request: Request, grant_id: GrantId, body: GroupsIn, db: DbSession, user: CurrentUser
) -> None:
    """Make ``groups`` the served tool groups the app is approved for, adding or removing: its
    next call sees them. 400 ``groups_not_approvable`` as for a decision."""
    served = _served(request)
    groups = approvable_groups(body.groups, served)
    names = {group.name for group in served}
    if set_grant_groups(db, grant_id, user.id, groups, names) is None:
        raise _NO_SUCH_GRANT


@grants_router.put("/{grant_id}/limits", status_code=204)
def set_connected_app_limits(
    grant_id: GrantId, body: Limits, ctx: Context, db: DbSession, user: CurrentUser
) -> None:
    """Make these the app's limits, both of them: its next call sees them. 400
    ``accounts_not_approvable`` as for a decision."""
    if not set_grant_reach(db, grant_id, user.id, limits_reach(ctx, user.id, body)):
        raise _NO_SUCH_GRANT


@grants_router.delete("/{grant_id}", status_code=204)
def revoke_connected_app(grant_id: GrantId, db: DbSession, user: CurrentUser) -> None:
    """End the grant and its tokens: the app's next ``/mcp`` call is a 401."""
    if not revoke_grant(db, grant_id, user_id=user.id):
        raise _NO_SUCH_GRANT
