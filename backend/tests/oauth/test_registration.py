"""Dynamic client registration: the redirect allowlist, the throttles and the cap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import update
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

from mailroom.oauth import clients
from mailroom.oauth.config import OAuthSettings, oauth_settings_from_env
from mailroom.oauth.models import (
    OAuthClient,
)
from mailroom.oauth.server import OAuthServer
from mailroom.oauth.throttle import Throttle, Throttles, ipv6_network_key
from tests.oauth.helpers import (
    CLAUDE_CALLBACK,
    ORIGIN,
    approved_code,
    register_client,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.models import User


def test_registration_makes_a_public_client_of_the_one_scope(
    client: TestClient, db_session: Session
) -> None:
    reply = register_client(
        client, token_endpoint_auth_method="client_secret_post", scope="mailroom"
    )
    assert reply.status_code == 201, reply.text
    body = reply.json()
    assert "client_secret" not in body
    assert body["token_endpoint_auth_method"] == "none"
    assert body["scope"] == "mailroom"
    row = db_session.get(OAuthClient, body["client_id"])
    assert row is not None
    assert row.kind == "dcr"
    assert row.redirect_uris == [CLAUDE_CALLBACK]
    assert "client_secret" not in row.metadata_


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:33418/callback",
        "http://127.0.0.1:6274/oauth/callback",
        "http://[::1]:8080/cb",
        "https://claude.com/api/mcp/auth_callback",
    ],
)
def test_registration_accepts_the_allowlist(client: TestClient, uri: str) -> None:
    assert register_client(client, redirect_uris=[uri]).status_code == 201


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.example/api/mcp/auth_callback",
        "https://claude.ai/api/mcp/auth_callback/../evil",
        "http://claude.ai/api/mcp/auth_callback",
        "http://192.168.1.2:8080/callback",
    ],
)
def test_registration_refuses_redirects_off_the_allowlist(client: TestClient, uri: str) -> None:
    reply = register_client(client, redirect_uris=[CLAUDE_CALLBACK, uri])
    assert reply.status_code == 400
    assert reply.json()["error"] == "invalid_redirect_uri"


def test_extra_redirect_uris_are_allowed(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "MAILROOM_OAUTH_EXTRA_REDIRECT_URIS", "https://app.example/cb, https://b.example/x"
    )
    server = OAuthServer(
        oauth_settings_from_env(ORIGIN), lambda: sessions, client_key=lambda _: "k"
    )
    with TestClient(Starlette(routes=server.routes()), base_url=ORIGIN) as client:
        assert register_client(client, redirect_uris=["https://app.example/cb"]).status_code == 201


def test_extra_redirect_uris_must_be_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILROOM_OAUTH_EXTRA_REDIRECT_URIS", "http://app.example/cb")
    with pytest.raises(ValueError, match="https"):
        oauth_settings_from_env(ORIGIN)


def test_registration_refuses_an_overlong_name(client: TestClient) -> None:
    reply = register_client(client, client_name="x" * 101)
    assert reply.status_code == 400
    assert reply.json()["error"] == "invalid_client_metadata"


def test_registration_refuses_other_scopes(client: TestClient) -> None:
    assert register_client(client, scope="mailroom admin").status_code == 400


def test_registration_is_throttled_per_client(
    settings: OAuthSettings, sessions: sessionmaker[Session]
) -> None:
    server = OAuthServer(
        settings,
        lambda: sessions,
        client_key=lambda request: request.headers.get("x-client", ""),
        throttles=Throttles(register=Throttle(2, 3600)),
    )
    with TestClient(Starlette(routes=server.routes()), base_url=ORIGIN) as client:
        body = {"redirect_uris": [CLAUDE_CALLBACK]}
        for _ in range(2):
            assert (
                client.post("/oauth/register", json=body, headers={"x-client": "a"}).status_code
                == 201
            )
        throttled = client.post("/oauth/register", json=body, headers={"x-client": "a"})
        assert throttled.status_code == 429
        assert int(throttled.headers["retry-after"]) > 0
        assert (
            client.post("/oauth/register", json=body, headers={"x-client": "b"}).status_code == 201
        )


def test_throttle_forgets_after_the_window() -> None:
    now = [0.0]
    throttle = Throttle(1, 10, clock=lambda: now[0])
    assert throttle.wait("a") == 0
    assert throttle.wait("a") == 10
    now[0] = 10.0
    assert throttle.wait("a") == 0


def test_registration_is_throttled_per_ipv6_network(
    settings: OAuthSettings, sessions: sessionmaker[Session]
) -> None:
    server = OAuthServer(
        settings,
        lambda: sessions,
        client_key=lambda request: request.headers.get("x-client", ""),
        network_key=lambda request: request.headers.get("x-network"),
        throttles=Throttles(register=Throttle(5, 3600), register_network=Throttle(2, 3600)),
    )
    body = {"redirect_uris": [CLAUDE_CALLBACK]}

    def register(client_key: str, network: str) -> int:
        headers = {"x-client": client_key, "x-network": network}
        return client.post("/oauth/register", json=body, headers=headers).status_code

    with TestClient(Starlette(routes=server.routes()), base_url=ORIGIN) as client:
        assert register("a", "net-1") == 201
        assert register("b", "net-1") == 201
        assert register("c", "net-1") == 429  # a new client key, the same /48
        assert register("c", "net-2") == 201


@pytest.mark.parametrize(
    ("host", "network"),
    [
        ("2001:db8:1:2::5", "2001:db8:1::/48"),
        ("2001:db8:1:ff00::1", "2001:db8:1::/48"),
        ("203.0.113.7", None),
        ("::ffff:203.0.113.7", None),
        ("testclient", None),
    ],
)
def test_the_registration_network_is_an_ipv6_48(host: str, network: str | None) -> None:
    request = Request({"type": "http", "client": (host, 1234), "headers": []})
    assert ipv6_network_key(request) == network


def test_a_full_register_evicts_the_oldest_never_used_client(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clients, "MAX_REGISTERED_CLIENTS", 2)
    oldest = register_client(client).json()["client_id"]
    newer = register_client(client).json()["client_id"]
    db_session.execute(
        update(OAuthClient)
        .where(OAuthClient.client_id == oldest)
        .values(created_at=datetime.now(UTC) - timedelta(hours=1))
    )
    third = register_client(client)
    assert third.status_code == 201
    assert db_session.get(OAuthClient, oldest) is None
    assert db_session.get(OAuthClient, newer) is not None


def test_a_register_full_of_clients_in_use_refuses(
    client: TestClient,
    db_session: Session,
    settings: OAuthSettings,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clients, "MAX_REGISTERED_CLIENTS", 2)
    used = register_client(client).json()["client_id"]
    granted = register_client(client).json()["client_id"]
    db_session.execute(
        update(OAuthClient)
        .where(OAuthClient.client_id == used)
        .values(last_used_at=datetime.now(UTC))
    )
    approved_code(client, db_session, settings, granted, user, "v" * 50)  # a grant, unused
    reply = register_client(client)
    assert reply.status_code == 400
    assert "too many" in reply.json()["error_description"]
    assert db_session.get(OAuthClient, used) is not None
    assert db_session.get(OAuthClient, granted) is not None


def test_registration_forgets_idle_clients(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clients, "MAX_REGISTERED_CLIENTS", 1)
    idle = register_client(client).json()["client_id"]
    db_session.execute(
        update(OAuthClient)
        .where(OAuthClient.client_id == idle)
        .values(created_at=datetime.now(UTC) - timedelta(days=31))
    )
    assert register_client(client).status_code == 201
    assert db_session.get(OAuthClient, idle) is None


def test_a_never_used_client_is_forgotten_after_a_day(
    client: TestClient, db_session: Session
) -> None:
    never_used = register_client(client).json()["client_id"]
    used = register_client(client).json()["client_id"]
    two_days_ago = datetime.now(UTC) - timedelta(days=2)
    db_session.execute(update(OAuthClient).values(created_at=two_days_ago))
    db_session.execute(
        update(OAuthClient)
        .where(OAuthClient.client_id == used)
        .values(last_used_at=datetime.now(UTC) - timedelta(hours=1))
    )
    assert register_client(client).status_code == 201
    assert db_session.get(OAuthClient, never_used) is None
    assert db_session.get(OAuthClient, used) is not None
