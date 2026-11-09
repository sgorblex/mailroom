"""The settings, sealed mailbox secrets and send log: core's part of `src/store/db.ts`.

Every row belongs to one user, and each call reads or writes one user's only. The functions taking
a `Connection` run inside the caller's transaction, so mailroom-mcp can save its own half of the
settings in the same one.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from psycopg.errors import LockNotAvailable
from sqlalchemy import and_, delete, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.exc import OperationalError

from mailroom_core.store.schema import account_secrets, sends, settings

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from sqlalchemy import ColumnElement, Connection, Engine, Row, Table

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None
"""A JSON value, as stored in a JSONB column."""

type SendState = Literal["sending", "sent", "failed", "unknown"]
"""`sending` ends in `sent`, in `failed` if the server definitely refused, else in `unknown`."""

type FinishedSendState = Literal["sent", "failed", "unknown"]

SETTINGS_HISTORY: Final = 100
"""Revisions of the settings kept."""

LOCK_TIMEOUT: Final = timedelta(seconds=5)
"""How long a settings save or a send claim waits for another, as SQLite's busy_timeout."""

SEND_CEILING: Final = timedelta(seconds=360)
"""The longest a send can run: one still `sending` after this reads as `unknown`, as after a
restart that lost it."""

_SEND_LOG_SPAN: Final = timedelta(days=1)
_KEYED_SEND_SPAN: Final = timedelta(days=30)
_SEND_LIMIT_WINDOW: Final = timedelta(hours=1)


class RevisionConflict(Exception):  # noqa: N818 -- the TypeScript name
    """The settings changed since the caller read them: its write is refused."""


class SettingsBusy(Exception):  # noqa: N818 -- named as RevisionConflict is
    """Another save held the settings for longer than `LOCK_TIMEOUT`: this write is refused."""


class SendsBusy(Exception):  # noqa: N818 -- named as RevisionConflict is
    """Another claim held the send log for longer than `LOCK_TIMEOUT`: nothing was claimed."""


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


class SendLimitReached(Exception):  # noqa: N818 -- named as RevisionConflict is
    """The hourly limit is used up: nothing was claimed. `recent` says when the next send fits."""

    def __init__(self, recent: SendCount) -> None:
        super().__init__(f"{recent.count} sends in the last hour")
        self.recent: Final = recent


@dataclass(frozen=True, slots=True)
class SendRecord:
    """A send as `claim_send` wrote it. `at` is when its latest attempt was claimed."""

    id: int
    key: str | None
    state: SendState
    account: str | None
    result: Json
    at: datetime


@dataclass(frozen=True, slots=True)
class SendClaim:
    """What `claim_send` decided. `fresh`: this caller owns the attempt and must deliver it and
    call `finish_send`. Otherwise `send` is the earlier attempt and nothing may be delivered: its
    `state` is `sent` (replay `result`), `sending` (same id) or `unknown` (ask to confirm).
    """

    send: SendRecord
    fresh: bool


class SendLog(Protocol):
    """Messages handed to SMTP: the hourly send limit and each send's outcome."""

    def sends_since(self, since: datetime) -> SendCount: ...

    def record_send(self, at: datetime) -> None: ...

    def claim_send(
        self,
        key: str | None,
        account: str,
        limit: int,
        *,
        confirm_resend: bool = False,
        now: datetime | None = None,
    ) -> SendClaim: ...

    def finish_send(
        self, send: SendRecord, state: FinishedSendState, result: Json = None
    ) -> bool: ...

    def send_state(self, send_id: int, now: datetime | None = None) -> SendRecord | None: ...

    def send_state_by_key(self, key: str, now: datetime | None = None) -> SendRecord | None: ...


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
    busy = SettingsBusy("another save of the settings is still running")
    _lock_user_rows(connection, settings, user_id, lock_timeout, busy)


def _lock_user_rows(
    connection: Connection, table: Table, user_id: str, lock_timeout: timedelta, busy: Exception
) -> None:
    """Take `user_id`'s lock on `table` until the transaction ends, or raise `busy`. The
    transaction's `lock_timeout` stays set, for any other lock it takes."""
    connection.execute(
        text("select set_config('lock_timeout', :timeout, true)"),
        {"timeout": f"{int(lock_timeout.total_seconds() * 1000)}ms"},
    )
    with _busy_past_lock_timeout(busy):
        connection.execute(
            text("select pg_advisory_xact_lock(hashtext(:table), hashtext(:user_id))"),
            {"table": table.fullname, "user_id": user_id},
        )


@contextmanager
def _busy_past_lock_timeout(busy: Exception) -> Iterator[None]:
    try:
        yield
    except OperationalError as error:
        if isinstance(error.orig, LockNotAvailable):
            raise busy from None
        raise


def _sends_since(connection: Connection, user_id: str, since: datetime) -> SendCount:
    row = connection.execute(
        select(func.count(), func.min(sends.c.at)).where(
            sends.c.user_id == user_id, sends.c.at > since
        )
    ).one()
    return SendCount(count=row[0], oldest=row[1])


