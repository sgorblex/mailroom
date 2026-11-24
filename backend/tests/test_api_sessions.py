from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select, update
from starlette.testclient import TestClient

from mailroom.auth.sessions import SEEN_EVERY, SESSION_COOKIE_NAME, create_session, session_user
from mailroom.auth.sign_in_client import SignInClient, device_label, recorded_ip
from mailroom.auth.users import create_user
from mailroom.config.instance_settings import InstanceSettingsChange, change_instance_settings
from mailroom.models import UserSession
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.orm import Session

    from mailroom.config.instance_settings import SessionIp
    from mailroom.models import User

PATH = "/api/v1/auth/sessions"
CREDENTIALS = {"username": USERNAME, "password": PASSWORD}
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
PHONE = SignInClient(device="Safari · iOS", ip="203.0.113.7")


def listed(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(PATH)
    assert response.status_code == 200
    sessions: list[dict[str, Any]] = response.json()
    return sessions


def rows(db: Session, user: User) -> list[UserSession]:
    query = select(UserSession).where(UserSession.user_id == user.id).order_by(UserSession.id)
    return list(db.scalars(query.execution_options(populate_existing=True)))


def set_session_ip(db: Session, admin: User, choice: SessionIp) -> None:
    change_instance_settings(db, admin, InstanceSettingsChange(session_ip=choice))


@pytest.mark.parametrize(
    ("method", "path"), [("GET", PATH), ("POST", f"{PATH}/end-others"), ("DELETE", f"{PATH}/1")]
)
def test_signed_out_browsers_are_refused(api_client: TestClient, method: str, path: str) -> None:
    assert api_client.request(method, path).status_code == 401


@pytest.mark.parametrize("raw", ["Bearer {}", "bearer {}"])
def test_a_sessions_token_signs_nothing_in_but_as_the_cookie(
    api_client: TestClient, db_session: Session, user: User, raw: str
) -> None:
    headers = {"Authorization": raw.format(create_session(db_session, user))}
    assert api_client.get(PATH, headers=headers).status_code == 401
    assert api_client.post(f"{PATH}/end-others", headers=headers).status_code == 401


def test_the_list_is_the_users_live_sessions_this_one_first(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    older = create_session(db_session, user, PHONE)
    newer = create_session(db_session, user)
    expired = create_session(db_session, user)
    create_session(db_session, create_user(db_session, "bob", PASSWORD))
    for token, seen in ((older, timedelta(hours=2)), (newer, timedelta(hours=1))):
        assert session_user(db_session, token) == user
        db_session.execute(
            update(UserSession)
            .where(UserSession.id == _id_of(db_session, token))
            .values(last_seen_at=datetime.now(UTC) - seen)
        )
    db_session.execute(
        update(UserSession)
        .where(UserSession.id == _id_of(db_session, expired))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    response = user_client.get(PATH)
    assert response.headers["cache-control"] == "private, no-store"
    sessions = response.json()
    assert [session["current"] for session in sessions] == [True, False, False]
    assert [session["id"] for session in sessions[1:]] == [
        _id_of(db_session, newer),
        _id_of(db_session, older),
    ]
    assert (sessions[2]["device"], sessions[2]["ip"]) == ("Safari · iOS", "203.0.113.x")
    assert set(sessions[0]) == {
        "id",
        "current",
        "device",
        "ip",
        "created_at",
        "last_seen_at",
        "expires_at",
    }


def _id_of(db: Session, raw_token: str) -> int:
    import hashlib

    digest = hashlib.sha256(raw_token.encode()).hexdigest()
    found = db.scalar(select(UserSession.id).where(UserSession.token_hash == digest))
    assert found is not None
    return found


@pytest.mark.parametrize(
    ("choice", "recorded"), [("none", None), ("partial", "203.0.113.x"), ("full", "203.0.113.7")]
)
def test_signing_in_records_the_browser_and_the_ip_the_setting_lets_it(
    application: FastAPI,
    api_client: TestClient,
    db_session: Session,
    user: User,
    admin: User,
    choice: SessionIp,
    recorded: str | None,
) -> None:
    set_session_ip(db_session, admin, choice)
    browser = TestClient(application, base_url="https://testserver", client=("203.0.113.7", 50000))
    response = browser.post("/api/v1/auth/login", json=CREDENTIALS, headers={"User-Agent": FIREFOX})
    assert response.status_code == 200
    [row] = rows(db_session, user)
    assert (row.device, row.ip) == ("Firefox · Linux", recorded)
    [session] = listed(browser)
    assert (session["device"], session["ip"]) == ("Firefox · Linux", recorded)


@pytest.mark.usefixtures("user")
def test_a_client_with_no_address_and_an_unknown_browser_signs_in(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/auth/login", json=CREDENTIALS, headers={"User-Agent": "x" * 5000}
    )
    assert response.status_code == 200
    [session] = listed(api_client)
    assert (session["device"], session["ip"]) == (None, None)


def test_a_stricter_setting_hides_what_was_recorded(
    user_client: TestClient, db_session: Session, user: User, admin: User
) -> None:
    create_session(db_session, user, PHONE)
    for choice, shown in (("full", "203.0.113.7"), ("partial", "203.0.113.x"), ("none", None)):
        set_session_ip(db_session, admin, choice)  # type: ignore[arg-type]
        assert listed(user_client)[1]["ip"] == shown


def test_a_session_is_seen_at_most_every_five_minutes(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    [row] = rows(db_session, user)
    for age, written in ((SEEN_EVERY / 2, False), (SEEN_EVERY * 2, True)):
        stored = datetime.now(UTC) - age
        db_session.execute(update(UserSession).values(last_seen_at=stored))
        assert user_client.get("/api/v1/auth/me").json()["user"]["username"] == USERNAME
        db_session.refresh(row)
        assert (row.last_seen_at != stored) is written
        assert (datetime.now(UTC) - row.last_seen_at < timedelta(minutes=1)) is written


def test_an_expired_session_is_not_seen(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    long_ago = datetime.now(UTC) - timedelta(days=1)
    db_session.execute(update(UserSession).values(last_seen_at=long_ago, expires_at=long_ago))
    assert user_client.get(PATH).status_code == 401
    assert rows(db_session, user)[0].last_seen_at == long_ago


def test_ending_one_session(
    user_client: TestClient, db_session: Session, user: User, caplog: pytest.LogCaptureFixture
) -> None:
    phone, other = create_session(db_session, user, PHONE), create_session(db_session, user)
    path = f"{PATH}/{_id_of(db_session, phone)}"
    caplog.set_level(logging.INFO)
    assert user_client.delete(path).status_code == 204
    assert session_user(db_session, phone) is None
    assert session_user(db_session, other) == user
    assert "session_ended" in caplog.messages
    assert "203.0.113" not in caplog.text
    response = user_client.delete(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "No such session"}


def test_this_session_ends_only_by_signing_out(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    [row] = rows(db_session, user)
    response = user_client.delete(f"{PATH}/{row.id}")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "current_session"
    assert len(listed(user_client)) == 1


def test_another_users_session_is_not_found(user_client: TestClient, db_session: Session) -> None:
    bob = create_user(db_session, "bob", PASSWORD)
    token = create_session(db_session, bob)
    response = user_client.delete(f"{PATH}/{_id_of(db_session, token)}")
    assert response.status_code == 404
    assert session_user(db_session, token) == bob


@pytest.mark.parametrize("session_id", ["0", "-1", str(2**63), "end-others"])
def test_ids_that_cannot_be_are_refused(user_client: TestClient, session_id: str) -> None:
    assert user_client.delete(f"{PATH}/{session_id}").status_code in {405, 422}


def test_ending_every_other_session(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    others = [create_session(db_session, user) for _ in range(2)]
    bob = create_user(db_session, "bob", PASSWORD)
    bobs = create_session(db_session, bob)
    for _ in range(2):  # the second finds none left
        assert user_client.post(f"{PATH}/end-others").status_code == 204
    assert [session_user(db_session, token) for token in others] == [None, None]
    assert session_user(db_session, bobs) == bob
    assert [session["current"] for session in listed(user_client)] == [True]


def test_cross_site_ends_are_refused(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    other = create_session(db_session, user)
    headers = {"Sec-Fetch-Site": "cross-site"}
    one = user_client.delete(f"{PATH}/{_id_of(db_session, other)}", headers=headers)
    every = user_client.post(f"{PATH}/end-others", headers=headers)
    assert (one.status_code, every.status_code) == (403, 403)
    assert session_user(db_session, other) == user


def test_an_ended_browser_is_signed_out(
    api_client: TestClient, db_session: Session, user: User
) -> None:
    mine, theirs = create_session(db_session, user), create_session(db_session, user)
    api_client.cookies.set(SESSION_COOKIE_NAME, mine)
    assert api_client.post(f"{PATH}/end-others").status_code == 204
    api_client.cookies.set(SESSION_COOKIE_NAME, theirs)
    assert api_client.get("/api/v1/auth/me").json()["user"] is None
    assert api_client.get(PATH).status_code == 401


_CHROME = "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0"
_WEBKIT = "AppleWebKit/605.1.15 (KHTML, like Gecko)"
_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"
_IPAD = "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)"
_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
_ANDROID = "Mozilla/5.0 (Linux; Android 14)"
_WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


@pytest.mark.parametrize(
    ("user_agent", "label"),
    [
        (FIREFOX, "Firefox · Linux"),
        (f"{_WINDOWS} {_CHROME} Safari/537.36", "Chrome · Windows"),
        (f"{_WINDOWS} {_CHROME} Safari/537.36 Edg/140.0.0.0", "Edge · Windows"),
        (f"{_WINDOWS} {_CHROME} Safari/537.36 OPR/120.0.0.0", "Opera · Windows"),
        (
            f"Mozilla/5.0 (Linux; Android 10; K) {_CHROME} Mobile Safari/537.36",
            "Chrome · Android",
        ),
        (
            f"{_ANDROID} SamsungBrowser/25.0 Chrome/121.0.0.0 Safari/537.36",
            "Samsung Internet · Android",
        ),
        (f"Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) {_CHROME} Safari/537.36", "Chrome · ChromeOS"),
        (f"{_MAC} {_WEBKIT} Version/18.0 Safari/605.1.15", "Safari · macOS"),
        (f"{_IPHONE} {_WEBKIT} Version/18.0 Mobile/15E148 Safari/604.1", "Safari · iOS"),
        (f"{_IPHONE} {_WEBKIT} CriOS/140.0 Mobile/15E148 Safari/604.1", "Chrome · iOS"),
        (f"{_IPHONE} {_WEBKIT} FxiOS/140.0 Mobile/15E148 Safari/605.1.15", "Firefox · iOS"),
        (f"{_IPAD} {_WEBKIT} Version/18.0 Mobile/15E148 Safari/604.1", "Safari · iPadOS"),
        ("curl/8.9.0", "curl"),
        ("Mozilla/5.0 (X11; Linux x86_64)", "Linux"),
        ("<b>hello</b>", None),
        ("", None),
        ("x" * 600 + " Firefox/140.0", None),
    ],
)
def test_the_device_is_named_from_known_browsers_and_systems(
    user_agent: str, label: str | None
) -> None:
    assert device_label(user_agent) == label


@pytest.mark.parametrize(
    ("address", "partial", "full"),
    [
        ("203.0.113.7", "203.0.113.x", "203.0.113.7"),
        ("2001:db8:1:2ff::1", "2001:db8:1:x", "2001:db8:1:2ff::1"),
        ("2001:db8::1", "2001:db8:0:x", "2001:db8::1"),
        ("::ffff:203.0.113.7", "203.0.113.x", "203.0.113.7"),
        ("fe80::1%eth0", "fe80:0:0:x", "fe80::1"),
        ("203.0.113.x", "203.0.113.x", "203.0.113.x"),
        ("2001:db8:1:x", "2001:db8:1:x", "2001:db8:1:x"),
        ("testclient", None, None),
        ("", None, None),
    ],
)
def test_an_ip_is_recorded_as_far_as_the_setting_goes(
    address: str, partial: str | None, full: str | None
) -> None:
    assert recorded_ip(address, "partial") == partial
    assert recorded_ip(address, "full") == full
    assert recorded_ip(address, "none") is None
