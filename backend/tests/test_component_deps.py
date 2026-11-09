from __future__ import annotations

import socket

import pytest

from mailroom_core.testing.no_network import NetworkAccessError


def test_core_plugin_refuses_remote_hosts() -> None:
    """mailroom-core's pytest plugin is loaded: a test cannot reach a host off this machine."""
    with socket.socket() as sock:
        sock.settimeout(1)
        with pytest.raises(NetworkAccessError):
            sock.connect(("192.0.2.1", 993))
