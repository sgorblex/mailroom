"""``/oauth/authorize`` and the consent calls: the stored request, its checks and the user's
decision."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, update
from starlette.applications import Starlette
from starlette.testclient import TestClient

from mailroom.oauth import consent
from mailroom.oauth.models import (
    OAuthAuthorizationRequest,
    OAuthGrant,
)
from mailroom.oauth.server import OAuthServer
from mailroom.oauth.throttle import Throttle, Throttles
from tests.oauth.helpers import (
    CLAUDE_CALLBACK,
    ORIGIN,
    RESOURCE,
    authorize,
    consent_request_id,
    query_of,
    register_client,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.models import User
    from mailroom.oauth.config import OAuthSettings


def test_authorize_stores_the_request_and_sends_to_the_consent_screen(
    client: TestClient, db_session: Session, client_id: str
) -> None:
    reply = authorize(client, client_id, "v" * 50)
    request_id = consent_request_id(reply)
    assert reply.headers["cache-control"] == "no-store"
    rows = db_session.scalars(select(OAuthAuthorizationRequest)).all()
    assert len(rows) == 1
    assert rows[0].id_hash != request_id  # only its hash is kept
    assert rows[0].params["resource"] == RESOURCE
    assert rows[0].params["scopes"] == ["mailroom"]


@pytest.mark.parametrize("resource", [None, "https://other.example/mcp", f"{ORIGIN}/mcp/x"])
def test_authorize_needs_this_servers_resource(
    client: TestClient, client_id: str, resource: str | None
) -> None:
    reply = authorize(client, client_id, "v" * 50, resource=resource)
    assert reply.status_code == 302
    assert reply.headers["location"].startswith(f"{CLAUDE_CALLBACK}?")
    query = query_of(reply.headers["location"])
    assert query["error"] == "invalid_request"
    assert query["state"] == "the-state"
    assert query["iss"] == f"{ORIGIN}/oauth"


def test_the_sdks_error_redirects_carry_iss(client: TestClient, client_id: str) -> None:
    reply = authorize(client, client_id, "v" * 50, scope="admin")
    assert reply.status_code == 302
    query = query_of(reply.headers["location"])
    assert query["error"] == "invalid_scope"
    assert query["iss"] == f"{ORIGIN}/oauth"
    assert list(parse_qs(urlsplit(reply.headers["location"]).query)["iss"]) == [query["iss"]]


def test_the_consent_redirect_carries_no_iss(client: TestClient, client_id: str) -> None:
    reply = authorize(client, client_id, "v" * 50)
    consent_request_id(reply)
    assert "iss" not in query_of(reply.headers["location"])


def test_authorize_is_throttled_per_client(
    settings: OAuthSettings, sessions: sessionmaker[Session]
) -> None:
    server = OAuthServer(
        settings,
        lambda: sessions,
        client_key=lambda request: request.headers.get("x-client", ""),
        throttles=Throttles(authorize=Throttle(2, 600)),
    )
    with TestClient(
        Starlette(routes=server.routes()), base_url=ORIGIN, follow_redirects=False
    ) as client:
        client_id = register_client(client).json()["client_id"]
        client.headers["x-client"] = "a"
        for _ in range(2):
            consent_request_id(authorize(client, client_id, "v" * 50))
        throttled = authorize(client, client_id, "v" * 50)
        assert throttled.status_code == 429
        assert int(throttled.headers["retry-after"]) > 0
        client.headers["x-client"] = "b"
        consent_request_id(authorize(client, client_id, "v" * 50))


def test_authorize_caps_the_state(client: TestClient, db_session: Session, client_id: str) -> None:
    consent_request_id(authorize(client, client_id, "v" * 50, state="s" * 1024))
    reply = authorize(client, client_id, "v" * 50, state="s" * 1025)
    assert reply.status_code == 400
    assert "location" not in reply.headers
    assert reply.json()["error"] == "invalid_request"
    assert len(db_session.scalars(select(OAuthAuthorizationRequest)).all()) == 1


def test_authorize_accepts_the_resource_with_a_trailing_slash(
    client: TestClient, client_id: str
) -> None:
    consent_request_id(authorize(client, client_id, "v" * 50, resource=f"{RESOURCE}/"))


def test_authorize_refuses_plain_pkce(client: TestClient, client_id: str) -> None:
    reply = authorize(client, client_id, "v" * 50, code_challenge_method="plain")
    assert "/connect" not in reply.headers.get("location", "")
    assert "error=invalid_request" in reply.headers.get("location", "") or reply.status_code == 400


def test_authorize_refuses_an_unregistered_redirect(client: TestClient, client_id: str) -> None:
    reply = authorize(
        client, client_id, "v" * 50, redirect_uri="https://claude.com/api/mcp/auth_callback"
    )
    assert reply.status_code == 400
    assert "location" not in reply.headers


def test_authorize_refuses_an_unknown_client(client: TestClient) -> None:
    reply = authorize(client, "no-such-client", "v" * 50)
    assert reply.status_code == 400


# Consent


def test_deny_sends_access_denied_back(
    client: TestClient, db_session: Session, settings: OAuthSettings, client_id: str, user: User
) -> None:
    request_id = consent_request_id(authorize(client, client_id, "v" * 50))
    back = consent.deny(db_session, settings, request_id, user.id)
    assert back is not None
    assert query_of(back) == {
        "error": "access_denied",
        "state": "the-state",
        "iss": f"{ORIGIN}/oauth",
    }
    assert consent.approve(db_session, settings, request_id, user.id) is None
    assert db_session.scalars(select(OAuthGrant)).all() == []


def test_a_request_belongs_to_the_first_user_who_opens_it(
    client: TestClient,
    db_session: Session,
    settings: OAuthSettings,
    client_id: str,
    user: User,
    admin: User,
) -> None:
    request_id = consent_request_id(authorize(client, client_id, "v" * 50))
    pending = consent.claim_pending_authorization(db_session, request_id, user.id)
    assert pending is not None
    assert pending.client_name == "Claude"
    assert pending.client_kind == "dcr"
    assert pending.redirect_origin == "https://claude.ai"
    assert pending.scopes == ["mailroom"]
    assert consent.claim_pending_authorization(db_session, request_id, admin.id) is None
    assert consent.approve(db_session, settings, request_id, admin.id) is None
    assert consent.deny(db_session, settings, request_id, admin.id) is None
    assert consent.approve(db_session, settings, request_id, user.id) is not None


def test_an_unknown_or_expired_request_cannot_be_decided(
    client: TestClient, db_session: Session, settings: OAuthSettings, client_id: str, user: User
) -> None:
    assert consent.claim_pending_authorization(db_session, "nope", user.id) is None
    assert consent.approve(db_session, settings, "nope", user.id) is None
    request_id = consent_request_id(authorize(client, client_id, "v" * 50))
    db_session.execute(
        update(OAuthAuthorizationRequest).values(
            expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
    )
    db_session.commit()
    assert consent.claim_pending_authorization(db_session, request_id, user.id) is None
    assert consent.approve(db_session, settings, request_id, user.id) is None
    # Swept by the next request stored.
    consent_request_id(authorize(client, client_id, "v" * 50))
    assert len(db_session.scalars(select(OAuthAuthorizationRequest)).all()) == 1
