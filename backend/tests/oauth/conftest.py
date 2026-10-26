"""Fixtures for the built-in authorization server's tests: its settings, the server on a
Starlette app, a registered client and its first tokens."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from mailroom.oauth.config import OAuthSettings
from mailroom.oauth.server import OAuthServer
from tests.oauth.helpers import (
    ORIGIN,
    Documents,
    approved_code,
    exchange_code,
    register_client,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.models import User


@pytest.fixture
def documents() -> Documents:
    return Documents()


@pytest.fixture
def settings() -> OAuthSettings:
    return OAuthSettings(public_origin=ORIGIN)


@pytest.fixture
def server(
    settings: OAuthSettings, sessions: sessionmaker[Session], documents: Documents
) -> OAuthServer:
    return OAuthServer(
        settings,
        lambda: sessions,
        client_key=lambda request: "client",
        cimd_transport=httpx.MockTransport(documents.handle),
    )


@pytest.fixture
def client(server: OAuthServer) -> Iterator[TestClient]:
    with TestClient(
        Starlette(routes=server.routes()), base_url=ORIGIN, follow_redirects=False
    ) as test_client:
        yield test_client


@pytest.fixture
def client_id(client: TestClient) -> str:
    reply = register_client(client)
    assert reply.status_code == 201, reply.text
    return str(reply.json()["client_id"])


@pytest.fixture
def tokens(
    client: TestClient, db_session: Session, settings: OAuthSettings, client_id: str, user: User
) -> dict[str, Any]:
    verifier = secrets.token_urlsafe(48)
    code = approved_code(client, db_session, settings, client_id, user, verifier)
    reply = exchange_code(client, client_id, code, verifier)
    assert reply.status_code == 200, reply.text
    assert reply.headers["cache-control"] == "no-store"
    body: dict[str, Any] = reply.json()
    return body
