from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, update

from mailroom.api.deps import CurrentUser  # noqa: TC001  (FastAPI reads it at runtime)
from mailroom.auth.sessions import SESSION_COOKIE_NAME
from mailroom.auth.users import time_zone_of
from mailroom.config.features import Features
from mailroom.models import User, UserSession
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient


EVERY_FEATURE = ["mail", "mcp", "filters"]
SIGNED_IN = {
    "user": {
        "username": USERNAME,
        "is_admin": False,
        "time_zone": None,
        "mark_read_subfolders": True,
        "index_max_age_days": None,
    },
    "features": EVERY_FEATURE,
}
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


def test_the_user_sets_their_time_zone(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    response = user_client.put("/api/v1/auth/me/time-zone", json={"time_zone": "europe/rome"})
    assert response.status_code == 200
    assert response.json()["time_zone"] == "Europe/Rome"
    assert user_client.get("/api/v1/auth/me").json()["user"]["time_zone"] == "Europe/Rome"
    db_session.expire_all()
    assert time_zone_of(db_session, user.id) == "Europe/Rome"

    response = user_client.put("/api/v1/auth/me/time-zone", json={"time_zone": None})
    assert response.json()["time_zone"] is None
    db_session.expire_all()
    assert time_zone_of(db_session, user.id) == "UTC"


@pytest.mark.parametrize("zone", ["Mars/Olympus_Mons", "", 3, "Europe/Rome\n"])
def test_an_unknown_time_zone_is_refused(user_client: TestClient, zone: object) -> None:
    response = user_client.put("/api/v1/auth/me/time-zone", json={"time_zone": zone})
    assert response.status_code == 422
    assert user_client.get("/api/v1/auth/me").json()["user"]["time_zone"] is None


def test_setting_a_time_zone_needs_a_session(api_client: TestClient) -> None:
    response = api_client.put("/api/v1/auth/me/time-zone", json={"time_zone": "UTC"})
    assert response.status_code == 401


def test_the_user_picks_what_mark_all_as_read_does_first(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    route = "/api/v1/auth/me/mark-read-subfolders"
    response = user_client.put(route, json={"mark_read_subfolders": False})
    assert response.status_code == 200
    assert response.json()["mark_read_subfolders"] is False
    assert user_client.get("/api/v1/auth/me").json()["user"]["mark_read_subfolders"] is False
    db_session.expire_all()
    stored = db_session.get(User, user.id)
    assert stored is not None
    assert stored.mark_read_subfolders is False

    for body in ({"mark_read_subfolders": "false"}, {"mark_read_subfolders": None}, {}):
        assert user_client.put(route, json=body).status_code == 422
    assert user_client.get("/api/v1/auth/me").json()["user"]["mark_read_subfolders"] is False

    cross_site = user_client.put(
        route, json={"mark_read_subfolders": True}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert cross_site.status_code == 403
    assert user_client.put(route, json={"mark_read_subfolders": True}).status_code == 200
    assert user_client.get("/api/v1/auth/me").json()["user"]["mark_read_subfolders"] is True


def test_picking_what_mark_all_as_read_does_needs_a_session(api_client: TestClient) -> None:
    response = api_client.put(
        "/api/v1/auth/me/mark-read-subfolders", json={"mark_read_subfolders": False}
    )
    assert response.status_code == 401


def test_the_user_limits_the_index_to_recent_mail(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    route = "/api/v1/auth/me/index-max-age"
    response = user_client.put(route, json={"index_max_age_days": 365})
    assert response.status_code == 200
    assert response.json()["index_max_age_days"] == 365
    assert user_client.get("/api/v1/auth/me").json()["user"]["index_max_age_days"] == 365
    db_session.expire_all()
    stored = db_session.get(User, user.id)
    assert stored is not None
    assert stored.index_max_age_days == 365

    for body in (
        {"index_max_age_days": 0},
        {"index_max_age_days": 36501},
        {"index_max_age_days": "30"},
        {"index_max_age_days": 30.5},
        {"index_max_age_days": 30, "other": 1},
        {},
    ):
        assert user_client.put(route, json=body).status_code == 422, body
    assert user_client.get("/api/v1/auth/me").json()["user"]["index_max_age_days"] == 365

    cross_site = user_client.put(
        route, json={"index_max_age_days": None}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert cross_site.status_code == 403
    assert user_client.put(route, json={"index_max_age_days": None}).status_code == 200
    assert user_client.get("/api/v1/auth/me").json()["user"]["index_max_age_days"] is None


def test_limiting_the_index_needs_a_session(api_client: TestClient) -> None:
    response = api_client.put("/api/v1/auth/me/index-max-age", json={"index_max_age_days": 30})
    assert response.status_code == 401


def test_a_cross_site_time_zone_change_is_refused(user_client: TestClient) -> None:
    response = user_client.put(
        "/api/v1/auth/me/time-zone",
        json={"time_zone": "UTC"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403
