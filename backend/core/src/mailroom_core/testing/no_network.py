"""Refuse every socket to a host that is not this machine or one of the mail test servers.

The example config names real mail servers; a test that reached one would spend a login attempt
against a production rate limit with a fake password. Same allow-list as
`src/testing/no-network.ts`.
"""

import os
import socket
from contextlib import contextmanager
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterator


class NetworkAccessError(RuntimeError):
    """A test tried to reach a host other than this machine."""


def _test_hosts() -> list[str]:
    hosts = [os.environ.get("IMAP_TEST_HOST"), os.environ.get("SMTP_TEST_HOST")]
    api = os.environ.get("MAILPIT_TEST_API")
    if api is not None:
        hosts.append(urlsplit(api).hostname)
    return [host for host in hosts if host]


def allowed_hosts() -> frozenset[str]:
    """This machine and the test servers, by name and by address.

    The addresses too: a client may resolve the host itself and connect to the IP, so in CI (hosts
    `dovecot`, `mailpit`) the socket names an address, not the host.
    """
    hosts = _test_hosts()
    addresses: set[str] = set()
    for host in hosts:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            continue
        addresses.update(str(info[4][0]) for info in infos)
    return frozenset({"127.0.0.1", "::1", "localhost", *hosts, *addresses})


def _host_of(address: object) -> str | None:
    if isinstance(address, tuple) and address and isinstance(address[0], str):
        return address[0]
    return None  # a Unix socket path


@contextmanager
def no_network() -> Iterator[None]:
    """Patch `socket.socket.connect` and `connect_ex` for the duration of the block."""
    allowed = allowed_hosts()
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def check(address: object) -> None:
        host = _host_of(address)
        if host is not None and host not in allowed:
            message = f"test tried to open a connection to {host}"
            raise NetworkAccessError(f"{message}: tests may only reach this machine")

    def connect(self: socket.socket, address: object, /) -> None:
        check(address)
        original_connect(self, address)  # type: ignore[arg-type]

    def connect_ex(self: socket.socket, address: object, /) -> int:
        check(address)
        return original_connect_ex(self, address)  # type: ignore[arg-type]

    socket.socket.connect = connect  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign,assignment]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
