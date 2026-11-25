"""External sign-in only: passwords refused while an admin's instance setting says so."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from mailroom.auth.oidc import OidcProvider
from mailroom.auth.password import password_matches
from mailroom.auth.password_sign_in import PASSWORD_SIGN_IN_ENV, warn_about_environment
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session, session_user
from mailroom.auth.sso import CALLBACK_PATH, Sso, SsoSettings
from mailroom.auth.users import create_user, user_by_username
from mailroom.config.instance_settings import stored_settings
from mailroom.models import ExternalIdentity, InstanceSetting
from tests.conftest import ADMIN_USERNAME, PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User

ISSUER = "https://idp.example.test"
SETTINGS = "/api/v1/admin/settings"
CONFIG = "/api/v1/auth/oidc/config"
LOGIN = "/api/v1/auth/login"
NEW_PASSWORD = "a brand new passphrase"
CREDENTIALS = {"username": USERNAME, "password": PASSWORD}


@pytest.fixture(autouse=True)
def _no_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PASSWORD_SIGN_IN_ENV, raising=False)


@pytest.fixture
def sso(application: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured provider, which is never reached."""
    config = SsoSettings(
        issuer=ISSUER,
        client_id="mailroom-test",
        client_secret="not a secret",
        name="Example ID",
        link_claim=None,
        redirect_uri=f"https://testserver{CALLBACK_PATH}",
    )
    provider = OidcProvider(ISSUER, transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    monkeypatch.setattr(application.state, "sso", Sso(config, provider), raising=False)


@pytest.fixture
def external_only(sso: None, db_session: Session) -> None:
    db_session.add(InstanceSetting(key="external_sign_in_only", value=True))
    db_session.commit()


def link(db: Session, user: User, subject: str = "sub-1", issuer: str = ISSUER) -> int:
    identity = ExternalIdentity(user_id=user.id, issuer=issuer, subject=subject)
    db.add(identity)
    db.commit()
    return identity.id


def body(*, external: bool) -> dict[str, Any]:
    return {"external_sign_in_only": external}


def code(response: httpx.Response) -> str:
    detail = response.json()["detail"]
    return str(detail["code"])


def is_off(response: httpx.Response) -> bool:
    return response.status_code == 403 and code(response) == "password_sign_in_off"


# Turning it on and off


def test_an_admin_with_a_linked_sign_in_turns_it_on_and_off(
    admin_client: TestClient,
    sso: None,
    db_session: Session,
    admin: User,
    caplog: pytest.LogCaptureFixture,
) -> None:
    link(db_session, admin)
    assert admin_client.get(SETTINGS).json()["external_sign_in"] == "available"
    caplog.set_level(logging.INFO)
    for external, outcome in ((True, "on"), (False, "off")):
        caplog.clear()
        response = admin_client.put(SETTINGS, json=body(external=external))
        assert response.status_code == 200
        assert response.json()["external_sign_in_only"] is external
        assert admin_client.get(CONFIG).json()["password_sign_in"] is not external
        [logged] = [r for r in caplog.records if r.message == "external_sign_in_only_changed"]
        assert (getattr(logged, "user_id", None), getattr(logged, "outcome", None)) == (
            admin.id,
            outcome,
        )


def test_it_is_not_offered_without_a_provider(
    admin_client: TestClient,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    admin: User,
) -> None:
    monkeypatch.setattr(application.state, "sso", None, raising=False)
    link(db_session, admin)
    assert admin_client.get(SETTINGS).json()["external_sign_in"] == "not_configured"
    response = admin_client.put(SETTINGS, json=body(external=True))
    assert (response.status_code, code(response)) == (409, "external_sign_in_not_configured")
    assert stored_settings(db_session).external_sign_in_only is False


@pytest.mark.parametrize("linked", ["nothing", "another provider", "another user"])
def test_an_admin_without_a_sign_in_of_the_provider_may_not_turn_it_on(
    admin_client: TestClient,
    sso: None,
    db_session: Session,
    admin: User,
    user: User,
    linked: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    if linked == "another provider":
        link(db_session, admin, issuer="https://old.example.test")
    elif linked == "another user":
        link(db_session, user)
    assert admin_client.get(SETTINGS).json()["external_sign_in"] == "not_linked"
    caplog.set_level(logging.INFO)
    changed = body(external=True) | {"audit_retention_days": 30}
    response = admin_client.put(SETTINGS, json=changed)
    assert (response.status_code, code(response)) == (409, "external_sign_in_not_linked")
    # Nothing of the change is kept.
    assert stored_settings(db_session).audit_retention_days == 90
    assert "external_sign_in_only_refused" in caplog.messages


@pytest.mark.usefixtures("external_only")
def test_an_unlinked_admin_still_changes_other_settings_and_turns_it_off(
    admin_client: TestClient,
) -> None:
    kept = admin_client.put(SETTINGS, json=body(external=True) | {"audit_retention_days": 30})
    assert kept.status_code == 200
    assert kept.json()["external_sign_in_only"] is True
    # A form loaded before it was turned on leaves it on.
    others = admin_client.put(SETTINGS, json={"session_ip": "none"})
    assert others.json()["external_sign_in_only"] is True
    assert admin_client.put(SETTINGS, json=body(external=False)).status_code == 200


def test_the_setting_is_an_admins(user_client: TestClient, sso: None) -> None:
    assert user_client.put(SETTINGS, json=body(external=True)).status_code == 403


# While it is on


@pytest.mark.usefixtures("external_only")
def test_nobody_signs_in_with_a_password(
    api_client: TestClient, user: User, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    assert api_client.get(CONFIG).json() == {
        "enabled": True,
        "name": "Example ID",
        "password_sign_in": False,
    }
    # The same for a wrong password or an unknown user, and never throttled.
    wrong = {"username": USERNAME, "password": "wrong"}
    unknown = {"username": "nobody", "password": PASSWORD}
    for credentials in (CREDENTIALS, wrong, unknown, wrong):
        response = api_client.post(LOGIN, json=credentials)
        assert is_off(response)
        assert "set-cookie" not in response.headers
    assert "password_refused" in caplog.messages
    assert PASSWORD not in caplog.text


@pytest.mark.usefixtures("external_only")
def test_nobody_changes_their_password(user_client: TestClient, user: User) -> None:
    change = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert is_off(user_client.post("/api/v1/auth/password", json=change))
    assert password_matches(user.password_hash, PASSWORD)


@pytest.mark.usefixtures("external_only")
def test_an_admin_resets_no_password(
    admin_client: TestClient, user: User, db_session: Session
) -> None:
    path = f"/api/v1/users/{user.id}/password"
    assert is_off(admin_client.post(path, json={"password": NEW_PASSWORD}))
    assert password_matches(user.password_hash, PASSWORD)


@pytest.mark.usefixtures("external_only")
def test_an_admin_still_signs_a_user_out(
    admin_client: TestClient, user: User, db_session: Session
) -> None:
    session = create_session(db_session, user)
    path = f"/api/v1/users/{user.id}/sign-out"
    assert admin_client.post(path, json={"revoke_access": True}).status_code == 204
    assert session_user(db_session, session) is None


@pytest.mark.usefixtures("external_only", "admin")
def test_who_may_ask_is_checked_first(
    api_client: TestClient, user: User, db_session: Session
) -> None:
    change = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    reset = f"/api/v1/users/{user.id}/password"
    assert api_client.post("/api/v1/auth/password", json=change).status_code == 401
    assert api_client.post(reset, json={"password": NEW_PASSWORD}).status_code == 401
    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, user))
    refused = api_client.post(reset, json={"password": NEW_PASSWORD})
    assert refused.json() == {"detail": "Admins only"}


@pytest.mark.usefixtures("external_only")
def test_users_are_still_added_and_sessions_kept(
    admin_client: TestClient, db_session: Session
) -> None:
    assert admin_client.get("/api/v1/auth/me").json()["user"]["username"] == ADMIN_USERNAME
    added = admin_client.post("/api/v1/users", json={"username": "bob", "password": PASSWORD})
    assert added.status_code == 201
    # As `mise run create-user` adds one.
    create_user(db_session, "carol", PASSWORD, is_admin=True)
    assert user_by_username(db_session, "carol") is not None


@pytest.mark.usefixtures("external_only")
def test_the_last_sign_in_of_the_provider_is_not_unlinked(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    first, second = link(db_session, user), link(db_session, user, "sub-2")
    other = link(db_session, user, issuer="https://old.example.test")
    path = "/api/v1/me/identities"
    assert user_client.delete(f"{path}/{first}").status_code == 204
    response = user_client.delete(f"{path}/{second}")
    assert (response.status_code, code(response)) == (409, "only_sign_in")
    assert user_client.delete(f"{path}/{other}").status_code == 204
    assert user_client.delete(f"{path}/{first}").status_code == 404


# The ways back in


@pytest.mark.usefixtures("external_only")
@pytest.mark.parametrize(
    ("value", "works"), [("on", True), (" ON ", True), ("", False), ("off", False), ("1", False)]
)
def test_the_environment_overrides_the_setting(
    api_client: TestClient, user: User, monkeypatch: pytest.MonkeyPatch, value: str, works: bool
) -> None:
    monkeypatch.setenv(PASSWORD_SIGN_IN_ENV, value)
    assert api_client.get(CONFIG).json()["password_sign_in"] is works
    response = api_client.post(LOGIN, json=CREDENTIALS)
    assert response.status_code == 200 if works else is_off(response)


@pytest.mark.usefixtures("external_only")
def test_the_environments_override_brings_every_password_route_back(
    admin_client: TestClient, user: User, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PASSWORD_SIGN_IN_ENV, "on")
    settings = admin_client.get(SETTINGS).json()
    assert (settings["external_sign_in_only"], settings["password_sign_in_forced"]) == (True, True)
    path = f"/api/v1/users/{user.id}/password"
    assert admin_client.post(path, json={"password": NEW_PASSWORD}).status_code == 204
    change = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert admin_client.post("/api/v1/auth/password", json=change).status_code == 204
    admin_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, user))
    identity = link(db_session, user)
    assert admin_client.delete(f"/api/v1/me/identities/{identity}").status_code == 204


@pytest.mark.usefixtures("external_only")
def test_the_setting_holds_nothing_back_without_a_provider(
    api_client: TestClient, application: FastAPI, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(application.state, "sso", None)
    assert api_client.get(CONFIG).json()["password_sign_in"] is True
    assert api_client.post(LOGIN, json=CREDENTIALS).status_code == 200


@pytest.mark.parametrize(
    ("value", "said"), [("on", "even with external"), ("no", "is ignored"), ("", "")]
)
def test_startup_says_what_the_environment_does(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str, said: str
) -> None:
    monkeypatch.setenv(PASSWORD_SIGN_IN_ENV, value)
    warn_about_environment()
    assert said in caplog.text
    assert bool(caplog.records) is bool(said)
