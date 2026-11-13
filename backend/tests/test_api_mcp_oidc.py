"""``MAILROOM_MCP_AUTH=oidc`` end to end: the built-in authorization server sends the browser
through web SSO (the fake provider) before the consent screen, and only a request whose user
signed in that way can be shown or decided."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from sqlalchemy import select, update
from starlette.testclient import TestClient

from mailroom.api.main import create_application
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.sso import BINDING_COOKIE_NAME, sso_start_limit
from mailroom.config.features import ALL_FEATURES
from mailroom.dev.fake_idp import FakeIdp
from mailroom.mcp.groups import selected_groups
from mailroom.mcp.settings import McpSettings
from mailroom.models import SsoLoginState
from mailroom.oauth.models import OAuthAuthorizationRequest, OAuthGrant
from mailroom.oauth.throttle import Throttle
from mailroom.oauth.tokens import secret_hash
from tests.test_api_oauth_consent import CALLBACK, LISTING, MCP_HEADERS, RESOURCE, _challenge
from tests.test_api_sso import CLIENT, ISSUER, START, at_provider, configure, link, sso_error

if TYPE_CHECKING:
    from collections.abc import Iterator

    import httpx
    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.models import User

PUBLIC_URL = "https://mailroom.example"


@pytest.fixture(scope="module")
def _oidc_application(_worker_engine: Engine) -> Iterator[tuple[TestClient, FastAPI]]:
    settings = McpSettings(public_url=PUBLIC_URL, auth="oidc", groups=selected_groups(None))
    application = create_application(features=ALL_FEATURES, mcp_settings=settings)
    assert application.state.oauth_server is not None
    with TestClient(application, base_url=PUBLIC_URL, follow_redirects=False) as client:
        yield client, application


@pytest.fixture(scope="module")
def _keys() -> FakeIdp:
    return FakeIdp(issuer=ISSUER, client_id=CLIENT)


@pytest.fixture
def idp(_keys: FakeIdp) -> FakeIdp:
    return FakeIdp(issuer=ISSUER, client_id=CLIENT, keys=list(_keys.keys), subject="sub-alice")


class Browser:
    """An MCP client's side of the flow, and the browser's through SSO and the consent screen."""

    def __init__(self, client: TestClient, db: Session, idp: FakeIdp) -> None:
        self.client = client
        self.db = db
        self.idp = idp
        self.verifier = secrets.token_urlsafe(48)
        reply = client.post(
            "/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": "Claude"}
        )
        assert reply.status_code == 201, reply.text
        self.client_id = str(reply.json()["client_id"])

    def authorize(self, **overrides: str) -> httpx.Response:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": CALLBACK,
            "code_challenge": _challenge(self.verifier),
            "code_challenge_method": "S256",
            "state": "the-state",
            "resource": RESOURCE,
            **overrides,
        }
        return self.client.get("/oauth/authorize", params=params)

    def request_id(self) -> str:
        """A new authorization request's id, from the SSO start ``/oauth/authorize`` sends the
        browser to."""
        reply = self.authorize()
        assert reply.status_code == 302, reply.text
        location = reply.headers["location"]
        assert location.startswith(f"{PUBLIC_URL}{START}?")
        query = dict(parse_qsl(urlsplit(location).query))
        assert query.keys() == {"intent", "request"}
        assert query["intent"] == "mcp"
        return query["request"]

    def start(self, request_id: str, **params: str) -> httpx.Response:
        return self.client.get(START, params={"intent": "mcp", "request": request_id, **params})

    def through_sso(self, request_id: str) -> httpx.Response:
        """Start, the provider, and its callback: the callback's answer."""
        started = self.start(request_id)
        assert started.status_code == 303, started.text
        return self.client.get(at_provider(self.idp, started))

    def show(self, request_id: str) -> httpx.Response:
        return self.client.get(f"/api/v1/oauth/requests/{request_id}")

    def decide(self, request_id: str, decision: str) -> httpx.Response:
        return self.client.post(f"/api/v1/oauth/requests/{request_id}", json={"decision": decision})

    def sign_in_with_password(self, user: User) -> None:
        self.client.cookies.set(SESSION_COOKIE_NAME, create_session(self.db, user))

    def stored(self, request_id: str) -> dict[str, Any]:
        row = self.db.scalar(
            select(OAuthAuthorizationRequest).where(
                OAuthAuthorizationRequest.id_hash == secret_hash(request_id)
            )
        )
        assert row is not None
        self.db.refresh(row)
        return dict(row.params)


