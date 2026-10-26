"""The consent screen's calls: the pending authorization request ``/connect?request=<id>`` shows,
and the signed-in user's decision on it. Same-origin requests with the session cookie only; the
POST is a state change under ``/api/``, so a cross-site one is refused before it gets here."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict

from mailroom.api.deps import CurrentUser, DbSession
from mailroom.errors import Refusal
from mailroom.oauth import consent
from mailroom.oauth.server import OAuthServer

consent_router = APIRouter(prefix="/oauth/requests")

RequestId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]

_NO_SUCH_REQUEST = Refusal("not_found", "request_not_found", "No such authorization request")


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


@consent_router.get("/{request_id}", dependencies=[Depends(_oauth_server)])
def consent_request(
    request_id: RequestId, response: Response, db: DbSession, user: CurrentUser
) -> ConsentRequestOut:
    """The request, claimed for this user on first sight; 404 when it is unknown, expired, or
    another user's."""
    pending = consent.claim_pending_authorization(db, request_id, user.id)
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
) -> Decided:
    """Approve (a new connected app) or deny; either way the request is used up."""
    decide_with = consent.approve if body.decision == "approve" else consent.deny
    redirect_to = decide_with(db, server.settings, request_id, user.id)
    if redirect_to is None:
        raise _NO_SUCH_REQUEST
    return Decided(redirect_to=redirect_to)
