"""Pending authorization requests and the user's decision on them.

``/oauth/authorize`` stores the request and sends the browser to the web app's consent screen,
``/connect?request=<id>``. The screen, signed in, calls :func:`claim_pending_authorization` to
show it, then :func:`approve` or :func:`deny`, and sends the browser to the URL they return. The
first user to open a request claims it: nobody else can see or decide it afterwards.

When the server signs users in through SSO first (``MAILROOM_MCP_AUTH=oidc``), the SSO callback
claims the request with :func:`claim_via_sso` for the session it signs the browser in with, and
the screen's calls pass ``require_sso`` and their session: any other session, the same user's
included, raises :class:`SsoRequiredError` and claims nothing.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from mcp.server.auth.provider import AuthorizationParams, construct_redirect_uri
from sqlalchemy import delete, select

from mailroom.oauth.config import REQUEST_TTL
from mailroom.oauth.models import OAuthAuthorizationRequest, OAuthClient
from mailroom.oauth.tokens import create_grant_with_code, new_secret, secret_hash

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.oauth.config import OAuthSettings

logger = logging.getLogger(__name__)

_CLAIMED_BY = "claimed_by"
_SSO_SESSION = "sso_session"
"""The SHA-256 of the session the SSO callback claimed the request for: only that browser, signed
in through the provider for this request, may answer it."""


class SsoRequiredError(Exception):
    """The request may only be shown or decided after signing in through SSO for it."""


@dataclass(frozen=True, slots=True)
class PendingAuthorization:
    """What the consent screen shows."""

    client_id: str
    client_name: str | None
    client_kind: str
    """``dcr`` (registered by the client itself) or ``cimd`` (a document on an allowed host)."""
    redirect_origin: str
    """Where the browser goes back to: ``scheme://host[:port]``."""
    scopes: list[str]
    expires_at: datetime


def store_request(db: Session, client_id: str, params: AuthorizationParams) -> str:
    """Store ``params`` for the consent screen and return the request's raw id."""
    now = datetime.now(UTC)
    db.execute(delete(OAuthAuthorizationRequest).where(OAuthAuthorizationRequest.expires_at <= now))
    request_id = new_secret()
    db.add(
        OAuthAuthorizationRequest(
            id_hash=secret_hash(request_id),
            client_id=client_id,
            params=params.model_dump(mode="json"),
            expires_at=now + REQUEST_TTL,
        )
    )
    db.commit()
    return request_id


def _live_request(
    db: Session,
    request_id: str,
    user_id: int,
    *,
    require_sso: bool = False,
    session: str | None = None,
) -> tuple[OAuthAuthorizationRequest, OAuthClient] | None:
    """The request and its client, locked; ``None`` when it is unknown, expired, or another
    user's. Raises ``SsoRequiredError`` (rolled back) when ``require_sso`` and the SSO callback
    did not claim it for ``session``, the caller's raw session token."""
    found = db.execute(
        select(OAuthAuthorizationRequest, OAuthClient)
        .join(OAuthClient, OAuthClient.client_id == OAuthAuthorizationRequest.client_id)
        .where(
            OAuthAuthorizationRequest.id_hash == secret_hash(request_id),
            OAuthAuthorizationRequest.expires_at > datetime.now(UTC),
        )
        .with_for_update(of=OAuthAuthorizationRequest)
    ).one_or_none()
    if found is None:
        return None
    request, client = found
    claimed_by = request.params.get(_CLAIMED_BY)
    if claimed_by is not None and claimed_by != user_id:
        return None
    if require_sso:
        claimed_for = request.params.get(_SSO_SESSION)
        if not (
            isinstance(claimed_for, str)
            and session is not None
            and hmac.compare_digest(claimed_for, secret_hash(session))
        ):
            db.rollback()
            raise SsoRequiredError
    return request, client


def is_live(db: Session, request_id: str) -> bool:
    """Whether ``request_id`` names a request not yet decided nor expired."""
    found = db.scalar(
        select(OAuthAuthorizationRequest.id_hash).where(
            OAuthAuthorizationRequest.id_hash == secret_hash(request_id),
            OAuthAuthorizationRequest.expires_at > datetime.now(UTC),
        )
    )
    return found is not None