@pytest.fixture
def browser(
    _oidc_application: tuple[TestClient, FastAPI],
    sessions: sessionmaker[Session],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    idp: FakeIdp,
) -> Iterator[Browser]:
    client, application = _oidc_application
    monkeypatch.setattr(application.state, "session_maker", sessions)
    monkeypatch.setattr(application.state, "sso_start_limit", sso_start_limit())
    monkeypatch.setattr(Throttle, "wait", lambda self, key: 0)
    configure(application, monkeypatch, idp)
    client.cookies.clear()
    try:
        yield Browser(client, db_session, idp)
    finally:
        client.cookies.clear()


def _query(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query))


def test_signing_in_through_sso_then_approving_connects_the_client(
    browser: Browser, db_session: Session, user: User
) -> None:
    link(db_session, user)
    request_id = browser.request_id()
    back = browser.through_sso(request_id)
    assert back.status_code == 303
    assert back.headers["location"] == f"/connect?request={request_id}"
    assert any(c.startswith(f"{SESSION_COOKIE_NAME}=") for c in back.headers.get_list("set-cookie"))
    assert browser.stored(request_id)["claimed_by"] == user.id

    shown = browser.show(request_id)
    assert shown.status_code == 200, shown.text
    assert shown.json()["client_name"] == "Claude"
    decided = browser.decide(request_id, "approve")
    assert decided.status_code == 200, decided.text
    query = _query(decided.json()["redirect_to"])
    assert (query["state"], query["iss"]) == ("the-state", f"{PUBLIC_URL}/oauth")

    browser.client.cookies.clear()
    reply = browser.client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"],
            "redirect_uri": CALLBACK,
            "client_id": browser.client_id,
            "code_verifier": browser.verifier,
        },
    )
    assert reply.status_code == 200, reply.text
    token = reply.json()["access_token"]
    answer = browser.client.post(
        "/mcp", json=LISTING, headers={**MCP_HEADERS, "authorization": f"Bearer {token}"}
    )
    assert answer.status_code == 200, answer.text
    grants = db_session.scalars(select(OAuthGrant)).all()
    assert [grant.user_id for grant in grants] == [user.id]


def test_the_sso_flow_comes_back_to_the_request_whatever_return_to_says(
    browser: Browser, db_session: Session
) -> None:
    request_id = browser.request_id()
    started = browser.start(request_id, return_to="/elsewhere")
    assert started.status_code == 303
    assert started.headers["location"].startswith(f"{ISSUER}/authorize?")
    flows = db_session.scalars(select(SsoLoginState)).all()
    assert [(flow.intent, flow.return_to) for flow in flows] == [
        ("mcp", f"/connect?request={request_id}")
    ]


@pytest.mark.usefixtures("user")
def test_an_identity_linked_to_nobody_is_refused(browser: Browser) -> None:
    request_id = browser.request_id()
    back = browser.through_sso(request_id)
    assert sso_error(back) == "not_linked"
    assert urlsplit(back.headers["location"]).path == "/connect"
    assert _query(back.headers["location"])["request"] == request_id
    assert not any(
        c.startswith(f"{SESSION_COOKIE_NAME}=") for c in back.headers.get_list("set-cookie")
    )
    assert "claimed_by" not in browser.stored(request_id)


def test_a_password_session_cannot_show_or_decide_a_request(
    browser: Browser, db_session: Session, user: User
) -> None:
    request_id = browser.request_id()
    browser.sign_in_with_password(user)
    for answer in (browser.show(request_id), browser.decide(request_id, "approve")):
        assert answer.status_code == 409, answer.text
        assert answer.json()["detail"]["code"] == "sso_required"
    assert "claimed_by" not in browser.stored(request_id)
    # Signing in through SSO for it still works afterwards.
    link(db_session, user)
    assert browser.through_sso(request_id).headers["location"] == f"/connect?request={request_id}"
    assert browser.show(request_id).status_code == 200


def test_a_request_claimed_through_sso_is_nobody_elses(
    browser: Browser, db_session: Session, user: User, admin: User
) -> None:
    link(db_session, user)
    request_id = browser.request_id()
    browser.through_sso(request_id)
    browser.client.cookies.clear()
    browser.sign_in_with_password(admin)
    assert browser.show(request_id).status_code == 404
    assert browser.decide(request_id, "approve").status_code == 404


