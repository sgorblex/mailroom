from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, update

from mailroom.api.deps import CurrentUser  # noqa: TC001  (FastAPI reads it at runtime)
from mailroom.auth.sessions import SESSION_COOKIE_NAME
from mailroom.config.features import Features
from mailroom.models import User, UserSession
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient


EVERY_FEATURE = ["mail", "mcp", "filters"]
SIGNED_IN = {"user": {"username": USERNAME, "is_admin": False}, "features": EVERY_FEATURE}
SIGNED_OUT = {"user": None, "features": EVERY_FEATURE}
CREDENTIALS = {"username": USERNAME, "password": PASSWORD}


def test_protected_route_needs_a_session(db_session: Session, user: User) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from mailroom.api.deps import get_db_session
    from mailroom.auth.sessions import create_session

    application = FastAPI()

    @application.get("/protected")
    def protected(current: CurrentUser) -> dict[str, str]:
        return {"username": current.username}

    application.dependency_overrides[get_db_session] = lambda: db_session
    with TestClient(application) as client:
        response = client.get("/protected")
        assert response.status_code == 401
        assert response.json() == {"detail": "Not authenticated"}
        client.cookies.set(SESSION_COOKIE_NAME, "not-a-session")
        assert client.get("/protected").status_code == 401
        client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, user))
        assert client.get("/protected").json() == {"username": USERNAME}


def test_unknown_session_is_signed_out(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/auth/me").json() == SIGNED_OUT
    api_client.cookies.set(SESSION_COOKIE_NAME, "not-a-session")
    assert api_client.get("/api/v1/auth/me").json() == SIGNED_OUT


def test_me_names_the_instances_features(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        application.state, "features", Features(mail=False, mcp=True, filters=False)
    )
    assert user_client.get("/api/v1/auth/me").json()["features"] == ["mcp"]
    monkeypatch.setattr(
        application.state, "features", Features(mail=False, mcp=False, filters=False)
    )
    assert user_client.get("/api/v1/auth/me").json()["features"] == []
    user_client.cookies.clear()
    assert user_client.get("/api/v1/auth/me").json() == {"user": None, "features": []}


@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/ready", "/api/v1/version", "/healthz"])
def test_health_readiness_and_version_stay_public(api_client: TestClient, path: str) -> None:
    assert api_client.get(path).status_code == 200


@pytest.mark.usefixtures("user")
def test_sign_in_and_out(api_client: TestClient) -> None:
    response = api_client.post("/api/v1/auth/login", json=CREDENTIALS)
    assert response.status_code == 200
    assert response.json() == SIGNED_IN["user"]
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE_NAME}=")
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=1209600"):
        assert flag in cookie

    assert api_client.get("/api/v1/auth/me").json() == SIGNED_IN

    token = api_client.cookies[SESSION_COOKIE_NAME]
    assert api_client.post("/api/v1/auth/logout").status_code == 204
    assert api_client.get("/api/v1/auth/me").json() == SIGNED_OUT

    api_client.cookies.set(SESSION_COOKIE_NAME, token)
    assert api_client.get("/api/v1/auth/me").json() == SIGNED_OUT


@pytest.mark.usefixtures("user")
def test_usernames_ignore_case(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/auth/login", json={**CREDENTIALS, "username": f" {USERNAME.upper()} "}
    )
    assert response.status_code == 200


@pytest.mark.usefixtures("user")
@pytest.mark.parametrize(
    "credentials",
    [{**CREDENTIALS, "password": PASSWORD + "!"}, {**CREDENTIALS, "username": "bob"}],
    ids=["wrong password", "unknown user"],
)
def test_wrong_credentials_are_refused_alike(
    api_client: TestClient, credentials: dict[str, str]
) -> None:
    response = api_client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong username or password"}
    assert "set-cookie" not in response.headers


def test_expired_session_is_refused(user_client: TestClient, db_session: Session) -> None:
    assert user_client.get("/api/v1/auth/me").json() == SIGNED_IN
    db_session.execute(
        update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert user_client.get("/api/v1/auth/me").json() == SIGNED_OUT
    response = user_client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    assert f"{SESSION_COOKIE_NAME}=" in response.headers["set-cookie"]


def test_deleting_a_user_ends_their_sessions(user_client: TestClient, db_session: Session) -> None:
    db_session.execute(delete(User))
    assert user_client.get("/api/v1/auth/me").json() == SIGNED_OUT
    assert db_session.query(UserSession).count() == 0


@pytest.mark.usefixtures("user")
def test_failed_attempts_make_the_client_wait(api_client: TestClient) -> None:
    wrong = {**CREDENTIALS, "password": "wrong"}
    assert api_client.post("/api/v1/auth/login", json=wrong).status_code == 401

    response = api_client.post("/api/v1/auth/login", json=CREDENTIALS)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"


@pytest.mark.usefixtures("user")
@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://elsewhere.example"},
    ],
)
def test_cross_site_writes_are_refused(api_client: TestClient, headers: dict[str, str]) -> None:
    response = api_client.post("/api/v1/auth/login", json=CREDENTIALS, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-site request refused"}


@pytest.mark.usefixtures("user")
@pytest.mark.parametrize(
    "headers", [{"Sec-Fetch-Site": "same-origin"}, {"Origin": "https://testserver"}]
)
def test_same_origin_writes_pass(api_client: TestClient, headers: dict[str, str]) -> None:
    response = api_client.post("/api/v1/auth/login", json=CREDENTIALS, headers=headers)
    assert response.status_code == 200