def claim_via_sso(db: Session, request_id: str, user_id: int, session: str) -> bool:
    """Claim the request for ``user_id``, who just signed in through SSO for it, and for
    ``session``, the raw token of the session that sign-in starts; ``False`` when the request is
    unknown, expired, or another user's. Claiming it again (a second sign-in for it) moves it to
    the newer session."""
    found = _live_request(db, request_id, user_id)
    if found is None:
        db.rollback()
        return False
    request, _ = found
    request.params = {**request.params, _CLAIMED_BY: user_id, _SSO_SESSION: secret_hash(session)}
    db.commit()
    return True


def claim_pending_authorization(
    db: Session,
    request_id: str,
    user_id: int,
    *,
    require_sso: bool = False,
    session: str | None = None,
) -> PendingAuthorization | None:
    """The request for the consent screen, claimed for ``user_id``; ``None`` when it is unknown,
    expired, or another user's. Raises ``SsoRequiredError`` as :func:`_live_request` does."""
    found = _live_request(db, request_id, user_id, require_sso=require_sso, session=session)
    if found is None:
        db.rollback()
        return None
    request, client = found
    if _CLAIMED_BY not in request.params:
        request.params = {**request.params, _CLAIMED_BY: user_id}
    db.commit()
    params = AuthorizationParams.model_validate(request.params)
    redirect = urlsplit(str(params.redirect_uri))
    return PendingAuthorization(
        client_id=client.client_id,
        client_name=client.client_name,
        client_kind=client.kind,
        redirect_origin=f"{redirect.scheme}://{redirect.netloc}",
        scopes=list(params.scopes or ()),
        expires_at=request.expires_at,
    )


def _decided(
    db: Session, request_id: str, user_id: int, *, require_sso: bool, session: str | None
) -> tuple[str, AuthorizationParams] | None:
    found = _live_request(db, request_id, user_id, require_sso=require_sso, session=session)
    if found is None:
        db.rollback()
        return None
    request, client = found
    params: dict[str, Any] = dict(request.params)
    params.pop(_CLAIMED_BY, None)
    params.pop(_SSO_SESSION, None)
    db.delete(request)
    return client.client_id, AuthorizationParams.model_validate(params)


def approve(
    db: Session,
    settings: OAuthSettings,
    request_id: str,
    user_id: int,
    *,
    session: str | None = None,
) -> str | None:
    """Approve the request for ``user_id``: a new grant (a connected app) and its code. Returns
    the client's redirect URL carrying the code, or ``None`` when the request is unknown, expired
    or another user's. Raises ``SsoRequiredError`` when the settings sign users in through SSO and
    the request was not claimed that way for ``session``, the caller's raw session token."""
    decided = _decided(db, request_id, user_id, require_sso=settings.sso_sign_in, session=session)
    if decided is None:
        return None
    client_id, params = decided
    code = create_grant_with_code(
        db,
        user_id=user_id,
        client_id=client_id,
        redirect_uri=str(params.redirect_uri),
        redirect_uri_explicit=params.redirect_uri_provided_explicitly,
        code_challenge=params.code_challenge,
        resource=params.resource or settings.resource,
        scopes=list(params.scopes or ()),
    )
    db.commit()
    logger.info(
        "oauth_authorization_approved",
        extra={"event": "oauth_authorization_approved", "user_id": user_id, "client_id": client_id},
    )
    return construct_redirect_uri(
        str(params.redirect_uri), code=code, state=params.state, iss=settings.issuer
    )


def deny(
    db: Session,
    settings: OAuthSettings,
    request_id: str,
    user_id: int,
    *,
    session: str | None = None,
) -> str | None:
    """Deny the request. Returns the client's redirect URL carrying ``access_denied``, or ``None``
    as :func:`approve` does, which raises the same."""
    decided = _decided(db, request_id, user_id, require_sso=settings.sso_sign_in, session=session)
    if decided is None:
        return None
    client_id, params = decided
    db.commit()
    logger.info(
        "oauth_authorization_denied",
        extra={"event": "oauth_authorization_denied", "user_id": user_id, "client_id": client_id},
    )
    return construct_redirect_uri(
        str(params.redirect_uri), error="access_denied", state=params.state, iss=settings.issuer
    )
