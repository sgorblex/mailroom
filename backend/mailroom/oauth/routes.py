"""The authorization server's HTTP surface: its RFC 8414 metadata and ``/oauth/{authorize, token,
register, revoke}``, on the SDK's handlers where they fit."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import construct_redirect_uri
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.server.streamable_http import MCP_PROTOCOL_VERSION_HEADER
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE, RequestBodyLimitMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, request_response

from mailroom.oauth.config import MAX_STATE_LENGTH, SCOPE
from mailroom.oauth.provider import canonical_resource, requester
from mailroom.oauth.throttle import Throttles

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from starlette.requests import Request
    from starlette.types import ASGIApp

    from mailroom.oauth.config import OAuthSettings
    from mailroom.oauth.provider import Provider
    from mailroom.oauth.throttle import Throttle

logger = logging.getLogger(__name__)

METADATA_PATH = "/.well-known/oauth-authorization-server/oauth"
"""RFC 8414's place for the metadata of the issuer ``{public origin}/oauth``."""

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def authorization_server_metadata(settings: OAuthSettings) -> dict[str, Any]:
    """RFC 8414. Every client is public, so the endpoints take no client authentication."""
    return {
        "issuer": settings.issuer,
        "authorization_endpoint": settings.endpoint("authorize"),
        "token_endpoint": settings.endpoint("token"),
        "registration_endpoint": settings.endpoint("register"),
        "revocation_endpoint": settings.endpoint("revoke"),
        "scopes_supported": [SCOPE],
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }


def _cors(app: ASGIApp, methods: list[str]) -> ASGIApp:
    """Browser-based clients (the MCP Inspector) call these cross-origin; no cookie is involved."""
    return CORSMiddleware(
        app=app,
        allow_origins="*",
        allow_methods=methods,
        allow_headers=[MCP_PROTOCOL_VERSION_HEADER],
    )


def _limited(handler: Callable[[Request], Awaitable[Response]]) -> ASGIApp:
    return RequestBodyLimitMiddleware(request_response(handler), DEFAULT_MAX_REQUEST_BODY_SIZE)


def _too_many(wait: int, event: str) -> Response:
    logger.warning(event, extra={"event": event})
    return JSONResponse(
        {"error": "too_many_requests", "error_description": "Try again later"},
        status_code=429,
        headers={"Retry-After": str(wait)},
    )


@contextmanager
def _counted_against(key: str) -> Iterator[None]:
    """Within it, a metadata document fetch the request causes counts against ``key``."""
    token = requester.set(key)
    try:
        yield
    finally:
        requester.reset(token)


def _wait(*checks: tuple[Throttle, str | None]) -> int:
    """The first wait a throttle asks for its key (a ``None`` key is not counted there), or 0."""
    for throttle, key in checks:
        if key is not None and (wait := throttle.wait(key)):
            return wait
    return 0


def _with_iss(response: Response, settings: OAuthSettings) -> Response:
    """A redirect back to the client carries ``iss`` (RFC 9207), as the metadata promises: the
    SDK's authorize handler sends its errors back without one. The consent screen redirect is not
    the client's and is left alone."""
    location = response.headers.get("location")
    consent = f"{settings.public_origin}{settings.consent_path}?"
    if location is None or location.startswith(consent):
        return response
    if "iss" not in parse_qs(urlsplit(location).query):
        response.headers["location"] = construct_redirect_uri(location, iss=settings.issuer)
    return response


def oauth_routes(
    provider: Provider,
    *,
    client_key: Callable[[Request], str],
    network_key: Callable[[Request], str | None],
    throttles: Throttles | None = None,
) -> list[Route]:
    settings = provider.settings
    metadata = authorization_server_metadata(settings)
    authenticator = ClientAuthenticator(provider)
    registration = RegistrationHandler(
        provider,
        ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
    )
    limits = throttles or Throttles()
    authorization = AuthorizationHandler(provider)
    token_handler = TokenHandler(provider, authenticator)

    async def metadata_endpoint(request: Request) -> Response:
        _ = request
        return JSONResponse(metadata)

    async def register(request: Request) -> Response:
        wait = _wait(
            (limits.register, client_key(request)),
            (limits.register_network, network_key(request)),
        )
        if wait:
            return _too_many(wait, "oauth_client_registration_throttled")
        return await registration.handle(request)

    async def authorize(request: Request) -> Response:
        """The SDK's handler, behind a per-client limit and a cap on ``state``, which is stored
        and sent back. Every redirect back to the client carries ``iss``."""
        key = client_key(request)
        wait = limits.authorize.wait(key)
        if wait:
            return _too_many(wait, "oauth_authorization_throttled")
        params = request.query_params if request.method == "GET" else await request.form()
        state = params.get("state")
        if isinstance(state, str) and len(state) > MAX_STATE_LENGTH:
            return JSONResponse(
                {
                    "error": "invalid_request",
                    "error_description": f"state is longer than {MAX_STATE_LENGTH} characters",
                },
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        with _counted_against(key):
            return _with_iss(await authorization.handle(request), settings)

    async def token(request: Request) -> Response:
        """The SDK's handler, which ignores ``resource``: a token request naming another resource
        than ``/mcp`` is refused (RFC 8707, 2.2)."""
        form = await request.form()
        named = form.getlist("resource")
        if any(
            not isinstance(value, str) or canonical_resource(value) != settings.resource
            for value in named
        ):
            return JSONResponse(
                {
                    "error": "invalid_target",
                    "error_description": f"resource must be {settings.resource}",
                },
                status_code=400,
                headers=_NO_STORE,
            )
        with _counted_against(client_key(request)):
            answer: Response = await token_handler.handle(request)
            return answer

    async def revoke(request: Request) -> Response:
        """RFC 7009 for public clients (the SDK's handler insists on a ``client_secret`` field).
        An unknown token, or another client's, is answered 200 all the same."""
        with _counted_against(client_key(request)):
            return await _revoke(request)

    async def _revoke(request: Request) -> Response:
        try:
            client = await authenticator.authenticate_request(request)
        except AuthenticationError as exc:
            return JSONResponse(
                {"error": "unauthorized_client", "error_description": exc.message},
                status_code=401,
                headers=_NO_STORE,
            )
        form = await request.form()
        token = form.get("token")
        if not isinstance(token, str) or not token:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "token is required"},
                status_code=400,
                headers=_NO_STORE,
            )
        found = await provider.load_access_token(token) or await provider.load_refresh_token(
            client, token
        )
        if found is not None and found.client_id == client.client_id:
            await provider.revoke_token(found)
        return Response(status_code=200, headers=_NO_STORE)

    return [
        Route(
            METADATA_PATH,
            endpoint=_cors(request_response(metadata_endpoint), ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        ),
        # No CORS: a browser navigates here.
        Route(
            "/oauth/authorize",
            endpoint=_limited(authorize),
            methods=["GET", "POST"],
        ),
        Route(
            "/oauth/token",
            endpoint=_cors(_limited(token), ["POST", "OPTIONS"]),
            methods=["POST", "OPTIONS"],
        ),
        Route(
            "/oauth/register",
            endpoint=_cors(_limited(register), ["POST", "OPTIONS"]),
            methods=["POST", "OPTIONS"],
        ),
        Route(
            "/oauth/revoke",
            endpoint=_cors(_limited(revoke), ["POST", "OPTIONS"]),
            methods=["POST", "OPTIONS"],
        ),
    ]
