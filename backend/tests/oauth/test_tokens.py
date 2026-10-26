"""Codes and tokens: PKCE, single-use codes, refresh rotation, revocation."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select, update

from mailroom.oauth.models import (
    OAuthAuthorizationCode,
    OAuthGrant,
    OAuthToken,
)
from tests.oauth.helpers import (
    ORIGIN,
    RESOURCE,
    approved_code,
    exchange_code,
    refresh,
    register_client,
    verify,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User
    from mailroom.oauth.config import OAuthSettings
    from mailroom.oauth.server import OAuthServer


def test_the_code_flow_issues_tokens_for_the_user(
    tokens: dict[str, Any], server: OAuthServer, user: User, client_id: str, db_session: Session
) -> None:
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 3600
    assert tokens["scope"] == "mailroom"
    access = verify(server, tokens["access_token"])
    assert access is not None
    assert access.subject == str(user.id)
    assert access.client_id == client_id
    assert access.resource == RESOURCE
    grant = db_session.scalars(select(OAuthGrant)).one()
    assert access.claims == {"grant_id": str(grant.id)}
    stored = {row.token_hash for row in db_session.scalars(select(OAuthToken))}
    assert tokens["access_token"] not in stored
    assert hashlib.sha256(tokens["access_token"].encode()).hexdigest() in stored


def test_a_wrong_verifier_burns_the_code(
    client: TestClient, db_session: Session, settings: OAuthSettings, client_id: str, user: User
) -> None:
    verifier = secrets.token_urlsafe(48)
    code = approved_code(client, db_session, settings, client_id, user, verifier)
    wrong = exchange_code(client, client_id, code, secrets.token_urlsafe(48))
    assert wrong.status_code == 400
    assert wrong.json()["error"] == "invalid_grant"
    assert exchange_code(client, client_id, code, verifier).json()["error"] == "invalid_grant"


def test_a_code_works_once(
    client: TestClient, db_session: Session, settings: OAuthSettings, client_id: str, user: User
) -> None:
    verifier = secrets.token_urlsafe(48)
    code = approved_code(client, db_session, settings, client_id, user, verifier)
    assert exchange_code(client, client_id, code, verifier).status_code == 200
    again = exchange_code(client, client_id, code, verifier)
    assert again.status_code == 400
    assert again.json()["error"] == "invalid_grant"


def test_replaying_a_code_revokes_what_it_issued(
    client: TestClient,
    server: OAuthServer,
    db_session: Session,
    settings: OAuthSettings,
    client_id: str,
    user: User,
) -> None:
    verifier = secrets.token_urlsafe(48)
    code = approved_code(client, db_session, settings, client_id, user, verifier)
    issued = exchange_code(client, client_id, code, verifier).json()
    used = db_session.scalars(select(OAuthAuthorizationCode)).one()
    assert used.used_at is not None  # kept until it expires
    assert exchange_code(client, client_id, code, verifier).json()["error"] == "invalid_grant"
    assert verify(server, issued["access_token"]) is None
    assert refresh(client, client_id, issued["refresh_token"]).status_code == 400
    assert db_session.scalars(select(OAuthGrant)).all() == []
    assert db_session.scalars(select(OAuthAuthorizationCode)).all() == []


@pytest.mark.parametrize("resource", [RESOURCE, f"{RESOURCE}/"])
def test_the_token_request_may_name_this_resource(
    client: TestClient,
    db_session: Session,
    settings: OAuthSettings,
    client_id: str,
    user: User,
    resource: str,
) -> None:
    verifier = secrets.token_urlsafe(48)
    code = approved_code(client, db_session, settings, client_id, user, verifier)
    reply = exchange_code(client, client_id, code, verifier, resource=resource)
    assert reply.status_code == 200, reply.text
    refreshed = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": reply.json()["refresh_token"],
            "client_id": client_id,
            "resource": resource,
        },
    )
    assert refreshed.status_code == 200, refreshed.text


@pytest.mark.parametrize("resource", ["https://other.example/mcp", f"{ORIGIN}/mcp/x", ""])
def test_the_token_request_refuses_another_resource(
    client: TestClient,
    db_session: Session,
    settings: OAuthSettings,
    client_id: str,
    user: User,
    tokens: dict[str, Any],
    resource: str,
) -> None:
    verifier = secrets.token_urlsafe(48)
    code = approved_code(client, db_session, settings, client_id, user, verifier)
    reply = exchange_code(client, client_id, code, verifier, resource=resource)
    assert reply.status_code == 400
    assert reply.json()["error"] == "invalid_target"
    refreshed = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
            "resource": resource,
        },
    )
    assert refreshed.status_code == 400
    assert refreshed.json()["error"] == "invalid_target"
    # Neither was consumed.
    assert exchange_code(client, client_id, code, verifier).status_code == 200
    assert refresh(client, client_id, tokens["refresh_token"]).status_code == 200


def test_an_expired_code_is_refused(
    client: TestClient, db_session: Session, settings: OAuthSettings, client_id: str, user: User
) -> None:
    verifier = secrets.token_urlsafe(48)
    code = approved_code(client, db_session, settings, client_id, user, verifier)
    db_session.execute(
        update(OAuthAuthorizationCode).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert exchange_code(client, client_id, code, verifier).json()["error"] == "invalid_grant"


def test_a_code_is_bound_to_its_client_and_redirect(
    client: TestClient, db_session: Session, settings: OAuthSettings, client_id: str, user: User
) -> None:
    verifier = secrets.token_urlsafe(48)
    other = register_client(client).json()["client_id"]
    code = approved_code(client, db_session, settings, client_id, user, verifier)
    assert exchange_code(client, other, code, verifier).json()["error"] == "invalid_grant"
    code = approved_code(client, db_session, settings, client_id, user, verifier)
    moved = exchange_code(client, client_id, code, verifier, redirect_uri="http://localhost:1/cb")
    assert moved.status_code == 400


def test_refresh_rotates_both_tokens(
    client: TestClient, server: OAuthServer, tokens: dict[str, Any], client_id: str
) -> None:
    reply = refresh(client, client_id, tokens["refresh_token"])
    assert reply.status_code == 200, reply.text
    rotated = reply.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]
    assert rotated["access_token"] != tokens["access_token"]
    assert verify(server, rotated["access_token"]) is not None
    assert refresh(client, client_id, rotated["refresh_token"]).status_code == 200


def test_reusing_a_spent_refresh_token_ends_the_grant(
    client: TestClient,
    server: OAuthServer,
    tokens: dict[str, Any],
    client_id: str,
    db_session: Session,
) -> None:
    rotated = refresh(client, client_id, tokens["refresh_token"]).json()
    reused = refresh(client, client_id, tokens["refresh_token"])
    assert reused.status_code == 400
    assert reused.json()["error"] == "invalid_grant"
    assert db_session.scalars(select(OAuthGrant)).all() == []
    assert verify(server, rotated["access_token"]) is None
    assert refresh(client, client_id, rotated["refresh_token"]).status_code == 400


def test_a_refresh_token_is_bound_to_its_client(
    client: TestClient, tokens: dict[str, Any], client_id: str
) -> None:
    other = register_client(client).json()["client_id"]
    assert refresh(client, other, tokens["refresh_token"]).status_code == 400
    assert refresh(client, client_id, tokens["refresh_token"]).status_code == 200


def test_an_expired_access_token_is_refused(
    server: OAuthServer, tokens: dict[str, Any], db_session: Session
) -> None:
    db_session.execute(
        update(OAuthToken)
        .where(OAuthToken.kind == "access")
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert verify(server, tokens["access_token"]) is None
    assert verify(server, tokens["refresh_token"]) is None  # a refresh token is no access token


def test_using_an_access_token_touches_its_grant_at_most_once_a_minute(
    server: OAuthServer, tokens: dict[str, Any], db_session: Session
) -> None:
    def last_used() -> datetime | None:
        db_session.expire_all()
        return db_session.scalars(select(OAuthGrant.last_used_at)).one()

    exchanged = last_used()
    assert exchanged is not None
    assert verify(server, tokens["access_token"]) is not None
    assert last_used() == exchanged  # within the minute: no write
    earlier = datetime.now(UTC) - timedelta(minutes=2)
    db_session.execute(update(OAuthGrant).values(last_used_at=earlier))
    assert verify(server, tokens["access_token"]) is not None
    touched = last_used()
    assert touched is not None
    assert touched > earlier + timedelta(minutes=1)


@pytest.mark.parametrize("which", ["access_token", "refresh_token"])
def test_revoking_either_token_ends_the_grant(
    client: TestClient, server: OAuthServer, tokens: dict[str, Any], client_id: str, which: str
) -> None:
    reply = client.post("/oauth/revoke", data={"token": tokens[which], "client_id": client_id})
    assert reply.status_code == 200
    assert verify(server, tokens["access_token"]) is None
    assert refresh(client, client_id, tokens["refresh_token"]).status_code == 400


def test_revoking_logs_whose_grant_ended(
    client: TestClient,
    tokens: dict[str, Any],
    client_id: str,
    user: User,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    reply = client.post(
        "/oauth/revoke", data={"token": tokens["access_token"], "client_id": client_id}
    )
    assert reply.status_code == 200
    revoked = [record for record in caplog.records if record.msg == "oauth_grant_revoked"]
    assert [(line.user_id, line.client_id) for line in revoked] == [(user.id, client_id)]  # type: ignore[attr-defined]


def test_revoking_another_clients_token_does_nothing(
    client: TestClient, server: OAuthServer, tokens: dict[str, Any]
) -> None:
    other = register_client(client).json()["client_id"]
    reply = client.post("/oauth/revoke", data={"token": tokens["access_token"], "client_id": other})
    assert reply.status_code == 200
    assert verify(server, tokens["access_token"]) is not None


def test_removing_the_user_removes_the_grant(
    server: OAuthServer, tokens: dict[str, Any], user: User, db_session: Session
) -> None:
    db_session.delete(user)
    db_session.flush()
    assert verify(server, tokens["access_token"]) is None
