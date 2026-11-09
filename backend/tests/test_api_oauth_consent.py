"""The consent screen's and the connected apps' API, on an app with the built-in authorization
server on, end to end: a client registers and authorizes, the signed-in user approves through
``/api/v1/oauth/requests``, the client exchanges the code and calls ``/mcp`` with the token, and
revoking under ``/api/v1/oauth/grants`` stops it."""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, update
from starlette.testclient import TestClient

from mailroom.api.main import create_application
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.config.features import ALL_FEATURES
from mailroom.mcp.groups import selected_groups
from mailroom.mcp.settings import McpSettings
from mailroom.oauth.models import OAuthAuthorizationRequest, OAuthGrant
from mailroom.oauth.throttle import Throttle

if TYPE_CHECKING:
    from collections.abc import Iterator

    import httpx
    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.models import User

PUBLIC_URL = "https://mailroom.example"
RESOURCE = f"{PUBLIC_URL}/mcp"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
LISTING = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


@pytest.fixture(scope="module")
def _builtin_application(_worker_engine: Engine) -> Iterator[tuple[TestClient, FastAPI]]:
    settings = McpSettings(public_url=PUBLIC_URL, auth="builtin", groups=selected_groups(None))
    application = create_application(features=ALL_FEATURES, mcp_settings=settings)
    assert application.state.oauth_server is not None
    # One client for the module: the MCP session manager runs in its event loop.
    with TestClient(application, base_url=PUBLIC_URL, follow_redirects=False) as client:
        yield client, application


class Flow:
    """A client's side of the flow, and the browser's on the consent screen."""

    def __init__(self, client: TestClient, db: Session) -> None:
        self.client = client
        self.db = db
        self.verifier = secrets.token_urlsafe(48)
        self.client_id = ""

    def register(self, name: str = "Claude") -> str:
        reply = self.client.post(
            "/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": name}
        )
        assert reply.status_code == 201, reply.text
        self.client_id = str(reply.json()["client_id"])
        return self.client_id

    def authorize(self) -> str:
        """A new authorization request's id, as the consent screen gets it."""
        if not self.client_id:
            self.register()
        reply = self.client.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": CALLBACK,
                "code_challenge": _challenge(self.verifier),
                "code_challenge_method": "S256",
                "state": "the-state",
                "resource": RESOURCE,
            },
        )
        assert reply.status_code == 302, reply.text
        location = reply.headers["location"]
        assert location.startswith(f"{PUBLIC_URL}/connect?")
        return _query(location)["request"]

    def sign_in(self, user: User) -> None:
        self.client.cookies.set(SESSION_COOKIE_NAME, create_session(self.db, user))

    def sign_out(self) -> None:
        self.client.cookies.clear()

    def show(self, request_id: str) -> httpx.Response:
        return self.client.get(f"/api/v1/oauth/requests/{request_id}")

    def decide(self, request_id: str, decision: str, **headers: str) -> httpx.Response:
        return self.client.post(
            f"/api/v1/oauth/requests/{request_id}", json={"decision": decision}, headers=headers
        )

    def approved_tokens(self, user: User) -> dict[str, Any]:
        self.sign_in(user)
        decided = self.decide(self.authorize(), "approve")
        assert decided.status_code == 200, decided.text
        code = _query(decided.json()["redirect_to"])["code"]
        self.sign_out()
        reply = self.client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": CALLBACK,
                "client_id": self.client_id,
                "code_verifier": self.verifier,
            },
        )
        assert reply.status_code == 200, reply.text
        tokens: dict[str, Any] = reply.json()
        return tokens

    def mcp(self, token: str) -> httpx.Response:
        return self.client.post(
            "/mcp", json=LISTING, headers={**MCP_HEADERS, "authorization": f"Bearer {token}"}
        )


@pytest.fixture
def flow(
    _builtin_application: tuple[TestClient, FastAPI],
    sessions: sessionmaker[Session],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Flow]:
    """The app's database work inside the test's transaction, and no throttles."""
    client, application = _builtin_application
    monkeypatch.setattr(application.state, "session_maker", sessions)
    monkeypatch.setattr(Throttle, "wait", lambda self, key: 0)
    client.cookies.clear()
    try:
        yield Flow(client, db_session)
    finally:
        client.cookies.clear()


