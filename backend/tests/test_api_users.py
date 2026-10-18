from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mailroom.auth.password import password_matches
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session, session_user
from tests.conftest import ADMIN_USERNAME, PASSWORD, USERNAME

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User

NEW_PASSWORD = "a brand new passphrase"


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/users").status_code == 401
    change = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert api_client.post("/api/v1/auth/password", json=change).status_code == 401


@pytest.mark.usefixtures("admin")
def test_users_who_are_not_admins_are_refused(user_client: TestClient) -> None:
    response = user_client.get("/api/v1/users")
    assert response.status_code == 403
    assert response.json() == {"detail": "Admins only"}
    assert user_client.delete("/api/v1/users/1").status_code == 403
    reset = user_client.post("/api/v1/users/1/password", json={"password": NEW_PASSWORD})
    assert reset.status_code == 403
    new = {"username": "bob", "password": NEW_PASSWORD}
    assert user_client.post("/api/v1/users", json=new).status_code == 403


def test_an_admin_adds_and_removes_users(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/v1/users", json={"username": "Bob", "password": NEW_PASSWORD, "is_admin": True}
    )
    assert response.status_code == 201
    bob = response.json()
    assert (bob["username"], bob["is_admin"]) == ("bob", True)

    listed = admin_client.get("/api/v1/users").json()
    assert [(user["username"], user["is_admin"]) for user in listed] == [
        ("bob", True),
        (ADMIN_USERNAME, True),
    ]

    assert admin_client.delete(f"/api/v1/users/{bob['id']}").status_code == 204
    assert [user["username"] for user in admin_client.get("/api/v1/users").json()] == [
        ADMIN_USERNAME
    ]
    response = admin_client.delete(f"/api/v1/users/{bob['id']}")
    assert response.status_code == 404
    assert response.json() == {"detail": "No such user"}


@pytest.mark.parametrize(
    ("new", "code"),
    [
        ({"username": ADMIN_USERNAME.upper(), "password": NEW_PASSWORD}, "username_taken"),
        ({"username": "not a name", "password": NEW_PASSWORD}, "username_invalid"),
        ({"username": "bob", "password": "short"}, "password_length"),
    ],
    ids=["taken", "malformed", "short password"],
)
def test_new_users_are_checked(admin_client: TestClient, new: dict[str, str], code: str) -> None:
    response = admin_client.post("/api/v1/users", json=new)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == code


def test_an_admin_cannot_remove_themselves(admin_client: TestClient, admin: User) -> None:
    response = admin_client.delete(f"/api/v1/users/{admin.id}")
    assert response.status_code == 400
    assert response.json() == {
        "detail": {"code": "cannot_remove_self", "message": "You cannot remove yourself."}
    }


def test_an_admin_cannot_reset_their_own_password(admin_client: TestClient, admin: User) -> None:
    response = admin_client.post(
        f"/api/v1/users/{admin.id}/password", json={"password": NEW_PASSWORD}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "cannot_reset_self"
    assert admin_client.get("/api/v1/auth/me").json()["user"]["username"] == ADMIN_USERNAME


@pytest.mark.parametrize(
    ("user_id", "status"), [(999, 404), (2**63, 422), (0, 422)], ids=["unknown", "huge", "zero"]
)
@pytest.mark.usefixtures("admin")
def test_unknown_users_are_not_found(admin_client: TestClient, user_id: int, status: int) -> None:
    assert admin_client.delete(f"/api/v1/users/{user_id}").status_code == status
    reset = admin_client.post(f"/api/v1/users/{user_id}/password", json={"password": NEW_PASSWORD})
    assert reset.status_code == status


def test_cross_site_removals_are_refused(admin_client: TestClient, user: User) -> None:
    response = admin_client.delete(
        f"/api/v1/users/{user.id}", headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert response.status_code == 403


def test_removing_a_user_ends_their_sessions(
    admin_client: TestClient, db_session: Session, user: User
) -> None:
    token = create_session(db_session, user)
    assert admin_client.delete(f"/api/v1/users/{user.id}").status_code == 204
    assert session_user(db_session, token) is None


def test_a_reset_signs_them_out_and_sets_the_new_password(
    admin_client: TestClient, db_session: Session, user: User
) -> None:
    token = create_session(db_session, user)
    response = admin_client.post(
        f"/api/v1/users/{user.id}/password", json={"password": NEW_PASSWORD}
    )
    assert response.status_code == 204
    assert session_user(db_session, token) is None

    admin_client.cookies.clear()
    login = {"username": USERNAME, "password": NEW_PASSWORD}
    assert admin_client.post("/api/v1/auth/login", json=login).status_code == 200


def test_a_reset_checks_the_password(admin_client: TestClient, user: User) -> None:
    response = admin_client.post(f"/api/v1/users/{user.id}/password", json={"password": "short"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "password_length"


def test_users_change_their_own_password(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    other = create_session(db_session, user)
    change = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert user_client.post("/api/v1/auth/password", json=change).status_code == 204
    assert user_client.get("/api/v1/auth/me").json()["user"]["username"] == USERNAME
    assert session_user(db_session, other) is None
    assert session_user(db_session, user_client.cookies[SESSION_COOKIE_NAME]) == user


def test_a_wrong_current_password_is_refused_and_throttled(user_client: TestClient) -> None:
    wrong = {"current_password": PASSWORD + "!", "new_password": NEW_PASSWORD}
    response = user_client.post("/api/v1/auth/password", json=wrong)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "wrong_password"

    right = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    response = user_client.post("/api/v1/auth/password", json=right)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"


def test_a_short_new_password_changes_nothing(user_client: TestClient, user: User) -> None:
    change = {"current_password": PASSWORD, "new_password": "short"}
    response = user_client.post("/api/v1/auth/password", json=change)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "password_length"
    assert password_matches(user.password_hash, PASSWORD)
    # The current password was right, so the throttle holds nothing against this client.
    change = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert user_client.post("/api/v1/auth/password", json=change).status_code == 204
