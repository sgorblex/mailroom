"""The settings, sealed mailbox secrets and send log: core's part of `src/store/db.ts`.

The functions taking a `Connection` run inside the caller's transaction, so mailroom-mcp can save
its own half of the settings in the same one.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol

from psycopg.errors import LockNotAvailable
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.exc import OperationalError

from mailroom_core.store.schema import account_secrets, sends, settings

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy import Connection, Engine

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None
"""A JSON value, as stored in a JSONB column."""

SETTINGS_HISTORY: Final = 100
"""Revisions of the settings kept."""

LOCK_TIMEOUT: Final = timedelta(seconds=5)
"""How long a settings save waits for another to finish, as the SQLite store's busy_timeout."""

_SEND_LOG_SPAN: Final = timedelta(days=1)


class RevisionConflict(Exception):  # noqa: N818 -- the TypeScript name
    """The settings changed since the caller read them: its write is refused."""


class SettingsBusy(Exception):  # noqa: N818 -- named as RevisionConflict is
    """Another save held the settings for longer than `LOCK_TIMEOUT`: this write is refused."""


@dataclass(frozen=True, slots=True)
class StoredSettings:
    revision: int
    doc: Json
    """The JSON document as stored; validated by the caller."""


@dataclass(frozen=True, slots=True)
class SecretChanges:
    """Stored secrets to write with a settings save: sealed blobs to set, accounts to clear."""

    set: Mapping[str, bytes] = field(default_factory=lambda: MappingProxyType({}))
    remove: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class SendCount:
    """Sends since a moment: how many, and when the oldest was."""

    count: int
    oldest: datetime | None


class SendLog(Protocol):
    """Messages handed to SMTP, for the hourly send limit."""

    def sends_since(self, since: datetime) -> SendCount: ...

    def record_send(self, at: datetime) -> None: ...


def current_settings(connection: Connection) -> StoredSettings | None:
    """The highest revision of the settings, if any was saved."""
    row = connection.execute(
        select(settings.c.revision, settings.c.doc).order_by(settings.c.revision.desc()).limit(1)
    ).one_or_none()
    return None if row is None else StoredSettings(revision=row.revision, doc=row.doc)


def write_settings(
    connection: Connection,
    doc: Json,
    expected: int,
    secrets: SecretChanges,
    now: datetime,
    lock_timeout: timedelta = LOCK_TIMEOUT,
) -> int:
    """Store `doc` as revision `expected + 1` with its secret changes, unless the settings are no
    longer at revision `expected` (0: none yet). Returns the new revision.

    Writers queue on a table lock, as `begin immediate` makes them in SQLite; readers do not wait.
    One that waits longer than `lock_timeout` raises `SettingsBusy`.
    """
    connection.execute(
        text("select set_config('lock_timeout', :timeout, true)"),
        {"timeout": f"{int(lock_timeout.total_seconds() * 1000)}ms"},
    )
    try:
        connection.execute(text(f"lock table {settings.fullname} in share row exclusive mode"))
    except OperationalError as error:
        if isinstance(error.orig, LockNotAvailable):
            raise SettingsBusy("another save of the settings is still running") from None
        raise
    stored = current_settings(connection)
    current = 0 if stored is None else stored.revision
    if current != expected:
        raise RevisionConflict(f"the settings are at revision {current}, not {expected}")
    revision = expected + 1
    connection.execute(insert(settings).values(revision=revision, doc=doc, saved_at=now))
    connection.execute(delete(settings).where(settings.c.revision <= revision - SETTINGS_HISTORY))
    if secrets.remove:
        connection.execute(
            delete(account_secrets).where(account_secrets.c.account_id.in_(secrets.remove))
        )
    for account_id, blob in secrets.set.items():
        statement = upsert(account_secrets).values(
            account_id=account_id, purpose="password", blob=blob, updated_at=now
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[account_secrets.c.account_id, account_secrets.c.purpose],
                set_={"blob": statement.excluded.blob, "updated_at": statement.excluded.updated_at},
            )
        )
    return revision


class AccountStore:
    """The settings, the sealed mailbox secrets and the send log, in schema `core`.

    mailroom-mcp's `Store` saves the settings through `write_settings`, with its profiles; the
    Mailroom app keeps schema `core` in its own database.
    """

    def __init__(self, engine: Engine, lock_timeout: timedelta = LOCK_TIMEOUT) -> None:
        self._engine = engine
        self._lock_timeout = lock_timeout

    def settings(self) -> StoredSettings | None:
        with self._engine.connect() as connection:
            return current_settings(connection)

    def save_settings(
        self,
        doc: Json,
        expected: int,
        secrets: SecretChanges | None = None,
        now: datetime | None = None,
    ) -> int:
        """Replace the settings with `doc` as revision `expected + 1`, unless they are no longer
        at revision `expected` (0: none yet), raising `RevisionConflict`, or `SettingsBusy` if
        another save holds them too long. Returns the new revision.
        """
        with self._engine.begin() as connection:
            return write_settings(
                connection,
                doc,
                expected,
                secrets or SecretChanges(),
                now or datetime.now(UTC),
                self._lock_timeout,
            )

    def secrets(self) -> dict[str, bytes]:
        """The sealed mailbox secrets, by account id."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(account_secrets.c.account_id, account_secrets.c.blob).where(
                    account_secrets.c.purpose == "password"
                )
            )
            return {row.account_id: row.blob for row in rows}

    def sends_since(self, since: datetime) -> SendCount:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(func.count(), func.min(sends.c.at)).where(sends.c.at > since)
            ).one()
        return SendCount(count=row[0], oldest=row[1])

    def record_send(self, at: datetime) -> None:
        with self._engine.begin() as connection:
            connection.execute(delete(sends).where(sends.c.at <= at - _SEND_LOG_SPAN))
            connection.execute(insert(sends).values(at=at))