def test_the_screen_shows_the_request_to_the_signed_in_user(flow: Flow, user: User) -> None:
    request_id = flow.authorize()
    assert flow.show(request_id).status_code == 401
    flow.sign_in(user)
    shown = flow.show(request_id)
    assert shown.status_code == 200, shown.text
    assert shown.headers["cache-control"] == "no-store"
    body = shown.json()
    assert body["client_name"] == "Claude"
    assert body["client_host"] is None  # registered by the client itself, not a document
    assert body["redirect_host"] == "claude.ai"
    assert body["scopes"] == ["mailroom"]
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(UTC)


def test_approving_issues_a_code_whose_token_works_at_mcp(flow: Flow, user: User) -> None:
    tokens = flow.approved_tokens(user)
    answer = flow.mcp(tokens["access_token"])
    assert answer.status_code == 200, answer.text
    assert any(tool["name"] == "accounts_list" for tool in answer.json()["result"]["tools"])
    assert flow.mcp("not-a-token").status_code == 401


def test_approving_answers_the_redirect_with_code_and_state(
    flow: Flow, user: User, db_session: Session
) -> None:
    flow.sign_in(user)
    request_id = flow.authorize()
    decided = flow.decide(request_id, "approve")
    assert decided.status_code == 200, decided.text
    redirect_to = decided.json()["redirect_to"]
    assert redirect_to.startswith(f"{CALLBACK}?")
    query = _query(redirect_to)
    assert query["state"] == "the-state"
    assert query["iss"] == f"{PUBLIC_URL}/oauth"
    assert query["code"]
    grants = db_session.scalars(select(OAuthGrant)).all()
    assert [(grant.user_id, grant.client_id) for grant in grants] == [(user.id, flow.client_id)]
    # Used up.
    assert flow.decide(request_id, "approve").status_code == 404
    assert flow.show(request_id).status_code == 404


def test_denying_sends_access_denied_back(flow: Flow, user: User, db_session: Session) -> None:
    flow.sign_in(user)
    decided = flow.decide(flow.authorize(), "deny")
    assert decided.status_code == 200, decided.text
    assert _query(decided.json()["redirect_to"]) == {
        "error": "access_denied",
        "state": "the-state",
        "iss": f"{PUBLIC_URL}/oauth",
    }
    assert db_session.scalars(select(OAuthGrant)).all() == []


def test_another_users_request_is_not_found(flow: Flow, user: User, admin: User) -> None:
    request_id = flow.authorize()
    flow.sign_in(user)
    assert flow.show(request_id).status_code == 200
    flow.sign_in(admin)
    assert flow.show(request_id).status_code == 404
    assert flow.decide(request_id, "approve").status_code == 404
    assert flow.decide(request_id, "deny").status_code == 404
    flow.sign_in(user)
    assert flow.decide(request_id, "approve").status_code == 200


