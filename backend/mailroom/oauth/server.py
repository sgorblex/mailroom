"""The authorization server as the app mounts it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailroom.oauth.provider import Provider
from mailroom.oauth.routes import oauth_routes
from mailroom.oauth.throttle import Throttles, ipv6_network_key

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx
    from mcp.server.auth.provider import AccessToken
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.requests import Request
    from starlette.routing import Route

    from mailroom.oauth.config import OAuthSettings


class OAuthServer:
    """The routes to mount, and the ``TokenVerifier`` for the MCP server's bearer middleware: an
    ``AccessToken`` whose ``subject`` is the user's id, with ``claims["grant_id"]``."""

    def __init__(
        self,
        settings: OAuthSettings,
        sessions: Callable[[], sessionmaker[Session] | None],
        *,
        client_key: Callable[[Request], str],
        network_key: Callable[[Request], str | None] = ipv6_network_key,
        cimd_transport: httpx.AsyncBaseTransport | None = None,
        throttles: Throttles | None = None,
    ) -> None:
        """``client_key`` names the client a request counts against; ``network_key`` the wider
        network registrations also count against (``None``: none)."""
        self.settings = settings
        self._throttles = throttles or Throttles()
        self.provider = Provider(
            settings, sessions, cimd_transport=cimd_transport, throttles=self._throttles
        )
        self._client_key = client_key
        self._network_key = network_key

    def routes(self) -> list[Route]:
        return oauth_routes(
            self.provider,
            client_key=self._client_key,
            network_key=self._network_key,
            throttles=self._throttles,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        return await self.provider.load_access_token(token)
