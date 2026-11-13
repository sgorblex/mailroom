"""Who a bearer token on ``/mcp`` speaks for.

The contract every verifier keeps (``mcp.server.auth.provider.TokenVerifier``): ``verify_token``
returns None for any token it does not accept, or an ``AccessToken`` whose ``subject`` is the
Mailroom user's id (``str(user.id)``), whose ``client_id`` is the OAuth client's, whose
``resource`` is the MCP resource (``{MAILROOM_PUBLIC_URL}/mcp``) and whose ``claims`` may carry a
``grant_id`` for the audit line. Scope is not checked past the verifier: which tools a client gets
is instance config.

``MAILROOM_MCP_AUTH`` picks the verifier: ``builtin`` and ``oidc`` (Mailroom's own authorization
server; in ``oidc`` its users sign in through web SSO), ``external`` (an OIDC provider's JWTs,
``ExternalJwtVerifier``) or ``off``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final, Literal

import anyio
import jwt
from mcp.server.auth.provider import AccessToken
from sqlalchemy.exc import SQLAlchemyError

from mailroom.auth.oidc import DEFAULT_LEEWAY_SECONDS, OidcError
from mailroom.auth.sso import linked_user_id

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.auth.provider import TokenVerifier
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.auth.oidc import OidcProvider

logger = logging.getLogger(__name__)

EXTERNAL_CLIENT_ID = "external"
"""The client a valid external token names when it carries neither ``azp`` nor ``client_id``."""

AUTH_ENV: Final = "MAILROOM_MCP_AUTH"
AuthMode = Literal["builtin", "oidc", "external", "off"]
"""``oidc`` is the built-in authorization server with its users signing in through web SSO."""
AUTH_MODES: tuple[AuthMode, ...] = ("builtin", "oidc", "external", "off")


class RefusingVerifier:
    """Accepts no token: the mode's verifier is not part of this build yet."""

    async def verify_token(self, token: str) -> AccessToken | None:
        del token
        return None


ACCESS_TOKEN_TYPES = frozenset({"jwt", "at+jwt", "application/at+jwt"})
"""The ``typ`` headers an external access token may carry (or none): RFC 9068's ``at+jwt``, and
the generic ``JWT`` Authentik puts on its access tokens. ``typ`` is compared case-insensitively,
``application/`` optional, as RFC 7515 says."""


MAX_LOGGED_TYPE: Final = 64
"""How much of a refused ``typ`` header the log keeps: the sender chose it, at any length."""


def _refused_token_type(token: str) -> str | None:
    """The token's ``typ`` header when it is not an access token's, unverified, as a ``repr`` cut
    to ``MAX_LOGGED_TYPE`` characters for the log; None when it is absent or allowed, or the header
    does not parse (``verify_jwt`` refuses that)."""
    try:
        typ = jwt.get_unverified_header(token).get("typ")
    except jwt.PyJWTError:
        return None
    if typ is None or (isinstance(typ, str) and typ.lower() in ACCESS_TOKEN_TYPES):
        return None
    return repr(typ)[:MAX_LOGGED_TYPE]


def _scopes(claims: dict[str, Any]) -> list[str]:
    """The token's scopes: OAuth's space-separated ``scope``, or ``scp`` (a list or a string)."""
    for name in ("scope", "scp"):
        value = claims.get(name)
        if isinstance(value, str):
            return value.split()
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
    return []