def test_an_unknown_or_expired_request_is_not_found(
    flow: Flow, user: User, db_session: Session
) -> None:
    flow.sign_in(user)
    assert flow.show("no-such-request").status_code == 404
    assert flow.decide("no-such-request", "approve").status_code == 404
    request_id = flow.authorize()
    db_session.execute(
        update(OAuthAuthorizationRequest).values(
            expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
    )
    db_session.commit()
    assert flow.show(request_id).status_code == 404
    assert flow.decide(request_id, "approve").status_code == 404


def test_a_cross_site_decision_is_refused(flow: Flow, user: User, db_session: Session) -> None:
    flow.sign_in(user)
    request_id = flow.authorize()
    refused = flow.decide(request_id, "approve", **{"sec-fetch-site": "cross-site"})
    assert refused.status_code == 403
    refused = flow.decide(request_id, "approve", origin="https://evil.example")
    assert refused.status_code == 403
    assert db_session.scalars(select(OAuthGrant)).all() == []
    assert (
        flow.decide(request_id, "approve", **{"sec-fetch-site": "same-origin"}).status_code == 200
    )


@pytest.mark.parametrize(
    "body", [{"decision": "maybe"}, {}, {"decision": "approve", "scopes": ["more"]}]
)
def test_a_decision_must_be_approve_or_deny(flow: Flow, user: User, body: dict[str, Any]) -> None:
    flow.sign_in(user)
    request_id = flow.authorize()
    reply = flow.client.post(f"/api/v1/oauth/requests/{request_id}", json=body)
    assert reply.status_code == 422
    assert flow.show(request_id).status_code == 200


def test_without_the_builtin_server_there_are_no_requests(user_client: TestClient) -> None:
    # The shared app runs with the MCP server off.
    assert user_client.get("/api/v1/oauth/requests/abc").status_code == 404
    reply = user_client.post("/api/v1/oauth/requests/abc", json={"decision": "approve"})
    assert reply.status_code == 404


# Connected apps


def _grants(flow: Flow) -> httpx.Response:
    return flow.client.get("/api/v1/oauth/grants")


def _revoke(flow: Flow, grant_id: int, **headers: str) -> httpx.Response:
    return flow.client.delete(f"/api/v1/oauth/grants/{grant_id}", headers=headers)


def test_connected_apps_lists_the_users_own_grants(flow: Flow, user: User, admin: User) -> None:
    flow.approved_tokens(user)
    flow.approved_tokens(admin)
    flow.sign_in(user)
    listed = _grants(flow)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["auth"] == "builtin"
    [app] = body["grants"]
    assert app["client_name"] == "Claude"
    assert app["client_host"] is None
    assert datetime.fromisoformat(app["created_at"]) <= datetime.now(UTC)
    assert app["last_used_at"] is not None  # the token exchange


def test_revoking_a_connected_app_stops_its_tokens(
    flow: Flow, user: User, db_session: Session
) -> None:
    tokens = flow.approved_tokens(user)
    assert flow.mcp(tokens["access_token"]).status_code == 200
    flow.sign_in(user)
    [app] = _grants(flow).json()["grants"]
    assert _revoke(flow, app["id"]).status_code == 204
    assert _grants(flow).json()["grants"] == []
    assert db_session.scalars(select(OAuthGrant)).all() == []
    flow.sign_out()
    revoked = flow.mcp(tokens["access_token"])
    assert revoked.status_code == 401
    refreshed = flow.client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": flow.client_id,
        },
    )
    assert refreshed.status_code == 400
    assert refreshed.json()["error"] == "invalid_grant"
    flow.sign_in(user)
    assert _revoke(flow, app["id"]).status_code == 404


def test_another_users_connected_app_cannot_be_revoked(flow: Flow, user: User, admin: User) -> None:
    tokens = flow.approved_tokens(user)
    flow.sign_in(user)
    [app] = _grants(flow).json()["grants"]
    flow.sign_in(admin)
    assert _grants(flow).json()["grants"] == []
    assert _revoke(flow, app["id"]).status_code == 404
    assert flow.mcp(tokens["access_token"]).status_code == 200


def test_connected_apps_need_a_session_and_the_same_site(flow: Flow, user: User) -> None:
    flow.approved_tokens(user)
    assert _grants(flow).status_code == 401
    assert _revoke(flow, 1).status_code == 401
    flow.sign_in(user)
    [app] = _grants(flow).json()["grants"]
    assert _revoke(flow, app["id"], **{"sec-fetch-site": "cross-site"}).status_code == 403
    assert len(_grants(flow).json()["grants"]) == 1
    assert _revoke(flow, 0).status_code == 422


@pytest.mark.parametrize(("settings", "auth"), [(None, "off"), ("external", "external")])
def test_connected_apps_say_how_access_is_managed(
    user_client: TestClient,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    settings: str | None,
    auth: str,
) -> None:
    if settings is not None:
        monkeypatch.setattr(
            application.state,
            "mcp_settings",
            McpSettings(public_url=PUBLIC_URL, auth="external", groups=selected_groups(None)),
        )
    listed = user_client.get("/api/v1/oauth/grants")
    assert listed.status_code == 200, listed.text
    assert listed.json() == {"auth": auth, "grants": []}
