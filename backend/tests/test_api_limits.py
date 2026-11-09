from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from mailroom.accounts import limits as limits_service
from mailroom.accounts.store import load_accounts, user_store
from mailroom_core.store import RevisionConflict, SettingsBusy
from tests.test_api_accounts import new_account

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from starlette.testclient import TestClient

    from mailroom.models import User

DEFAULTS = {
    "send_per_hour": 20,
    "max_recipients": 10,
    "body_chars": 6000,
    "attachment_max_mb": 10.0,
}


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/me/limits").status_code == 401
    assert api_client.put("/api/v1/me/limits", json=DEFAULTS).status_code == 401


def test_a_user_starts_with_the_defaults(user_client: TestClient) -> None:
    assert user_client.get("/api/v1/me/limits").json() == DEFAULTS


def test_a_user_changes_their_limits_and_keeps_their_accounts(
    user_client: TestClient,
    user: User,
    migrated_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    changed = {
        "send_per_hour": 5,
        "max_recipients": 30,
        "body_chars": 20000,
        "attachment_max_mb": 25,
    }

    response = user_client.put("/api/v1/me/limits", json=changed)

    assert response.status_code == 200
    assert response.json() == {**changed, "attachment_max_mb": 25.0}
    assert user_client.get("/api/v1/me/limits").json() == response.json()
    settings = load_accounts(user_store(migrated_engine, user.id)).settings
    assert [account.id for account in settings.account] == ["work"]
    listed = user_client.get("/api/v1/accounts").json()[0]
    assert (listed["attachment_max_mb"], listed["max_recipients"]) == (25.0, 30)
    assert [
        getattr(r, "user_id", None) for r in caplog.records if r.getMessage() == "limits_changed"
    ] == [user.id]


@pytest.mark.parametrize(
    ("change", "path"),
    [
        ({"attachment_max_mb": 25.5}, ["attachment_max_mb"]),
        ({"attachment_max_mb": 0}, ["attachment_max_mb"]),
        ({"body_chars": 499}, ["body_chars"]),
        ({"send_per_hour": 0}, ["send_per_hour"]),
    ],
)
def test_a_limit_out_of_bounds_is_refused_by_field(
    user_client: TestClient, change: dict[str, Any], path: list[str]
) -> None:
    response = user_client.put("/api/v1/me/limits", json=DEFAULTS | change)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_limits"
    assert [issue["path"] for issue in detail["issues"]] == [path]
    assert user_client.get("/api/v1/me/limits").json() == DEFAULTS


def test_every_limit_out_of_bounds_is_named_at_once(user_client: TestClient) -> None:
    change = {"attachment_max_mb": 30, "body_chars": 10}
    response = user_client.put("/api/v1/me/limits", json=DEFAULTS | change)

    assert response.status_code == 400
    paths = sorted(issue["path"] for issue in response.json()["detail"]["issues"])
    assert paths == [["attachment_max_mb"], ["body_chars"]]


def test_a_stored_attachment_limit_above_the_cap_reads_as_the_cap(
    user_client: TestClient, user: User, migrated_engine: Engine
) -> None:
    store = user_store(migrated_engine, user.id)
    store.save_settings({"limits": {"attachment_max_mb": 40.0}}, 0)

    assert user_client.get("/api/v1/me/limits").json()["attachment_max_mb"] == 25.0


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [(RevisionConflict, 409, "settings_changed"), (SettingsBusy, 503, "settings_busy")],
)
def test_a_refused_save_is_worded(
    user_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception],
    status: int,
    code: str,
) -> None:
    def save_limits(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(limits_service, "save_limits", save_limits)
    response = user_client.put("/api/v1/me/limits", json=DEFAULTS)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
