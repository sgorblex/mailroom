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
from mailroom.dev.fake_idp import FakeIdp
from mailroom.mcp.groups import selected_groups
from mailroom.mcp.settings import McpSettings
from mailroom.models import SsoLoginState
from mailroom.oauth.models import OAuthAuthorizationRequest, OAuthGrant
from mailroom.oauth.throttle import Throttle
from tests.test_api_accounts import new_account

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
APPROVED = ["mail_read", "mail_organize", "filters_read"]
"""What the flows approve unless a test chooses."""


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

    def decide(
        self,
        request_id: str,
        decision: str,
        groups: list[str] | None = None,
        **headers: str,
    ) -> httpx.Response:
        body: dict[str, Any] = {"decision": decision}
        if decision == "approve":
            body["groups"] = APPROVED if groups is None else groups
        return self.client.post(f"/api/v1/oauth/requests/{request_id}", json=body, headers=headers)

    def approved_tokens(self, user: User, groups: list[str] | None = None) -> dict[str, Any]:
        self.sign_in(user)
        decided = self.decide(self.authorize(), "approve", groups)
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

    def listed_tools(self, token: str) -> set[str]:
        answer = self.mcp(token)
        assert answer.status_code == 200, answer.text
        return {tool["name"] for tool in answer.json()["result"]["tools"]}


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


def test_the_screen_offers_the_served_groups_with_their_defaults(flow: Flow, user: User) -> None:
    flow.sign_in(user)
    offered = {group["name"]: group for group in flow.show(flow.authorize()).json()["groups"]}
    assert set(offered) == {group.name for group in selected_groups(None)}
    assert offered["mail_read"]["default"] is True
    assert offered["mail_read"]["requires"] == []
    assert offered["mail_send"]["default"] is False
    assert offered["mail_send"]["requires"] == ["mail_read"]
    assert offered["mail_folders"]["default"] is False
    assert offered["mail_organize"]["description"] == "move, trash and flag mail"


def test_a_client_sees_and_calls_only_the_groups_approved(flow: Flow, user: User) -> None:
    tokens = flow.approved_tokens(user, ["mail_read"])
    listed = flow.listed_tools(tokens["access_token"])
    assert {"accounts_list", "messages_list", "message_read"} <= listed
    assert "messages_move" not in listed
    assert "rule_sets_list" not in listed
    called = flow.client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "messages_trash", "arguments": {"refs": ["x"]}},
        },
        headers={**MCP_HEADERS, "authorization": f"Bearer {tokens['access_token']}"},
    )
    assert called.status_code == 200, called.text
    result = called.json()["result"]
    assert result["isError"] is True
    assert "not approved for the mail_organize tools" in result["content"][0]["text"]


@pytest.mark.parametrize(
    ("groups", "reason"),
    [([], "at least one"), (["mail_send"], "mail_send needs mail_read"), (["nope"], "nope")],
)
def test_a_choice_that_cannot_be_approved_leaves_the_request_open(
    flow: Flow, user: User, db_session: Session, groups: list[str], reason: str
) -> None:
    flow.sign_in(user)
    request_id = flow.authorize()
    reply = flow.decide(request_id, "approve", groups)
    assert reply.status_code == 400, reply.text
    assert reply.json()["detail"]["code"] == "groups_not_approvable"
    assert reason in reply.json()["detail"]["message"]
    assert db_session.scalars(select(OAuthGrant)).all() == []
    assert flow.show(request_id).status_code == 200


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


