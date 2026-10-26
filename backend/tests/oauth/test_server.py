"""The authorization server's metadata, and when the app mounts it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mailroom.oauth.routes import METADATA_PATH
from tests.oauth.helpers import (
    ORIGIN,
)

if TYPE_CHECKING:
    from starlette.testclient import TestClient


def test_metadata_describes_a_public_client_pkce_server(client: TestClient) -> None:
    reply = client.get(METADATA_PATH)
    assert reply.status_code == 200
    metadata = reply.json()
    assert metadata["issuer"] == f"{ORIGIN}/oauth"
    assert metadata["authorization_endpoint"] == f"{ORIGIN}/oauth/authorize"
    assert metadata["token_endpoint"] == f"{ORIGIN}/oauth/token"
    assert metadata["registration_endpoint"] == f"{ORIGIN}/oauth/register"
    assert metadata["revocation_endpoint"] == f"{ORIGIN}/oauth/revoke"
    assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["client_id_metadata_document_supported"] is True
    assert metadata["scopes_supported"] == ["mailroom"]


@pytest.mark.parametrize(
    ("auth", "public_url", "mounted"),
    [
        (None, ORIGIN, True),
        ("builtin", f"{ORIGIN}/", True),
        ("external", ORIGIN, False),
        ("off", ORIGIN, False),
        (None, None, False),
        (None, "not a url", False),
    ],
)
def test_the_routes_are_mounted_in_builtin_mode_with_a_public_url(
    monkeypatch: pytest.MonkeyPatch, auth: str | None, public_url: str | None, mounted: bool
) -> None:
    from mailroom.api.main import create_application

    for name, value in (("MAILROOM_MCP_AUTH", auth), ("MAILROOM_PUBLIC_URL", public_url)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    application = create_application()
    paths = {getattr(route, "path", None) for route in application.router.routes}
    assert ({METADATA_PATH, "/oauth/authorize", "/oauth/token"} <= paths) is mounted
    server = application.state.oauth_server
    assert (server is not None) is mounted
    if mounted:
        assert server.settings.issuer == f"{ORIGIN}/oauth"