def _prune_sends(connection: Connection, user_id: str, now: datetime) -> None:
    """Drop `user_id`'s rows that neither the hourly limit nor a retry can still need."""
    connection.execute(
        delete(sends).where(
            sends.c.user_id == user_id,
            or_(
                and_(sends.c.key.is_(None), sends.c.at <= now - _SEND_LOG_SPAN),
                sends.c.at <= now - _KEYED_SEND_SPAN,
            ),
        )
    )


_SEND_RECORD: Final = (
    sends.c.id,
    sends.c.key,
    sends.c.state,
    sends.c.account,
    sends.c.result,
    sends.c.at,
)


def _send_record(row: Row[*tuple[Any, ...]], now: datetime) -> SendRecord:
    stale = row.state == "sending" and now - row.at > SEND_CEILING
    return SendRecord(
        id=row.id,
        key=row.key,
        state="unknown" if stale else row.state,
        account=row.account,
        result=row.result,
        at=row.at,
    )


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
            return _sends_since(connection, self.user_id, since)

    def record_send(self, at: datetime) -> None:
        with self._engine.begin() as connection:
            _prune_sends(connection, self.user_id, at)
            connection.execute(insert(sends).values(user_id=self.user_id, at=at))

    def claim_send(
        self,
        key: str | None,
        account: str,
        limit: int,
        *,
        confirm_resend: bool = False,
        now: datetime | None = None,
    ) -> SendClaim:
        """Write a `sending` row before SMTP, unless a retry of `key` must not deliver again.

        The count and the insert share a per-user lock, so concurrent claims admit exactly `limit`
        in the hour and one key wins once. A send without a `key` is never a retry.

        A key already `sent`, `sending` or, without `confirm_resend`, `unknown` answers with that
        send, not `fresh`, whatever the limit. A `failed` one, or a confirmed `unknown` one, is
        claimed again under the same id: a new attempt, so it needs room under `limit` and the
        previous attempt stays counted, since it may have been delivered.

        Raises `SendLimitReached` at the limit, `SendsBusy` if another claim holds the lock too
        long.
        """
        now = now or datetime.now(UTC)
        busy = SendsBusy("another send is still being claimed")
        with self._engine.begin() as connection:
            _lock_user_rows(connection, sends, self.user_id, self._lock_timeout, busy)
            _prune_sends(connection, self.user_id, now)
            earlier = None
            if key is not None:
                # The row lock: `finish_send` takes no advisory lock, so an outcome could land
                # after this read and be overwritten by the reclaim below.
                with _busy_past_lock_timeout(busy):
                    earlier = connection.execute(
                        select(*_SEND_RECORD)
                        .where(sends.c.user_id == self.user_id, sends.c.key == key)
                        .with_for_update()
                    ).one_or_none()
            if earlier is not None:
                send = _send_record(earlier, now)
                if send.state in ("sent", "sending") or (
                    send.state == "unknown" and not confirm_resend
                ):
                    return SendClaim(send, fresh=False)
            recent = _sends_since(connection, self.user_id, now - _SEND_LIMIT_WINDOW)
            if recent.count >= limit:
                raise SendLimitReached(recent)
            if earlier is None:
                row = connection.execute(
                    insert(sends)
                    .values(user_id=self.user_id, key=key, state="sending", account=account, at=now)
                    .returning(*_SEND_RECORD)
                ).one()
            else:
                connection.execute(insert(sends).values(user_id=self.user_id, at=earlier.at))
                row = connection.execute(
                    update(sends)
                    .where(sends.c.id == earlier.id)
                    .values(state="sending", account=account, result=None, at=now)
                    .returning(*_SEND_RECORD)
                ).one()
            return SendClaim(_send_record(row, now), fresh=True)

    def finish_send(self, send: SendRecord, state: FinishedSendState, result: Json = None) -> bool:
        """Record how the attempt in `send`, from a fresh claim, ended; False if nothing changed.

        A finished state never changes, though finishing again with the same state replaces
        `result`, for what happens after delivery (filing to Sent). An attempt that has since been
        reclaimed no longer owns the row, and one past `SEND_CEILING` still does: its real outcome
        beats the `unknown` it reads as.
        """
        with self._engine.begin() as connection:
            updated = connection.execute(
                update(sends)
                .where(
                    sends.c.id == send.id,
                    sends.c.user_id == self.user_id,
                    sends.c.at == send.at,
                    sends.c.state.in_(("sending", state)),
                )
                .values(state=state, result=result)
            )
            return updated.rowcount > 0

    def send_state(self, send_id: int, now: datetime | None = None) -> SendRecord | None:
        """This user's send `send_id`, if it was claimed and is still kept."""
        return self._send(sends.c.id == send_id, now)

    def send_state_by_key(self, key: str, now: datetime | None = None) -> SendRecord | None:
        """This user's send of `key`, if any is still kept."""
        return self._send(sends.c.key == key, now)

    def _send(self, match: ColumnElement[bool], now: datetime | None) -> SendRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(*_SEND_RECORD).where(
                    sends.c.user_id == self.user_id, sends.c.state.is_not(None), match
                )
            ).one_or_none()
        return None if row is None else _send_record(row, now or datetime.now(UTC))