def test_builtin_mode_has_no_sso_sign_in_for_a_request(
    flow: Flow, monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    from tests.test_api_sso import CLIENT, ISSUER, START, configure, sso_error

    application: FastAPI = flow.client.app  # type: ignore[assignment]  # the app under test
    configure(application, monkeypatch, FakeIdp(issuer=ISSUER, client_id=CLIENT))
    request_id = flow.authorize()
    answer = flow.client.get(START, params={"intent": "mcp", "request": request_id})
    assert sso_error(answer) == "state_invalid"
    assert db_session.scalars(select(SsoLoginState)).all() == []


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
    assert listed.json() == {"auth": auth, "grants": [], "groups": [], "accounts": []}


def _set_groups(flow: Flow, grant_id: int, groups: list[str]) -> httpx.Response:
    return flow.client.put(f"/api/v1/oauth/grants/{grant_id}/groups", json={"groups": groups})


def test_connected_apps_list_their_groups_and_the_served_ones(flow: Flow, user: User) -> None:
    flow.approved_tokens(user, ["mail_read", "mail_organize"])
    flow.sign_in(user)
    body = _grants(flow).json()
    [app] = body["grants"]
    assert app["groups"] == ["mail_read", "mail_organize"]
    served = [group["name"] for group in body["groups"]]
    assert served == [group.name for group in selected_groups(None)]
    assert {"name", "description", "requires", "default"} <= set(body["groups"][0])


def test_groups_approved_on_a_connected_app_reach_its_next_call(
    flow: Flow, user: User, db_session: Session
) -> None:
    tokens = flow.approved_tokens(user, ["mail_read"])
    assert "messages_move" not in flow.listed_tools(tokens["access_token"])
    flow.sign_in(user)
    [app] = _grants(flow).json()["grants"]
    assert _set_groups(flow, app["id"], ["mail_read", "mail_organize"]).status_code == 204
    assert "messages_move" in flow.listed_tools(tokens["access_token"])
    assert _set_groups(flow, app["id"], ["filters_read"]).status_code == 204
    listed = flow.listed_tools(tokens["access_token"])
    assert "messages_move" not in listed
    assert "messages_list" not in listed
    assert "rule_sets_list" in listed
    grant = db_session.get(OAuthGrant, app["id"])
    assert grant is not None
    assert grant.tool_groups == ["filters_read"]


def test_approved_groups_the_instance_does_not_serve_are_kept(
    flow: Flow, user: User, db_session: Session
) -> None:
    flow.approved_tokens(user, ["mail_read"])
    grant = db_session.scalars(select(OAuthGrant)).one()
    grant.tool_groups = ["mail_read", "mail_transfer"]  # approved while it was served
    db_session.flush()
    flow.sign_in(user)
    [app] = _grants(flow).json()["grants"]
    assert app["groups"] == ["mail_read"]
    assert _set_groups(flow, app["id"], ["mail_read", "mail_organize"]).status_code == 204
    db_session.refresh(grant)
    assert grant.tool_groups == ["mail_read", "mail_organize", "mail_transfer"]


def test_another_users_connected_app_keeps_its_groups(
    flow: Flow, user: User, admin: User, db_session: Session
) -> None:
    flow.approved_tokens(admin, ["mail_read"])
    grant = db_session.scalars(select(OAuthGrant)).one()
    flow.sign_in(user)
    assert _set_groups(flow, grant.id, ["mail_read", "mail_send"]).status_code == 404
    db_session.refresh(grant)
    assert grant.tool_groups == ["mail_read"]


def test_a_connected_apps_groups_must_be_approvable(flow: Flow, user: User) -> None:
    flow.approved_tokens(user, ["mail_read"])
    flow.sign_in(user)
    [app] = _grants(flow).json()["grants"]
    reply = _set_groups(flow, app["id"], ["mail_organize"])
    assert reply.status_code == 400
    assert reply.json()["detail"]["code"] == "groups_not_approvable"


# Limits: the accounts an app is narrowed to, and its send ceiling


@pytest.fixture
def accounts(flow: Flow, user: User) -> list[int]:
    """The user's accounts, work and home, on servers nothing reaches."""
    flow.sign_in(user)
    added = []
    for label in ("work", "home"):
        reply = flow.client.post("/api/v1/accounts", json=new_account(label))
        assert reply.status_code == 201, reply.text
        added.append(int(reply.json()["id"]))
    flow.sign_out()
    return added


def _approve_limited(flow: Flow, request_id: str, **limits: Any) -> httpx.Response:
    body = {"decision": "approve", "groups": ["mail_read"], **limits}
    return flow.client.post(f"/api/v1/oauth/requests/{request_id}", json=body)


def _set_limits(flow: Flow, grant_id: int, **limits: Any) -> httpx.Response:
    return flow.client.put(f"/api/v1/oauth/grants/{grant_id}/limits", json=limits)


def _accounts_listed(flow: Flow, token: str) -> list[tuple[int, str]]:
    answer = flow.client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "accounts_list", "arguments": {}},
        },
        headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
    )
    listed = answer.json()["result"]["structuredContent"]["accounts"]
    return [(account["id"], account["send_mode"]) for account in listed]


