"""The SDK's ``OAuthAuthorizationServerProvider``: its handlers speak the protocol, this decides.
Database work runs in worker threads, off the event loop."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeVar

import anyio
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthToken
from pydantic import AnyUrl

from mailroom.oauth import clients, consent, tokens
from mailroom.oauth.config import SCOPE
from mailroom.oauth.throttle import Throttles

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import httpx
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.oauth.config import OAuthSettings

logger = logging.getLogger(__name__)

R = TypeVar("R")

#: The client key of the request being handled, set by the routes: a metadata document fetch it
#: causes counts against it, whichever endpoint it called.
requester: ContextVar[str] = ContextVar("oauth_requester", default="")


class DatabaseUnavailableError(RuntimeError):
    """The database is down: the request fails as a server error."""


class GrantedCode(AuthorizationCode):
    grant_id: int


class GrantedRefreshToken(RefreshToken):
    grant_id: int


class GrantedAccessToken(AccessToken):
    grant_id: int


def canonical_resource(resource: str) -> str:
    """``resource`` without a trailing slash, as resources are compared."""
    return resource.rstrip("/")


class Provider:
    """Implements ``OAuthAuthorizationServerProvider[GrantedCode, GrantedRefreshToken,
    GrantedAccessToken]``."""

    def __init__(
        self,
        settings: OAuthSettings,
        sessions: Callable[[], sessionmaker[Session] | None],
        *,
        cimd_transport: httpx.AsyncBaseTransport | None = None,
        throttles: Throttles | None = None,
        cimd_failures: clients.RecentFailures | None = None,
    ) -> None:
        self.settings = settings
        self._sessions = sessions
        self._cimd_transport = cimd_transport
        self._cimd_fetches = (throttles or Throttles()).cimd_fetch
        self._cimd_failures = cimd_failures or clients.RecentFailures()

    @contextmanager
    def session(self) -> Iterator[Session]:
        maker = self._sessions()
        if maker is None:
            msg = "Database unavailable"
            raise DatabaseUnavailableError(msg)
        with maker() as db:
            yield db

    async def _in_db(self, work: Callable[[Session], R]) -> R:
        def run() -> R:
            with self.session() as db:
                return work(db)

        return await anyio.to_thread.run_sync(run)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if not clients.is_cimd_client_id(client_id):
            row = await self._in_db(lambda db: clients.cached_client(db, client_id))
            return None if row is None or row.kind != "dcr" else clients.client_from_row(row)
        problem = clients.cimd_url_problem(self.settings, client_id)
        if problem is not None:
            self._cimd_refused(client_id, problem)
            return None
        found = await self._in_db(lambda db: clients.cached_client(db, client_id))
        row = found if found is not None and found.kind == "cimd" else None
        now = datetime.now(UTC)
        if row is not None and clients.cimd_cache_fresh(row, now):
            return clients.client_from_row(row)
        failure = self._cimd_failures.get(client_id)
        if failure is None:
            fetched = await self._fetch_cimd_client(client_id)
            if not isinstance(fetched, str):
                return fetched
            failure = fetched
        if (
            failure == "unreachable"
            and row is not None
            and clients.cimd_cache_usable_stale(row, now)
        ):
            return clients.client_from_row(row)
        return None

    async def _fetch_cimd_client(
        self, client_id: str
    ) -> OAuthClientInformationFull | clients.CimdFailure:
        """Fetch, check and cache the document at ``client_id``, or say how that failed. A fetch
        over the requester's ``cimd_fetch`` limit is not made, and counts as unreachable."""
        if self._cimd_fetches.wait(requester.get()):
            self._cimd_refused(client_id, "fetch throttled")
            return "unreachable"
        try:
            document = await clients.fetch_client_document(client_id, self._cimd_transport)
            info = clients.cimd_client(self.settings, client_id, document)
        except clients.CimdUnreachable as exc:
            self._cimd_refused(client_id, str(exc))
            self._cimd_failures.remember(client_id, "unreachable")
            return "unreachable"
        except clients.CimdRefused as exc:
            self._cimd_refused(client_id, str(exc))
            self._cimd_failures.remember(client_id, "invalid")
            return "invalid"
        self._cimd_failures.forget(client_id)
        await self._in_db(lambda db: clients.save_cimd_client(db, info))
        return info

    @staticmethod
    def _cimd_refused(client_id: str, reason: str) -> None:
        logger.warning(
            "oauth_client_document_refused",
            extra={
                "event": "oauth_client_document_refused",
                "client_id": client_id[:300],
                "reason": reason,
            },
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        try:
            stored = await self._in_db(
                lambda db: clients.register_client(db, self.settings, client_info)
            )
        except clients.RegistrationRefused as exc:
            raise RegistrationError(exc.code, exc.description) from None
        # The handler answers with the object it passed in: make it say what was stored.
        client_info.client_secret = None
        client_info.client_secret_expires_at = None
        client_info.token_endpoint_auth_method = stored.token_endpoint_auth_method
        client_info.scope = stored.scope

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource is None or canonical_resource(params.resource) != self.settings.resource:
            raise AuthorizeError("invalid_request", f"resource must be {self.settings.resource}")
        stored = params.model_copy(
            update={"resource": self.settings.resource, "scopes": params.scopes or [SCOPE]}
        )
        client_id = client.client_id or ""
        request_id = await self._in_db(lambda db: consent.store_request(db, client_id, stored))
        return f"{self.settings.public_origin}{self.settings.consent_path}?request={request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> GrantedCode | None:
        client_id = client.client_id or ""
        row = await self._in_db(lambda db: tokens.take_code(db, client_id, authorization_code))
        if row is None:
            return None
        return GrantedCode(
            code=authorization_code,
            scopes=list(row.scopes),
            expires_at=row.expires_at.timestamp(),
            client_id=row.client_id,
            code_challenge=row.code_challenge,
            redirect_uri=AnyUrl(row.redirect_uri),
            redirect_uri_provided_explicitly=row.redirect_uri_explicit,
            resource=row.resource,
            subject=str(row.user_id),
            grant_id=row.grant_id,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: GrantedCode
    ) -> OAuthToken:
        _ = client
        issued = await self._in_db(
            lambda db: tokens.issue_for_code(db, authorization_code.grant_id)
        )
        if issued is None:
            raise TokenError("invalid_grant", "authorization code does not exist")
        return _oauth_token(issued)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> GrantedRefreshToken | None:
        client_id = client.client_id or ""
        found = await self._in_db(
            lambda db: tokens.verify_refresh_token(db, client_id, refresh_token)
        )
        if found is None:
            return None
        return GrantedRefreshToken(
            token=refresh_token,
            client_id=found.client_id,
            scopes=found.scopes,
            expires_at=found.expires_at,
            resource=found.resource,
            subject=str(found.user_id),
            grant_id=found.grant_id,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: GrantedRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        _ = scopes  # the one scope; the handler has checked it is the token's
        client_id = client.client_id or ""
        issued = await self._in_db(
            lambda db: tokens.rotate_refresh_token(db, client_id, refresh_token.token)
        )
        if issued is None:
            raise TokenError("invalid_grant", "refresh token does not exist")
        return _oauth_token(issued)

    async def load_access_token(self, token: str) -> GrantedAccessToken | None:
        found = await self._in_db(lambda db: tokens.verify_access_token(db, token))
        if found is None:
            return None
        return GrantedAccessToken(
            token=token,
            client_id=found.client_id,
            scopes=found.scopes,
            expires_at=found.expires_at,
            resource=found.resource,
            subject=str(found.user_id),
            claims={"grant_id": str(found.grant_id)},
            grant_id=found.grant_id,
        )

    async def revoke_token(self, token: GrantedAccessToken | GrantedRefreshToken) -> None:
        """Revoking either token ends the whole grant: the client is disconnecting."""
        await self._in_db(lambda db: tokens.revoke_grant(db, token.grant_id))


def _oauth_token(issued: tokens.IssuedTokens) -> OAuthToken:
    return OAuthToken(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        scope=" ".join(issued.scopes),
        refresh_token=issued.refresh_token,
    )
