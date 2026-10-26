"""The MCP server's instance settings, from the environment the API starts with."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mailroom.config.public_url import PUBLIC_URL_ENV
from mailroom.mcp.groups import TOOL_GROUPS_ENV, selected_groups
from mailroom.mcp.verifier import AUTH_ENV, AUTH_MODES, AuthMode

if TYPE_CHECKING:
    from mailroom.config.public_url import PublicUrl
    from mailroom.mcp.groups import ToolGroup

logger = logging.getLogger(__name__)

SCOPE = "mailroom"
"""The one scope the built-in authorization server grants; tools are instance config, not scope."""


@dataclass(frozen=True, slots=True)
class McpSettings:
    public_url: str
    """``MAILROOM_PUBLIC_URL``, an origin."""
    auth: AuthMode
    groups: tuple[ToolGroup, ...]

    @property
    def resource(self) -> str:
        """The MCP server as an OAuth protected resource: what tokens are issued for."""
        return f"{self.public_url}/mcp"

    @property
    def issuer(self) -> str:
        """The built-in authorization server's issuer."""
        return f"{self.public_url}/oauth"


def auth_mode_from_env() -> AuthMode:
    """``MAILROOM_MCP_AUTH``, case-insensitive; unset or blank, ``builtin``. Raises
    ``ValueError`` for any other value: the API then fails to start rather than guess."""
    raw = os.environ.get(AUTH_ENV, "").strip().lower() or "builtin"
    if raw not in AUTH_MODES:
        msg = f"{AUTH_ENV} must be one of {', '.join(AUTH_MODES)}; got {raw!r}"
        raise ValueError(msg)
    return raw


def mcp_settings_from_env(public_url: PublicUrl | None) -> McpSettings | None:
    """The settings, or None (logged) when the MCP server is off: ``MAILROOM_MCP_AUTH=off``, or
    no ``public_url`` (``MAILROOM_PUBLIC_URL`` as the API parsed it at startup). Raises
    ``ValueError`` for a value that cannot be used."""
    auth = auth_mode_from_env()
    if auth == "off":
        logger.info("%s=off: the MCP server is off", AUTH_ENV)
        return None
    if public_url is None:
        logger.warning("%s is not set (or invalid): the MCP server is off", PUBLIC_URL_ENV)
        return None
    groups = selected_groups(os.environ.get(TOOL_GROUPS_ENV))
    return McpSettings(public_url=public_url.origin, auth=auth, groups=groups)
