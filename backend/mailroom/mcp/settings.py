"""The MCP server's instance settings, from the environment the API starts with."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mailroom.auth.oidc import OidcError, checked_provider_url
from mailroom.auth.sso import ISSUER_ENV
from mailroom.config.public_url import PUBLIC_URL_ENV
from mailroom.mcp.groups import TOOL_GROUPS_ENV, selected_groups
from mailroom.mcp.verifier import AUTH_ENV, AUTH_MODES, AuthMode

if TYPE_CHECKING:
    from mailroom.config.public_url import PublicUrl
    from mailroom.mcp.groups import ToolGroup

logger = logging.getLogger(__name__)

EXTERNAL_ISSUER_ENV: Final = "MAILROOM_MCP_OIDC_ISSUER"
EXTERNAL_AUDIENCES_ENV: Final = "MAILROOM_MCP_OIDC_AUDIENCES"
EXTERNAL_SCOPE_ENV: Final = "MAILROOM_MCP_OIDC_REQUIRED_SCOPE"
SCOPE = "mailroom"
"""The one scope the built-in authorization server grants; tools are instance config, not scope."""
EXTERNAL_SCOPES = ("openid", "offline_access")
"""What a client asks an external issuer for: a sign-in and a refresh token."""


@dataclass(frozen=True, slots=True)
class ExternalIssuer:
    """External mode's OIDC provider and what its access tokens must say."""

    issuer: str
    audiences: tuple[str, ...]
    """Accepted ``aud`` values (Authentik's is the client ID)."""
    required_scope: str | None = None


@dataclass(frozen=True, slots=True)
class McpSettings:
    public_url: str
    """``MAILROOM_PUBLIC_URL``, an origin."""
    auth: AuthMode
    groups: tuple[ToolGroup, ...]
    external: ExternalIssuer | None = None
    """Set exactly when ``auth`` is ``external``."""

    @property
    def resource(self) -> str:
        """The MCP server as an OAuth protected resource: what tokens are issued for."""
        return f"{self.public_url}/mcp"

    @property
    def issuer(self) -> str:
        """The built-in authorization server's issuer."""
        return f"{self.public_url}/oauth"

    @property
    def authorization_server(self) -> str:
        """The issuer the protected-resource metadata sends clients to."""
        return self.external.issuer if self.external is not None else self.issuer

    @property
    def scopes_supported(self) -> list[str]:
        """The protected-resource metadata's ``scopes_supported``: the built-in server's one scope,
        or what to ask the external issuer for (``EXTERNAL_SCOPES`` and the required scope)."""
        if self.external is None:
            return [SCOPE]
        required = self.external.required_scope
        return list(dict.fromkeys((*EXTERNAL_SCOPES, *([required] if required else []))))


def auth_mode_from_env() -> AuthMode:
    """``MAILROOM_MCP_AUTH``, case-insensitive; unset or blank, ``builtin``. Raises
    ``ValueError`` for any other value: the API then fails to start rather than guess."""
    raw = os.environ.get(AUTH_ENV, "").strip().lower() or "builtin"
    if raw not in AUTH_MODES:
        msg = f"{AUTH_ENV} must be one of {', '.join(AUTH_MODES)}; got {raw!r}"
        raise ValueError(msg)
    return raw


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def external_issuer_from_env() -> ExternalIssuer | None:
    """External mode's provider, or None (logged) when it is not set up well enough to use."""
    issuer = _env(EXTERNAL_ISSUER_ENV) or _env(ISSUER_ENV)
    listed = (part.strip() for part in _env(EXTERNAL_AUDIENCES_ENV).split(","))
    audiences = tuple(dict.fromkeys(audience for audience in listed if audience))
    if not issuer or not audiences:
        logger.warning(
            "%s=external needs %s (or %s) and %s: the MCP server is off",
            AUTH_ENV,
            EXTERNAL_ISSUER_ENV,
            ISSUER_ENV,
            EXTERNAL_AUDIENCES_ENV,
        )
        return None
    try:
        checked_provider_url(issuer, what="the issuer")
    except OidcError as exc:
        logger.warning("%s: %s; the MCP server is off", EXTERNAL_ISSUER_ENV, exc.message)
        return None
    scope = _env(EXTERNAL_SCOPE_ENV)
    if scope and any(c.isspace() for c in scope):
        logger.warning("%s must be one scope: the MCP server is off", EXTERNAL_SCOPE_ENV)
        return None
    return ExternalIssuer(issuer=issuer, audiences=audiences, required_scope=scope or None)


def mcp_settings_from_env(public_url: PublicUrl | None) -> McpSettings | None:
    """The settings, or None (logged) when the MCP server is off: ``MAILROOM_MCP_AUTH=off``, no
    ``public_url`` (``MAILROOM_PUBLIC_URL`` as the API parsed it at startup), or external mode
    without its provider. Raises ``ValueError`` for a ``MAILROOM_MCP_AUTH`` that is no mode."""
    auth = auth_mode_from_env()
    if auth == "off":
        logger.info("%s=off: the MCP server is off", AUTH_ENV)
        return None
    if public_url is None:
        logger.warning("%s is not set (or invalid): the MCP server is off", PUBLIC_URL_ENV)
        return None
    external = None
    if auth == "external":
        external = external_issuer_from_env()
        if external is None:
            return None
    groups = selected_groups(os.environ.get(TOOL_GROUPS_ENV))
    return McpSettings(public_url=public_url.origin, auth=auth, groups=groups, external=external)
