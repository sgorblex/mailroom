"""The authorization server's settings: its URLs, derived from the instance's public origin, and
which clients and redirect URIs it accepts."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit

from pydantic import AnyUrl

from mailroom.config.public_url import is_loopback_host

EXTRA_REDIRECT_URIS_ENV = "MAILROOM_OAUTH_EXTRA_REDIRECT_URIS"
CIMD_HOSTS_ENV = "MAILROOM_OAUTH_CIMD_HOSTS"

SCOPE = "mailroom"
"""The one scope. Which tools a client gets is instance config, not scope."""

#: Where claude.ai and claude.com send the user back after sign-in. Loopback is allowed on top of
#: these, on any port and path (RFC 8252): Claude Code and the MCP Inspector listen there.
BUILTIN_REDIRECT_URIS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)
DEFAULT_CIMD_HOSTS = ("claude.ai", "claude.com")

REQUEST_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=10)
ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=30)
CIMD_CACHE_TTL = timedelta(hours=24)
#: How long after its last good fetch a cached document still serves when fetching it again
#: fails in transit (timeout, connection error, a status other than 200).
CIMD_STALE_GRACE = timedelta(days=7)
#: How long a failed fetch is remembered (per process) before the URL is fetched again.
CIMD_FAILURE_TTL = timedelta(minutes=5)
#: A registered client with no grant is forgotten after this long unused...
IDLE_CLIENT_TTL = timedelta(days=30)
#: ... or after this long if it was never used at all.
NEVER_USED_CLIENT_TTL = timedelta(days=1)
#: Clients registered dynamically, at most. A registration past it evicts the oldest client that
#: was never used and holds no grant; when there is none, it is refused.
MAX_REGISTERED_CLIENTS = 1000
MAX_CLIENT_NAME = 100
#: The longest ``state`` an authorization request may carry: it is stored and sent back.
MAX_STATE_LENGTH = 1024


def normalized_uri(uri: str) -> str:
    """``uri`` as the SDK's handlers compare redirect URIs (pydantic's ``AnyUrl``)."""
    try:
        return str(AnyUrl(uri))
    except ValueError:
        return uri


def is_loopback_redirect(uri: str) -> bool:
    """``http://`` to a loopback host, any port and path, no fragment (RFC 8252, 7.3)."""
    parts = urlsplit(uri)
    return (
        parts.scheme == "http"
        and parts.hostname is not None
        and is_loopback_host(parts.hostname)
        and not parts.fragment
        and parts.username is None
        and parts.password is None
    )


def _checked_extra_redirect_uri(uri: str) -> str:
    parts = urlsplit(uri)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.fragment
        or parts.username is not None
        or parts.password is not None
    ):
        msg = f"{EXTRA_REDIRECT_URIS_ENV}: {uri!r} is not an https URL without a fragment"
        raise ValueError(msg)
    return uri


@dataclass(frozen=True, slots=True)
class OAuthSettings:
    public_origin: str
    """``scheme://host[:port]`` without a trailing slash: the instance as users reach it."""
    extra_redirect_uris: tuple[str, ...] = ()
    cimd_hosts: tuple[str, ...] = DEFAULT_CIMD_HOSTS
    consent_path: str = "/connect"
    """The web app's consent screen; ``?request=<id>`` names the pending request."""
    sso_start_path: str | None = None
    """Where ``/oauth/authorize`` sends the browser instead of the consent screen, ``request=<id>``
    added to its query, when users sign in through SSO first (``MAILROOM_MCP_AUTH=oidc``); the
    SSO callback goes on to the consent screen. ``None``: straight to the consent screen."""
    redirect_uris: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        listed = (*BUILTIN_REDIRECT_URIS, *self.extra_redirect_uris)
        object.__setattr__(self, "redirect_uris", frozenset(normalized_uri(uri) for uri in listed))

    @property
    def sso_sign_in(self) -> bool:
        """Only a request whose user signed in through SSO for it may be decided."""
        return self.sso_start_path is not None

    def sign_in_url(self, request_id: str) -> str:
        """Where ``/oauth/authorize`` sends the browser with a stored request."""
        if self.sso_start_path is None:
            return f"{self.public_origin}{self.consent_path}?{urlencode({'request': request_id})}"
        parts = urlsplit(self.sso_start_path)
        query = [*parse_qsl(parts.query), ("request", request_id)]
        return f"{self.public_origin}{parts.path}?{urlencode(query)}"

    @property
    def issuer(self) -> str:
        return f"{self.public_origin}/oauth"

    @property
    def resource(self) -> str:
        """The MCP server, the one resource tokens are issued for (RFC 8707)."""
        return f"{self.public_origin}/mcp"

    def endpoint(self, name: str) -> str:
        return f"{self.issuer}/{name}"

    def redirect_uri_allowed(self, uri: str) -> bool:
        return normalized_uri(uri) in self.redirect_uris or is_loopback_redirect(uri)


def _listed(raw: str | None) -> tuple[str, ...]:
    return tuple(item for item in (raw or "").replace(",", " ").split() if item)


def oauth_settings_from_env(
    public_origin: str, *, sso_start_path: str | None = None
) -> OAuthSettings:
    """``public_origin`` is ``MAILROOM_PUBLIC_URL``'s origin as ``mailroom.config.public_url``
    parses it; ``MAILROOM_OAUTH_EXTRA_REDIRECT_URIS`` and ``MAILROOM_OAUTH_CIMD_HOSTS`` (comma or
    space separated; an empty ``CIMD_HOSTS`` turns client metadata documents off);
    ``sso_start_path`` as on ``OAuthSettings``. Raises
    ``ValueError`` for an extra redirect URI that is not https."""
    extra = tuple(
        _checked_extra_redirect_uri(uri) for uri in _listed(os.environ.get(EXTRA_REDIRECT_URIS_ENV))
    )
    raw_hosts = os.environ.get(CIMD_HOSTS_ENV)
    hosts = DEFAULT_CIMD_HOSTS if raw_hosts is None else _listed(raw_hosts.lower())
    return OAuthSettings(
        public_origin=public_origin,
        extra_redirect_uris=extra,
        cimd_hosts=hosts,
        sso_start_path=sso_start_path,
    )
