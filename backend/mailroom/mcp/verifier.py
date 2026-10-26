"""Who a bearer token on ``/mcp`` speaks for.

The contract every verifier keeps (``mcp.server.auth.provider.TokenVerifier``): ``verify_token``
returns None for any token it does not accept, or an ``AccessToken`` whose ``subject`` is the
Mailroom user's id (``str(user.id)``), whose ``client_id`` is the OAuth client's, whose
``resource`` is the MCP resource (``{MAILROOM_PUBLIC_URL}/mcp``) and whose ``claims`` may carry a
``grant_id`` for the audit line. Scope is not checked past the verifier: which tools a client gets
is instance config.

``MAILROOM_MCP_AUTH`` picks the verifier: ``builtin`` (Mailroom's own authorization server),
``external`` (an OIDC provider's JWTs) or ``off``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from mcp.server.auth.provider import AccessToken, TokenVerifier

logger = logging.getLogger(__name__)

AUTH_ENV: Final = "MAILROOM_MCP_AUTH"
AuthMode = Literal["builtin", "external", "off"]
AUTH_MODES: tuple[AuthMode, ...] = ("builtin", "external", "off")


class RefusingVerifier:
    """Accepts no token: the mode's verifier is not part of this build yet."""

    async def verify_token(self, token: str) -> AccessToken | None:
        del token
        return None


def token_verifier(mode: AuthMode) -> TokenVerifier | None:
    """The verifier for ``mode``; None for ``off``."""
    if mode == "off":
        return None
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
