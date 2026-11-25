from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from mailroom.config import instance_settings as module
from mailroom.config.instance_settings import (
    RETENTION_DAYS_ENV,
    InstanceSettings,
    InstanceSettingsChange,
    change_instance_settings,
    default_settings,
    instance_settings,
    stored_settings,
)
from mailroom.models import InstanceSetting

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User

PATH = "/api/v1/admin/settings"


@pytest.fixture(autouse=True)
def _no_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RETENTION_DAYS_ENV, raising=False)


def rows(db: Session) -> dict[str, Any]:
    stored = db.execute(select(InstanceSetting.key, InstanceSetting.value))
    return {key: value for key, value in stored}  # noqa: C416


def settings(days: int = 90, session_ip: str = "partial") -> dict[str, Any]:
    return {"audit_retention_days": days, "session_ip": session_ip}


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.get(PATH).status_code == 401
    assert api_client.put(PATH, json=settings()).status_code == 401


def test_users_who_are_not_admins_are_refused(user_client: TestClient, db_session: Session) -> None:
    for response in (user_client.get(PATH), user_client.put(PATH, json=settings(1))):
        assert response.status_code == 403
        assert response.json() == {"detail": "Admins only"}
    assert rows(db_session) == {}


def test_cross_site_changes_are_refused(admin_client: TestClient, db_session: Session) -> None:
    response = admin_client.put(PATH, json=settings(1), headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert rows(db_session) == {}


def test_an_admin_changes_the_settings(
    admin_client: TestClient, db_session: Session, admin: User, caplog: pytest.LogCaptureFixture
) -> None:
    assert admin_client.get(PATH).json() == settings()
    caplog.set_level(logging.INFO)
    response = admin_client.put(PATH, json=settings(30, "none"))
    assert response.status_code == 200
    assert response.json() == settings(30, "none")
    assert admin_client.get(PATH).json() == settings(30, "none")
    [row] = db_session.scalars(
        select(InstanceSetting).where(InstanceSetting.key == "audit_retention_days")
    )
    assert row.updated_by == admin.id
    [logged] = [r for r in caplog.records if r.getMessage() == "instance_settings_changed"]
    assert (getattr(logged, "user_id", None), getattr(logged, "settings", None)) == (
        admin.id,
        ["audit_retention_days", "session_ip"],
    )


def test_a_setting_changes_again(admin_client: TestClient, db_session: Session) -> None:
    admin_client.put(PATH, json=settings(30))
    assert admin_client.put(PATH, json=settings(90)).json() == settings(90)
    assert rows(db_session) == {"audit_retention_days": 90}


def test_a_change_leaves_the_settings_not_given(
    admin_client: TestClient, db_session: Session
) -> None:
    admin_client.put(PATH, json=settings(30, "none"))
    assert admin_client.put(PATH, json={"audit_retention_days": 60}).json() == settings(60, "none")
    assert admin_client.put(PATH, json={"session_ip": "full"}).json() == settings(60, "full")
    assert admin_client.put(PATH, json={}).json() == settings(60, "full")
    assert rows(db_session) == settings(60, "full")


def test_a_setting_left_as_it_is_keeps_following_the_environment(
    admin_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(RETENTION_DAYS_ENV, "365")
    admin_client.put(PATH, json=settings(365, "full"))
    assert rows(db_session) == {"session_ip": "full"}
    monkeypatch.setenv(RETENTION_DAYS_ENV, "200")
    assert admin_client.get(PATH).json() == settings(200, "full")


def test_a_change_of_nothing_is_not_logged(
    admin_client: TestClient, db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    assert admin_client.put(PATH, json=settings()).status_code == 200
    assert rows(db_session) == {}
    assert "instance_settings_changed" not in caplog.messages


@pytest.mark.parametrize(
    "body",
    [
        settings(0),
        settings(3651),
        settings() | {"audit_retention_days": "30"},
        settings() | {"audit_retention_days": True},
        settings() | {"audit_retention_days": None},
        settings(session_ip="some"),
        settings() | {"other": 1},
        {"audit_retention_days": None},
        {"session_ip": None},
        {"session_ip": "none", "other": 1},
    ],
)
def test_an_invalid_change_is_refused(
    admin_client: TestClient, db_session: Session, body: dict[str, Any]
) -> None:
    assert admin_client.put(PATH, json=body).status_code == 422
    assert rows(db_session) == {}


@pytest.mark.parametrize("days", [1, 3650])
def test_the_retention_bounds_are_allowed(admin_client: TestClient, days: int) -> None:
    assert admin_client.put(PATH, json=settings(days)).json() == settings(days)


@pytest.mark.parametrize(
    ("raw", "days"), [(None, 90), ("", 90), ("x", 90), ("0", 1), ("365", 365), ("99999", 3650)]
)
def test_the_environments_retention_is_read_and_clamped(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, days: int
) -> None:
    if raw is not None:
        monkeypatch.setenv(RETENTION_DAYS_ENV, raw)
    assert default_settings().audit_retention_days == days


def test_stored_values_no_longer_valid_read_as_their_defaults(db_session: Session) -> None:
    db_session.add_all(
        [
            InstanceSetting(key="audit_retention_days", value=-5),
            InstanceSetting(key="session_ip", value="full"),
            InstanceSetting(key="from_a_newer_version", value=["x"]),
        ]
    )
    db_session.flush()
    assert stored_settings(db_session).model_dump() == settings(90, "full")


def test_a_setting_outlives_the_admin_who_changed_it(db_session: Session, admin: User) -> None:
    change_instance_settings(db_session, admin, InstanceSettingsChange(audit_retention_days=30))
    db_session.delete(admin)
    db_session.flush()
    db_session.expire_all()
    [row] = db_session.scalars(select(InstanceSetting))
    assert (row.value, row.updated_by) == (30, None)


def test_the_settings_are_cached_for_a_moment(
    db_session: Session, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [0.0]
    monkeypatch.setattr(module, "CACHE", module._Cache(lambda: now[0]))  # noqa: SLF001
    assert instance_settings(db_session).session_ip == "partial"
    db_session.add(InstanceSetting(key="session_ip", value="none"))
    db_session.flush()
    now[0] = 4.9
    assert instance_settings(db_session).session_ip == "partial"
    now[0] = 5.0
    assert instance_settings(db_session).session_ip == "none"
    # A change in this process shows at once.
    change_instance_settings(db_session, admin, InstanceSettingsChange(session_ip="full"))
    assert instance_settings(db_session).session_ip == "full"


def test_a_read_a_change_overtakes_is_not_kept() -> None:
    cache = module._Cache()  # noqa: SLF001
    before = InstanceSettings(audit_retention_days=90, session_ip="full")
    after = InstanceSettings(audit_retention_days=90, session_ip="none")

    def overtaken() -> InstanceSettings:
        cache.drop()
        return before

    assert cache.get(overtaken) == before
    assert cache.get(lambda: after) == after
