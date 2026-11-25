from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from mailroom.auth.api_tokens import create_token, token_access
from mailroom.auth.password import password_matches
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session, session_user
from mailroom.mail import remote_senders, sender_views
from mailroom.oauth.models import OAuthAuthorizationCode, OAuthClient
from mailroom.oauth.tokens import (
    create_grant_with_code,
    issue_for_code,
    secret_hash,
    take_code,
    verify_access_token,
    verify_refresh_token,
)
from mailroom.reach import WHOLE
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
    assert user_client.post("/api/v1/users/1/sign-out", json={}).status_code == 403
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


def test_removing_a_user_forgets_the_senders_they_remembered(
    admin_client: TestClient, db_session: Session, user: User
) -> None:
    remote_senders.remember(db_session, user.id, "news@shop.example")
    sender_views.remember(db_session, user.id, "news@shop.example")
    assert admin_client.delete(f"/api/v1/users/{user.id}").status_code == 204
    assert remote_senders.senders(db_session, user.id) == []
    assert sender_views.senders(db_session, user.id) == []


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


def _access(db: Session, user: User) -> tuple[str, dict[str, str], str]:
    """An API token of the user's, and a connected app's tokens and unspent code."""
    client_id = f"app-{user.username}"
    db.add(OAuthClient(client_id=client_id, kind="dcr", redirect_uris=[], metadata_={}))
    db.flush()

    def code() -> str:
        return create_grant_with_code(
            db,
            user_id=user.id,
            client_id=client_id,
            redirect_uri="https://app.example/cb",
            redirect_uri_explicit=True,
            code_challenge="x" * 43,
            resource="https://mailroom.example/mcp",
            scopes=["mailroom"],
            tool_groups=["mail_read"],
            reach=WHOLE,
        )

    spent = take_code(db, client_id, code())
    assert spent is not None
    issued = issue_for_code(db, spent.grant_id)
    assert issued is not None
    unspent = code()
    db.commit()
    _, raw = create_token(
        db, user.id, name="x", tool_groups=["mail_read"], reach=WHOLE, expires_in=None
    )
    return raw, {"access": issued.access_token, "refresh": issued.refresh_token}, unspent


def _works(db: Session, user: User, access: tuple[str, dict[str, str], str]) -> set[bool]:
    raw, issued, unspent = access
    client_id = f"app-{user.username}"
    return {
        token_access(db, raw) is not None,
        verify_access_token(db, issued["access"]) is not None,
        verify_refresh_token(db, client_id, issued["refresh"]) is not None,
        db.get(OAuthAuthorizationCode, secret_hash(unspent), populate_existing=True) is not None,
    }


@pytest.mark.parametrize("revoke", [True, False, None])
def test_a_reset_revokes_tokens_and_apps_only_when_asked(
    admin_client: TestClient,
    db_session: Session,
    user: User,
    admin: User,
    revoke: bool | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    theirs, admins = _access(db_session, user), _access(db_session, admin)
    body = {"password": NEW_PASSWORD} | ({} if revoke is None else {"revoke_access": revoke})
    with caplog.at_level(logging.INFO):
        reset = admin_client.post(f"/api/v1/users/{user.id}/password", json=body)
    assert reset.status_code == 204
    assert _works(db_session, user, theirs) == {not revoke}
    assert _works(db_session, admin, admins) == {True}
    assert caplog.messages.count("api_token_revoked") == (1 if revoke else 0)
    assert caplog.messages.count("oauth_grant_revoked") == (2 if revoke else 0)


@pytest.mark.parametrize("revoke", [True, False, None])
def test_ones_own_change_revokes_tokens_and_apps_only_when_asked(
    user_client: TestClient, db_session: Session, user: User, revoke: bool | None
) -> None:
    access = _access(db_session, user)
    change = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    body = change | ({} if revoke is None else {"revoke_access": revoke})
    assert user_client.post("/api/v1/auth/password", json=body).status_code == 204
    assert _works(db_session, user, access) == {not revoke}


def test_a_refused_password_revokes_nothing(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    access = _access(db_session, user)
    body = {"current_password": PASSWORD, "new_password": "short", "revoke_access": True}
    assert user_client.post("/api/v1/auth/password", json=body).status_code == 400
    assert _works(db_session, user, access) == {True}


@pytest.mark.parametrize("revoke", [True, False, None])
def test_an_admin_signs_a_user_out(
    admin_client: TestClient,
    db_session: Session,
    user: User,
    admin: User,
    revoke: bool | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sessions = [create_session(db_session, user) for _ in range(2)]
    theirs, admins = _access(db_session, user), _access(db_session, admin)
    body = {} if revoke is None else {"revoke_access": revoke}
    with caplog.at_level(logging.INFO):
        response = admin_client.post(f"/api/v1/users/{user.id}/sign-out", json=body)
    assert response.status_code == 204
    assert [session_user(db_session, raw) for raw in sessions] == [None, None]
    assert password_matches(user.password_hash, PASSWORD)
    assert admin_client.get("/api/v1/auth/me").json()["user"]["username"] == ADMIN_USERNAME
    assert _works(db_session, user, theirs) == {not revoke}
    assert _works(db_session, admin, admins) == {True}
    assert caplog.messages.count("user_signed_out") == 1
    assert caplog.messages.count("api_token_revoked") == (1 if revoke else 0)


def test_an_admin_cannot_sign_themselves_out_there(
    admin_client: TestClient, db_session: Session, admin: User
) -> None:
    access = _access(db_session, admin)
    path = f"/api/v1/users/{admin.id}/sign-out"
    response = admin_client.post(path, json={"revoke_access": True})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "cannot_sign_out_self"
    assert _works(db_session, admin, access) == {True}
    assert admin_client.get("/api/v1/auth/me").status_code == 200
    missing = admin_client.post("/api/v1/users/999999999/sign-out", json={})
    assert missing.status_code == 404
