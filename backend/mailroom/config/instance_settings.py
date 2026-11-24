"""What admins choose for the whole instance, kept in ``app.instance_setting``.

Only the settings an admin changed are stored; the others follow their defaults, the environment's
where there is one. Specification: ``docs/requirements/nonfunctional-requirements.md``, *Sign-in*.
"""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING, Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from mailroom.models import InstanceSetting, User

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

RETENTION_DAYS_ENV: Final = "MAILROOM_AUDIT_RETENTION_DAYS"
MAX_RETENTION_DAYS: Final = 3650
_DEFAULT_RETENTION_DAYS: Final = 90
_CACHE_SECONDS: Final = 5.0


RetentionDays = Annotated[StrictInt, Field(ge=1, le=MAX_RETENTION_DAYS)]
SessionIp = Literal["none", "partial", "full"]


class InstanceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_retention_days: RetentionDays
    """Audit records older than this many days are purged."""
    session_ip: SessionIp
    """What of the client's IP address a session records at sign-in, and at most shows."""
    external_sign_in_only: StrictBool
    """Users sign in through the identity provider alone: ``mailroom.auth.password_sign_in``, which
    says when it holds."""


class InstanceSettingsChange(BaseModel):
    """The settings to change; the ones not given stay as they are."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_retention_days: RetentionDays | None = None
    session_ip: SessionIp | None = None
    external_sign_in_only: StrictBool | None = None

    @field_validator("*")
    @classmethod
    def _not_null(cls, value: object) -> object:
        # Runs for given fields only.
        if value is None:
            msg = "must not be null"
            raise ValueError(msg)
        return value


def default_settings() -> InstanceSettings:
    """The settings of an instance whose admins changed none."""
    return InstanceSettings(
        audit_retention_days=_retention_days_from_env(),
        session_ip="partial",
        external_sign_in_only=False,
    )


def _retention_days_from_env() -> int:
    """``MAILROOM_AUDIT_RETENTION_DAYS`` (default 90, clamped to 1-3650)."""
    raw = os.environ.get(RETENTION_DAYS_ENV, "").strip()
    try:
        days = int(raw) if raw else _DEFAULT_RETENTION_DAYS
    except ValueError:
        days = _DEFAULT_RETENTION_DAYS
    return min(max(days, 1), MAX_RETENTION_DAYS)


def stored_settings(db: Session) -> InstanceSettings:
    """The settings as the database has them now. A stored value that is not valid reads as its
    default, and an unknown key is left alone: another version wrote it."""
    settings = default_settings()
    known = InstanceSetting.key.in_(InstanceSettings.model_fields)
    for key, value in db.execute(select(InstanceSetting.key, InstanceSetting.value).where(known)):
        settings = _with_valid(settings, key, value)
    return settings


def _with_valid(settings: InstanceSettings, key: str, value: object) -> InstanceSettings:
    try:
        return InstanceSettings.model_validate({**settings.model_dump(), key: value})
    except ValidationError:
        return settings


class _Cache:
    """The settings as last read, for a few seconds; per process."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._held: tuple[float, InstanceSettings] | None = None
        self._drops = 0

    def get(self, read: Callable[[], InstanceSettings]) -> InstanceSettings:
        with self._lock:
            if self._held is not None and self._clock() - self._held[0] < _CACHE_SECONDS:
                return self._held[1]
            drops = self._drops
            since = self._clock()
        settings = read()
        with self._lock:
            # Not if dropped meanwhile: the read may have come before the change.
            if drops == self._drops:
                self._held = (since, settings)
        return settings

    def drop(self) -> None:
        with self._lock:
            self._held = None
            self._drops += 1


CACHE: Final = _Cache()


def instance_settings(db: Session) -> InstanceSettings:
    """The settings, up to a few seconds old where another process changed them."""
    return CACHE.get(lambda: stored_settings(db))


def change_instance_settings(db: Session, admin: User, wanted: InstanceSettingsChange) -> list[str]:
    """Store the settings of ``wanted`` that differ from the ones in effect, and commit; their
    names. The others are not stored, so they keep following their defaults."""
    current = stored_settings(db).model_dump()
    given = wanted.model_dump(exclude_unset=True)
    changed = {key: value for key, value in given.items() if value != current[key]}
    for key, value in changed.items():
        row = {"value": value, "updated_by": admin.id}
        db.execute(
            insert(InstanceSetting)
            .values(key=key, **row)
            .on_conflict_do_update(
                index_elements=[InstanceSetting.key], set_={**row, "updated_at": func.now()}
            )
        )
    db.commit()
    CACHE.drop()
    return list(changed)