def test_an_app_is_limited_at_consent_and_later(
    flow: Flow, user: User, db_session: Session, accounts: list[int]
) -> None:
    work, home = accounts
    flow.sign_in(user)
    request_id = flow.authorize()
    named = [{"id": work, "name": "work@example.org"}, {"id": home, "name": "home@example.org"}]
    assert flow.show(request_id).json()["accounts"] == named
    decided = _approve_limited(flow, request_id, account_ids=[home, home], send_ceiling="off")
    assert decided.status_code == 200, decided.text
    grant = db_session.scalars(select(OAuthGrant)).one()
    assert (grant.account_ids, grant.send_ceiling) == ([home], "off")
    code = _query(decided.json()["redirect_to"])["code"]
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK,
        "client_id": flow.client_id,
        "code_verifier": flow.verifier,
    }
    token = flow.client.post("/oauth/token", data=form).json()["access_token"]
    assert _accounts_listed(flow, token) == [(home, "off")]

    body = _grants(flow).json()
    assert body["accounts"] == named
    [app] = body["grants"]
    assert (app["account_ids"], app["send_ceiling"]) == ([home], "off")
    # The next call of the same token sees a change.
    assert _set_limits(flow, app["id"], account_ids=None, send_ceiling=None).status_code == 204
    assert _accounts_listed(flow, token) == [(work, "drafts"), (home, "drafts")]
    assert (
        _set_limits(flow, app["id"], account_ids=[work], send_ceiling="direct").status_code == 204
    )
    assert _accounts_listed(flow, token) == [(work, "drafts")]
    db_session.refresh(grant)
    assert grant.tool_groups == ["mail_read"]


def test_an_app_approved_without_limits_has_none(
    flow: Flow, user: User, db_session: Session
) -> None:
    flow.approved_tokens(user)
    grant = db_session.scalars(select(OAuthGrant)).one()
    assert (grant.account_ids, grant.send_ceiling) == (None, None)


@pytest.mark.parametrize(
    ("limits", "status", "code"),
    [
        ({"account_ids": []}, 400, "accounts_not_approvable"),
        ({"account_ids": [999_999]}, 400, "accounts_not_approvable"),
        ({"account_ids": ["1"]}, 422, None),
        ({"account_ids": "all"}, 422, None),
        ({"send_ceiling": "always"}, 422, None),
        ({"extra": True}, 422, None),
    ],
)
def test_limits_that_cannot_be_approved_change_nothing(
    flow: Flow,
    user: User,
    db_session: Session,
    limits: dict[str, Any],
    status: int,
    code: str | None,
) -> None:
    flow.approved_tokens(user)
    flow.sign_in(user)
    request_id = flow.authorize()
    [app] = _grants(flow).json()["grants"]
    for reply in (
        _approve_limited(flow, request_id, **limits),
        _set_limits(flow, app["id"], **limits),
    ):
        assert reply.status_code == status, reply.text
        if code is not None:
            assert reply.json()["detail"]["code"] == code
    grant = db_session.scalars(select(OAuthGrant)).one()
    assert (grant.account_ids, grant.send_ceiling) == (None, None)
    assert flow.show(request_id).status_code == 200


def test_another_users_app_or_accounts_are_not_there(
    flow: Flow, user: User, admin: User, db_session: Session, accounts: list[int]
) -> None:
    flow.approved_tokens(user)
    grant = db_session.scalars(select(OAuthGrant)).one()
    assert _set_limits(flow, grant.id, account_ids=None).status_code == 401
    flow.sign_in(admin)
    assert _set_limits(flow, grant.id, account_ids=None, send_ceiling="off").status_code == 404
    request_id = flow.authorize()
    assert flow.show(request_id).json()["accounts"] == []
    refused = _approve_limited(flow, request_id, account_ids=accounts)
    assert refused.json()["detail"]["code"] == "accounts_not_approvable"
    db_session.refresh(grant)
    assert (grant.account_ids, grant.send_ceiling) == (None, None)
