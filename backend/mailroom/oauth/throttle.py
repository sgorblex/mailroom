"""The limits on the endpoints anyone may call. In memory, per process, like the login throttle."""

from __future__ import annotations

import ipaddress
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.requests import Request

_HOUR = 60 * 60
_TEN_MINUTES = 10 * 60

#: The IPv6 prefix registrations are also counted on: one site's whole allocation, so a client
#: cannot dodge the per-client limit by hopping across the /56s of its own network.
NETWORK_PREFIX_V6 = 48


class Throttle:
    """At most ``limit`` requests per key in any ``window`` seconds."""

    def __init__(
        self, limit: int, window: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: dict[str, deque[float]] = {}

    def wait(self, key: str) -> int:
        """Seconds ``key`` must wait, or 0 (and the request counts)."""
        now = self._clock()
        with self._lock:
            quiet = [k for k, seen in self._seen.items() if seen[-1] <= now - self._window]
            for k in quiet:
                del self._seen[k]
            times = self._seen.setdefault(key, deque())
            while times and times[0] <= now - self._window:
                times.popleft()
            if len(times) >= self._limit:
                return max(1, math.ceil(times[0] + self._window - now))
            times.append(now)
            return 0


@dataclass(frozen=True, slots=True)
class Throttles:
    """``register`` counts per client key and ``register_network`` per IPv6 /48 (an IPv4 client is
    counted per client key only); ``authorize`` per client key; ``cimd_fetch`` counts the client
    ID metadata documents a client key makes the server fetch, whichever endpoint it called."""

    register: Throttle = field(default_factory=lambda: Throttle(10, _HOUR))
    register_network: Throttle = field(default_factory=lambda: Throttle(30, _HOUR))
    authorize: Throttle = field(default_factory=lambda: Throttle(30, _TEN_MINUTES))
    cimd_fetch: Throttle = field(default_factory=lambda: Throttle(10, _TEN_MINUTES))


def ipv6_network_key(request: Request) -> str | None:
    """The client's IPv6 /48, or ``None`` for an IPv4 (or unparsable) client."""
    address = request.client.host if request.client else ""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    if not isinstance(parsed, ipaddress.IPv6Address) or parsed.ipv4_mapped is not None:
        return None
    return str(ipaddress.ip_network(f"{parsed}/{NETWORK_PREFIX_V6}", strict=False))
