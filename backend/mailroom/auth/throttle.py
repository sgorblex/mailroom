"""Login throttling: after n consecutive failures, a client waits 2^(n-1) s (capped at 15 min)
before its next attempt is even checked. In memory, per process; a success resets the client."""

from __future__ import annotations

import ipaddress
import math
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.requests import Request

MAX_WAIT_SECONDS = 15 * 60
# A client's failures are forgotten once it has been quiet this long.
_FORGET_AFTER_SECONDS = 24 * 60 * 60
_SWEEP_EVERY_SECONDS = 60 * 60


def client_key(request: Request) -> str:
    """The client an attempt counts against: an IPv4 address or an IPv6 /56. Behind the web
    container's nginx, uvicorn has already taken the address from its ``X-Forwarded-For``."""
    address = request.client.host if request.client else ""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return address
    if parsed.version == 6:
        return str(ipaddress.ip_network(f"{parsed}/56", strict=False))
    return str(parsed)


class LoginThrottle:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        # client -> (consecutive failures, time of the last one)
        self._failures: dict[str, tuple[int, float]] = {}
        self._last_sweep = clock()

    def begin_attempt(self, client: str) -> int:
        """Seconds the client must still wait, or 0 to let the attempt through. A let-through
        attempt counts as a failure until ``record_success``, so parallel guesses wait like
        serial ones."""
        now = self._clock()
        with self._lock:
            self._forget_quiet_clients(now)
            count, last = self._failures.get(client, (0, now))
            if count:
                remaining = math.ceil(last + min(1 << (count - 1), MAX_WAIT_SECONDS) - now)
                if remaining > 0:
                    return remaining
            self._failures[client] = (count + 1, now)
            return 0

    def record_success(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def _forget_quiet_clients(self, now: float) -> None:
        if now - self._last_sweep < _SWEEP_EVERY_SECONDS:
            return
        self._last_sweep = now
        self._failures = {
            key: entry
            for key, entry in self._failures.items()
            if now - entry[1] < _FORGET_AFTER_SECONDS
        }
