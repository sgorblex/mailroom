"""The settings, sealed mailbox secrets and send log: core's part of `src/store/db.ts`.

Every row belongs to one user, and each call reads or writes one user's only. The functions taking
a `Connection` run inside the caller's transaction, so mailroom-mcp can save its own half of the
settings in the same one.
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


def current_settings(connection: Connection, user_id: str) -> StoredSettings | None:
    """The highest revision of `user_id`'s settings, if any was saved."""
    row = connection.execute(
        select(settings.c.revision, settings.c.doc)
        .where(settings.c.user_id == user_id)
        .order_by(settings.c.revision.desc())
        .limit(1)
    ).one_or_none()
    return None if row is None else StoredSettings(revision=row.revision, doc=row.doc)


def write_settings(
    connection: Connection,
    user_id: str,
    doc: Json,
    expected: int,
    secrets: SecretChanges,
    now: datetime,
    lock_timeout: timedelta = LOCK_TIMEOUT,
) -> int:
    """Store `doc` as `user_id`'s revision `expected + 1` with its secret changes, unless their
    settings are no longer at revision `expected` (0: none yet). Returns the new revision.

    One user's writers queue on a lock of their own, as `begin immediate` makes them in SQLite;
    other users' writers and every reader do not wait. One that waits longer than `lock_timeout`
    raises `SettingsBusy`.
    """
    _lock_settings(connection, user_id, lock_timeout)
    stored = current_settings(connection, user_id)
    current = 0 if stored is None else stored.revision
    if current != expected:
        raise RevisionConflict(f"the settings are at revision {current}, not {expected}")
    revision = expected + 1
    connection.execute(
        insert(settings).values(user_id=user_id, revision=revision, doc=doc, saved_at=now)
    )
    connection.execute(
        delete(settings).where(
            settings.c.user_id == user_id, settings.c.revision <= revision - SETTINGS_HISTORY
        )
    )
    if secrets.remove:
        connection.execute(
            delete(account_secrets).where(
                account_secrets.c.user_id == user_id,
                account_secrets.c.account_id.in_(secrets.remove),
            )
        )
    for account_id, blob in secrets.set.items():
        statement = upsert(account_secrets).values(
            user_id=user_id, account_id=account_id, purpose="password", blob=blob, updated_at=now
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    account_secrets.c.user_id,
                    account_secrets.c.account_id,
                    account_secrets.c.purpose,
                ],
                set_={"blob": statement.excluded.blob, "updated_at": statement.excluded.updated_at},
            )
        )
    return revision


def forget_user(
    connection: Connection, user_id: str, lock_timeout: timedelta = LOCK_TIMEOUT
) -> None:
    """Delete everything stored for `user_id`: their settings and its history, their sealed
    secrets and their send log. Other users' rows are untouched.

    It queues on the lock `write_settings` takes, so it cannot interleave with a save of the same
    user's settings, and raises `SettingsBusy` after waiting longer than `lock_timeout`. Run inside
    the caller's transaction, it lets a host app delete its own rows for the user in the same one.
    """
    _lock_settings(connection, user_id, lock_timeout)
    connection.execute(delete(settings).where(settings.c.user_id == user_id))
    connection.execute(delete(account_secrets).where(account_secrets.c.user_id == user_id))
    connection.execute(delete(sends).where(sends.c.user_id == user_id))


def _lock_settings(connection: Connection, user_id: str, lock_timeout: timedelta) -> None:
    """Take `user_id`'s settings lock until the transaction ends, or raise `SettingsBusy`."""
    connection.execute(
        text("select set_config('lock_timeout', :timeout, true)"),
        {"timeout": f"{int(lock_timeout.total_seconds() * 1000)}ms"},
    )
    try:
        connection.execute(
            text("select pg_advisory_xact_lock(hashtext(:table), hashtext(:user_id))"),
            {"table": settings.fullname, "user_id": user_id},
        )
    except OperationalError as error:
        if isinstance(error.orig, LockNotAvailable):
            raise SettingsBusy("another save of the settings is still running") from None
        raise


class AccountStore:
    """One user's settings, sealed mailbox secrets and send log, in schema `core`.

    mailroom-mcp's `Store` saves the settings through `write_settings`, with its profiles, for
    `STANDALONE_USER`; the Mailroom app keeps schema `core` in its own database, with one store
    per signed-in user.
    """

    def __init__(
        self, engine: Engine, user_id: str, lock_timeout: timedelta = LOCK_TIMEOUT
    ) -> None:
        self._engine = engine
        self.user_id: Final = user_id
        self._lock_timeout = lock_timeout

    def settings(self) -> StoredSettings | None:
        with self._engine.connect() as connection:
            return current_settings(connection, self.user_id)

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
                self.user_id,
                doc,
                expected,
                secrets or SecretChanges(),
                now or datetime.now(UTC),
                self._lock_timeout,
            )

    def forget(self) -> None:
        """Delete everything stored for this user (`forget_user`), raising `SettingsBusy` if a
        save holds their settings too long.
        """
        with self._engine.begin() as connection:
            forget_user(connection, self.user_id, self._lock_timeout)

    def secrets(self) -> dict[str, bytes]:
        """The sealed mailbox secrets, by account id."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(account_secrets.c.account_id, account_secrets.c.blob).where(
                    account_secrets.c.user_id == self.user_id,
                    account_secrets.c.purpose == "password",
                )
            )
            return {row.account_id: row.blob for row in rows}

    def sends_since(self, since: datetime) -> SendCount:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(func.count(), func.min(sends.c.at)).where(
                    sends.c.user_id == self.user_id, sends.c.at > since
                )
            ).one()
        return SendCount(count=row[0], oldest=row[1])

    def record_send(self, at: datetime) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                delete(sends).where(
                    sends.c.user_id == self.user_id, sends.c.at <= at - _SEND_LOG_SPAN
                )
            )
            connection.execute(insert(sends).values(user_id=self.user_id, at=at))