def test_only_the_browser_that_signed_in_may_answer(
    browser: Browser, db_session: Session, user: User
) -> None:
    """Another session of the same user, a password one included, cannot answer the request
    an SSO sign-in claimed: it would need no sign-in through the provider at all."""
    link(db_session, user)
    request_id = browser.request_id()
    browser.through_sso(request_id)
    signed_in = browser.client.cookies.get(SESSION_COOKIE_NAME)
    browser.client.cookies.clear()
    browser.sign_in_with_password(user)
    for answer in (browser.show(request_id), browser.decide(request_id, "approve")):
        assert answer.status_code == 409, answer.text
        assert answer.json()["detail"]["code"] == "sso_required"
    browser.client.cookies.set(SESSION_COOKIE_NAME, signed_in)
    assert browser.show(request_id).status_code == 200


def test_of_two_sign_ins_for_one_request_the_first_user_keeps_it(
    browser: Browser, db_session: Session, user: User, admin: User
) -> None:
    link(db_session, user)
    link(db_session, admin, subject="sub-admin")
    request_id = browser.request_id()
    other = TestClient(browser.client.app, base_url=PUBLIC_URL, follow_redirects=False)
    theirs = other.get(START, params={"intent": "mcp", "request": request_id})
    ours = browser.start(request_id)
    browser.client.get(at_provider(browser.idp, ours))
    browser.idp.subject = "sub-admin"
    back = other.get(at_provider(browser.idp, theirs))
    assert sso_error(back) == "request_gone"
    assert not any(
        c.startswith(f"{SESSION_COOKIE_NAME}=") for c in back.headers.get_list("set-cookie")
    )
    assert browser.stored(request_id)["claimed_by"] == user.id


def test_a_request_gone_during_the_sign_in_is_said_so(
    browser: Browser, db_session: Session, user: User
) -> None:
    link(db_session, user)
    request_id = browser.request_id()
    started = browser.start(request_id)
    db_session.execute(
        update(OAuthAuthorizationRequest).values(expires_at=datetime.now(UTC) - timedelta(1))
    )
    db_session.commit()
    back = browser.client.get(at_provider(browser.idp, started))
    assert sso_error(back) == "request_gone"
    assert not any(
        c.startswith(f"{SESSION_COOKIE_NAME}=") for c in back.headers.get_list("set-cookie")
    )
    assert sso_error(browser.start(request_id)) == "request_gone"


def test_denying_after_signing_in_sends_access_denied_back(
    browser: Browser, db_session: Session, user: User
) -> None:
    link(db_session, user)
    request_id = browser.request_id()
    browser.through_sso(request_id)
    decided = browser.decide(request_id, "deny")
    assert decided.status_code == 200, decided.text
    query = _query(decided.json()["redirect_to"])
    assert (query["error"], query["iss"]) == ("access_denied", f"{PUBLIC_URL}/oauth")
    assert db_session.scalars(select(OAuthGrant)).all() == []


def test_the_callback_checks_the_browser_and_the_state(
    browser: Browser, db_session: Session, user: User
) -> None:
    link(db_session, user)
    request_id = browser.request_id()
    started = browser.start(request_id)
    callback = at_provider(browser.idp, started)
    browser.client.cookies.delete(BINDING_COOKIE_NAME, path="/api/v1/auth/oidc")
    assert sso_error(browser.client.get(callback)) == "browser_mismatch"
    assert "claimed_by" not in browser.stored(request_id)
    # The state was used up by that attempt: replaying it fails too.
    assert sso_error(browser.client.get(callback)) == "state_invalid"


@pytest.mark.parametrize(("request_id", "code"), [(None, "state_invalid"), ("x", "request_gone")])
def test_starting_needs_a_live_request(browser: Browser, request_id: str | None, code: str) -> None:
    params = {"intent": "mcp"} if request_id is None else {"intent": "mcp", "request": request_id}
    assert sso_error(browser.client.get(START, params=params)) == code


def test_a_redirect_uri_off_the_allowlist_never_reaches_sso(
    browser: Browser, db_session: Session
) -> None:
    reply = browser.authorize(redirect_uri="https://attacker.example/callback")
    assert reply.status_code == 400
    assert "location" not in reply.headers
    reply = browser.authorize(resource="https://elsewhere.example/mcp")
    assert reply.status_code == 302
    location = reply.headers["location"]
    assert location.startswith(f"{CALLBACK}?")
    assert _query(location)["iss"] == f"{PUBLIC_URL}/oauth"  # a redirect to the client has it
    assert db_session.scalars(select(OAuthAuthorizationRequest)).all() == []
