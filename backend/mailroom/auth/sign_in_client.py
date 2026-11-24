"""What a session keeps of the browser that signed in, for its user to recognise it: a browser and
system label, never the User-Agent itself, and as much of the IP address as the instance's
``session_ip`` setting lets be recorded."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mailroom.auth.throttle import client_address

if TYPE_CHECKING:
    from starlette.requests import Request

    from mailroom.config.instance_settings import SessionIp

_USER_AGENT_READ: Final = 512

# The first that matches wins: most browsers also name those they are compatible with.
_BROWSERS: Final = tuple(
    (re.compile(pattern), name)
    for pattern, name in (
        (r"\bEdg(?:e|A|iOS)?/", "Edge"),
        (r"\b(?:OPR|OPT|Opera)/", "Opera"),
        (r"\bVivaldi/", "Vivaldi"),
        (r"\bSamsungBrowser/", "Samsung Internet"),
        (r"\bYaBrowser/", "Yandex Browser"),
        (r"\bDuckDuckGo/", "DuckDuckGo"),
        (r"\b(?:Firefox|FxiOS)/", "Firefox"),
        (r"\bChromium/", "Chromium"),
        (r"\b(?:Chrome|CriOS)/", "Chrome"),
        (r"\bVersion/\S+ (?:Mobile/\S+ )?Safari/", "Safari"),
        (r"\bcurl/", "curl"),
    )
)
_SYSTEMS: Final = tuple(
    (re.compile(pattern), name)
    for pattern, name in (
        (r"\biPad\b", "iPadOS"),
        (r"\biP(?:hone|od)\b", "iOS"),
        (r"\bWindows\b", "Windows"),
        (r"\bAndroid\b", "Android"),
        (r"\bCrOS\b", "ChromeOS"),
        (r"\bMac(?:intosh| OS X)\b", "macOS"),
        (r"\b(?:Free|Open|Net)BSD\b", "BSD"),
        (r"\bLinux\b", "Linux"),
    )
)


@dataclass(frozen=True)
class SignInClient:
    device: str | None = None
    ip: str | None = None


UNKNOWN_CLIENT: Final = SignInClient()


def sign_in_client(request: Request, session_ip: SessionIp) -> SignInClient:
    """The browser ``request`` comes from, as its session may record it."""
    address = client_address(request)
    return SignInClient(
        device=device_label(request.headers.get("user-agent", "")),
        ip=None if address is None else recorded_ip(str(address), session_ip),
    )


def device_label(user_agent: str) -> str | None:
    """``Firefox · Linux``, or the half that is recognised; ``None`` for neither. Only names from
    the tables here, so nothing the browser sent is kept."""
    read = user_agent[:_USER_AGENT_READ]
    found = [
        next((name for pattern, name in table if pattern.search(read)), None)
        for table in (_BROWSERS, _SYSTEMS)
    ]
    return " · ".join(name for name in found if name is not None) or None


def recorded_ip(address: str, session_ip: SessionIp) -> str | None:
    """``address`` as far as ``session_ip`` goes: ``partial`` keeps an IPv4 /24 or an IPv6 /48 and
    writes ``x`` for the rest. One written so before stays so; text that is no address goes."""
    if session_ip == "none":
        return None
    if address.endswith((".x", ":x")):
        return address
    try:
        parsed = ipaddress.ip_address(address.partition("%")[0])
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    if session_ip == "full":
        return str(parsed)
    if parsed.version == 4:
        return f"{str(parsed).rpartition('.')[0]}.x"
    prefix = parsed.exploded.split(":")[:3]
    return f"{':'.join(f'{int(group, 16):x}' for group in prefix)}:x"