class ExternalJwtVerifier:
    """External mode: a JWT ``provider`` signed for one of ``audiences`` (checked by
    ``mailroom.auth.oidc.verify_jwt``: asymmetric algorithms only, ``iss``, ``aud``, ``exp`` and
    ``iat`` with leeway, the JWKS refetched for an unknown ``kid``), carrying ``required_scope``
    when set, whose ``(iss, sub)`` is a linked sign-in. The token's ``aud`` stands for the
    resource, so the ``AccessToken`` names ``resource`` itself.

    The provider's ID tokens share the access tokens' ``aud`` (Authentik's is the client ID), so
    a token that looks like one is refused: a ``typ`` header other than ``JWT`` or ``at+jwt``
    (checked before anything is fetched), or a ``nonce`` claim, which only an ID token carries."""

    def __init__(
        self,
        provider: OidcProvider,
        *,
        audiences: tuple[str, ...],
        required_scope: str | None,
        resource: str,
        session_maker: Callable[[], sessionmaker[Session] | None],
    ) -> None:
        self.provider = provider
        self._audiences = audiences
        self._required_scope = required_scope
        self._resource = resource
        self._session_maker = session_maker

    async def verify_token(self, token: str) -> AccessToken | None:
        return await anyio.to_thread.run_sync(self._verify, token)

    def _verify(self, token: str) -> AccessToken | None:
        typ = _refused_token_type(token)
        if typ is not None:
            logger.info(
                "External MCP token refused: its typ %s is not an access token's",
                typ,
                extra={"reason": "token_type"},
            )
            return None
        try:
            claims = self.provider.verify_jwt(token, audiences=self._audiences)
        except OidcError as exc:
            log = logger.warning if exc.code == "unavailable" else logger.info
            log("External MCP token refused: %s", exc.message, extra={"reason": exc.code})
            return None
        if "nonce" in claims:
            logger.info(
                "External MCP token refused: it carries a nonce, so it is an ID token",
                extra={"reason": "id_token"},
            )
            return None
        scopes = _scopes(claims)
        if self._required_scope is not None and self._required_scope not in scopes:
            logger.info(
                "External MCP token refused: no %s scope",
                self._required_scope,
                extra={"reason": "scope"},
            )
            return None
        user_id = self._linked_user(str(claims["iss"]), str(claims["sub"]))
        if user_id is None:
            return None
        client = claims.get("azp") or claims.get("client_id")
        return AccessToken(
            token=token,
            client_id=client if isinstance(client, str) and client else EXTERNAL_CLIENT_ID,
            scopes=scopes,
            # The bearer middleware drops a token past ``expires_at``: keep the check's leeway.
            expires_at=int(claims["exp"]) + DEFAULT_LEEWAY_SECONDS,
            resource=self._resource,
            subject=str(user_id),
            claims={"iss": claims["iss"]},
        )

    def _linked_user(self, issuer: str, subject: str) -> int | None:
        sessions = self._session_maker()
        if sessions is None:
            logger.warning("External MCP token refused: the database is unavailable")
            return None
        try:
            with sessions() as db:
                user_id = linked_user_id(db, issuer, subject)
        except SQLAlchemyError:
            logger.warning("External MCP token refused: the identity lookup failed", exc_info=True)
            return None
        if user_id is None:
            logger.info(
                "External MCP token refused: its subject is no linked sign-in",
                extra={"reason": "unlinked"},
            )
        return user_id


def token_verifier(
    mode: AuthMode,
    *,
    builtin: TokenVerifier | None = None,
    external: TokenVerifier | None = None,
) -> TokenVerifier | None:
    """The verifier for ``mode``; None for ``off``. ``builtin`` is the built-in authorization
    server, which checks its own access tokens; ``external`` an ``ExternalJwtVerifier``."""
    if mode == "off":
        return None
    if mode in ("builtin", "oidc") and builtin is not None:
        return builtin
    if mode == "external" and external is not None:
        return external
    logger.warning(
        "%s=%s: its token verifier is not available yet, so /mcp accepts no token",
        AUTH_ENV,
        mode,
    )
    return RefusingVerifier()


def user_id_of(token: AccessToken) -> int | None:
    """The Mailroom user ``token`` speaks for (its ``subject``), or None when it names none."""
    subject = token.subject
    if subject is None or not (subject.isascii() and subject.isdigit()):
        return None
    return int(subject)


def grant_id_of(token: AccessToken) -> str | None:
    grant = (token.claims or {}).get("grant_id")
    return None if grant is None else str(grant)
