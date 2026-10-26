"""Client ID metadata documents, served by a MockTransport: fetching, caching and refusal."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import anyio
import httpx
import pytest
from sqlalchemy import update
from starlette.applications import Starlette
from starlette.testclient import TestClient

from mailroom.oauth import clients
from mailroom.oauth.clients import RecentFailures
from mailroom.oauth.config import OAuthSettings, oauth_settings_from_env
from mailroom.oauth.models import (
    OAuthClient,
)
from mailroom.oauth.server import OAuthServer
from mailroom.oauth.throttle import Throttle, Throttles
from tests.oauth.helpers import (
    CIMD_URL,
    CLAUDE_CALLBACK,
    ORIGIN,
    Documents,
    approved_code,
    authorize,
    consent_request_id,
    exchange_code,
    verify,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.models import User


def _document(**fields: Any) -> dict[str, Any]:
    return {
        "client_id": CIMD_URL,
        "client_name": "Claude",
        "redirect_uris": [CLAUDE_CALLBACK],
        "token_endpoint_auth_method": "none",
        **fields,
    }


def test_a_metadata_document_is_a_client(
    client: TestClient, documents: Documents, db_session: Session
) -> None:
    documents.serve(CIMD_URL, _document())
    consent_request_id(authorize(client, CIMD_URL, "v" * 50))
    row = db_session.get(OAuthClient, CIMD_URL)
    assert row is not None
    assert row.kind == "cimd"
    consent_request_id(authorize(client, CIMD_URL, "v" * 50))
    assert documents.fetched == [CIMD_URL]  # cached


def test_a_stale_document_is_fetched_again(
    client: TestClient, documents: Documents, db_session: Session
) -> None:
    documents.serve(CIMD_URL, _document())
    consent_request_id(authorize(client, CIMD_URL, "v" * 50))
    db_session.execute(
        update(OAuthClient).values(fetched_at=datetime.now(UTC) - timedelta(hours=25))
    )
    documents.serve(CIMD_URL, _document(redirect_uris=["http://localhost:1/cb"]))
    reply = authorize(client, CIMD_URL, "v" * 50)
    assert reply.status_code == 400  # the cached redirect URI is gone
    assert documents.fetched == [CIMD_URL, CIMD_URL]


def _cache_document_fetched(client: TestClient, db_session: Session, ago: timedelta) -> None:
    consent_request_id(authorize(client, CIMD_URL, "v" * 50))
    db_session.execute(update(OAuthClient).values(fetched_at=datetime.now(UTC) - ago))


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500),
        httpx.Response(404),
        httpx.Response(302, headers={"location": "https://claude.ai/elsewhere"}),
        httpx.ConnectError("refused"),
    ],
    ids=["server-error", "not-found", "redirect", "connect-error"],
)
def test_an_unreachable_document_serves_from_the_stale_cache(
    client: TestClient,
    documents: Documents,
    db_session: Session,
    failure: httpx.Response | Exception,
) -> None:
    documents.serve(CIMD_URL, _document())
    _cache_document_fetched(client, db_session, timedelta(days=3))
    documents.replies[CIMD_URL] = failure
    consent_request_id(authorize(client, CIMD_URL, "v" * 50))
    assert len(documents.fetched) == 2
    row = db_session.get(OAuthClient, CIMD_URL)
    assert row is not None
    assert row.fetched_at is not None
    assert row.fetched_at < datetime.now(UTC) - timedelta(days=2)  # still the last good fetch


def test_the_stale_cache_ends_after_its_grace(
    client: TestClient, documents: Documents, db_session: Session
) -> None:
    documents.serve(CIMD_URL, _document())
    _cache_document_fetched(client, db_session, timedelta(days=8))
    documents.replies[CIMD_URL] = httpx.Response(500)
    assert authorize(client, CIMD_URL, "v" * 50).status_code == 400


def test_an_invalid_document_is_not_served_from_the_stale_cache(
    client: TestClient, documents: Documents, db_session: Session
) -> None:
    documents.serve(CIMD_URL, _document())
    _cache_document_fetched(client, db_session, timedelta(days=2))
    documents.serve(CIMD_URL, _document(client_id="https://claude.ai/other"))
    assert authorize(client, CIMD_URL, "v" * 50).status_code == 400


def test_a_failed_fetch_is_not_repeated_for_a_while(
    client: TestClient, documents: Documents
) -> None:
    documents.replies[CIMD_URL] = httpx.Response(500)
    assert authorize(client, CIMD_URL, "v" * 50).status_code == 400
    documents.serve(CIMD_URL, _document())
    assert authorize(client, CIMD_URL, "v" * 50).status_code == 400
    assert documents.fetched == [CIMD_URL]


def test_recent_failures_expire() -> None:
    now = [0.0]
    failures = RecentFailures(ttl=300, clock=lambda: now[0])
    failures.remember(CIMD_URL, "invalid")
    assert failures.get(CIMD_URL) == "invalid"
    now[0] = 299.0
    assert failures.get(CIMD_URL) == "invalid"
    now[0] = 300.0
    assert failures.get(CIMD_URL) is None


def test_document_fetches_are_throttled_per_client(
    settings: OAuthSettings, sessions: sessionmaker[Session], documents: Documents
) -> None:
    server = OAuthServer(
        settings,
        lambda: sessions,
        client_key=lambda request: request.headers.get("x-client", ""),
        cimd_transport=httpx.MockTransport(documents.handle),
        throttles=Throttles(cimd_fetch=Throttle(1, 600)),
    )
    first, second = f"{CIMD_URL}/one", f"{CIMD_URL}/two"
    with TestClient(
        Starlette(routes=server.routes()), base_url=ORIGIN, follow_redirects=False
    ) as client:
        client.headers["x-client"] = "a"
        assert authorize(client, first, "v" * 50).status_code == 400
        assert authorize(client, second, "v" * 50).status_code == 400
        assert documents.fetched == [first]
        client.headers["x-client"] = "b"
        documents.serve(second, _document(client_id=second))
        consent_request_id(authorize(client, second, "v" * 50))
        assert documents.fetched == [first, second]


def test_the_full_flow_works_for_a_document_client(
    client: TestClient,
    documents: Documents,
    db_session: Session,
    settings: OAuthSettings,
    user: User,
    server: OAuthServer,
) -> None:
    documents.serve(CIMD_URL, _document())
    verifier = secrets.token_urlsafe(48)
    code = approved_code(client, db_session, settings, CIMD_URL, user, verifier)
    reply = exchange_code(client, CIMD_URL, code, verifier)
    assert reply.status_code == 200, reply.text
    assert verify(server, reply.json()["access_token"]).client_id == CIMD_URL


@pytest.mark.parametrize(
    ("url", "reply"),
    [
        ("https://evil.example/oauth/metadata", None),
        ("https://claude.ai:8443/oauth/metadata", None),
        ("https://claude.ai/", None),
        ("https://claude.ai/oauth/../metadata", None),
        (CIMD_URL, httpx.Response(200, json=_document(client_id="https://claude.ai/other"))),
        (CIMD_URL, httpx.Response(200, json=_document(redirect_uris=["https://evil.example/cb"]))),
        (
            CIMD_URL,
            httpx.Response(200, json=_document(token_endpoint_auth_method="private_key_jwt")),
        ),
        (CIMD_URL, httpx.Response(200, content=b'{"client_id": "' + b"x" * 70_000 + b'"}')),
        (CIMD_URL, httpx.Response(302, headers={"location": "https://claude.ai/elsewhere"})),
        (CIMD_URL, httpx.Response(200, content=b"not json")),
        (CIMD_URL, httpx.Response(200, json=[1, 2])),
        (CIMD_URL, httpx.Response(500)),
    ],
    ids=[
        "host",
        "port",
        "no-path",
        "dot-segment",
        "client-id-mismatch",
        "redirect-not-allowed",
        "confidential",
        "too-large",
        "redirect",
        "not-json",
        "not-an-object",
        "server-error",
    ],
)
def test_a_bad_metadata_document_is_no_client(
    client: TestClient,
    documents: Documents,
    db_session: Session,
    url: str,
    reply: httpx.Response | None,
) -> None:
    if reply is not None:
        documents.replies[url] = reply
    assert authorize(client, url, "v" * 50).status_code == 400
    assert db_session.get(OAuthClient, url) is None
    if reply is None:
        assert documents.fetched == []  # never fetched


def test_documents_are_fetched_only_from_the_configured_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAILROOM_OAUTH_CIMD_HOSTS", "")
    settings = oauth_settings_from_env(ORIGIN)
    assert clients.cimd_url_problem(settings, CIMD_URL) == "host not allowed"
    monkeypatch.setenv("MAILROOM_OAUTH_CIMD_HOSTS", "Example.org, claude.ai")
    settings = oauth_settings_from_env(ORIGIN)
    assert clients.cimd_url_problem(settings, "https://example.org/client") is None


def test_a_slow_document_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clients, "CIMD_TIMEOUT_SECONDS", 0.05)

    async def slow(request: httpx.Request) -> httpx.Response:
        _ = request
        await anyio.sleep(1)
        return httpx.Response(200, json=_document())

    async def fetch() -> None:
        await clients.fetch_client_document(CIMD_URL, httpx.MockTransport(slow))

    with pytest.raises(clients.CimdRefused, match="timed out"):
        anyio.run(fetch)
