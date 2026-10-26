"""``MAILROOM_PUBLIC_URL``: the origin users and OAuth clients reach this instance at.

Parsed once at startup and never derived from a request's ``Host``, which a client chooses. SSO's
callback and, later, the MCP server's URLs are built from it; unset or invalid, both are off.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

PUBLIC_URL_ENV = "MAILROOM_PUBLIC_URL"

_DEFAULT_PORTS: Final = {"https": 443, "http": 80}
_DNS_LABEL: Final = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")


def is_loopback_host(host: str) -> bool:
    """``localhost`` or a loopback address: the only hosts plain HTTP is accepted for."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _valid_host(host: str) -> bool:
    """``host`` (as ``urlsplit`` gives it: lowercase, no brackets) is an IP address or an ASCII
    DNS name (IDNA A-labels for any other script): letters, digits and inner hyphens, dot-separated
    labels, no trailing dot. An all-numeric name must be an IPv4 address."""
    if ":" in host:
        try:
            return "%" not in host and ipaddress.ip_address(host).version == 6
        except ValueError:
            return False
    if len(host) > 253:
        return False
    labels = host.split(".")
    if not all(_DNS_LABEL.fullmatch(label) for label in labels):
        return False
    if labels[-1].isdigit():
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            return False
    return True


@dataclass(frozen=True, slots=True)
class PublicUrl:
    origin: str
    """``scheme://host[:port]``, lowercase, without a trailing slash; the port only when it is
    not the scheme's default."""
    host: str
    """The host (and port, when not the default), as a ``Host`` header names it."""

    def url(self, path: str) -> str:
        """The absolute URL of ``path``, which starts with ``/``."""
        return f"{self.origin}{path}"


def parse_public_url(raw: str) -> PublicUrl:
    """An origin: ``https://`` (or ``http://`` on loopback), a valid host, an optional port (the
    scheme's default dropped), nothing else. Raises ``ValueError`` saying what is wrong."""
    parts = urlsplit(raw.strip())
    if parts.scheme not in {"https", "http"}:
        msg = "it must start with https://"
        raise ValueError(msg)
    if parts.username is not None or parts.password is not None:
        msg = "it must not carry credentials"
        raise ValueError(msg)
    host = parts.hostname
    if not host:
        msg = "it has no host"
        raise ValueError(msg)
    if not _valid_host(host):
        msg = "its host is not a valid host name or IP address"
        raise ValueError(msg)
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        msg = "it must be an origin: no path, query or fragment"
        raise ValueError(msg)
    if parts.scheme == "http" and not is_loopback_host(host):
        msg = "plain http:// is for loopback only"
        raise ValueError(msg)
    try:
        port = parts.port
    except ValueError as exc:
        msg = "its port is not a number"
        raise ValueError(msg) from exc
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != _DEFAULT_PORTS[parts.scheme]:
        netloc = f"{netloc}:{port}"
    return PublicUrl(origin=f"{parts.scheme}://{netloc}", host=netloc)


def public_url_from_env() -> PublicUrl | None:
    """The configured public URL; ``None`` when unset or invalid (with a warning for the
    latter)."""
    raw = os.environ.get(PUBLIC_URL_ENV, "").strip()
    if not raw:
        return None
    try:
        return parse_public_url(raw)
    except ValueError as exc:
        logger.warning("%s is ignored: %s.", PUBLIC_URL_ENV, exc)
        return None
