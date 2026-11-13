"""The audit trail of mail writes: one ``app.audit_record`` per message a write acted on or tried
to, from mailroom-core's ``AuditItem``s (the web and MCP) and from filter runs.

Never a subject or a body: core gives a subject's hash only, and the senders and recipients in
clear, which an investigation needs. Writing a record never fails the write it is about: a
database error is logged (``audit_write_failed``, counts and codes only) and the write's answer
stands. Records older than ``MAILROOM_AUDIT_RETENTION_DAYS`` are purged as new ones are written,
at most once an hour in each process, so no scheduler is needed.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from sqlalchemy import delete, func, insert

from mailroom.models import AuditRecord

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.accounts.service import Via
    from mailroom_core.mail import AuditItem

logger = logging.getLogger(__name__)

Actor = Literal["user", "agent", "filter"]
"""Who acted: the user on the web, an agent through MCP, or a rule set's run."""

RETENTION_DAYS_ENV: Final = "MAILROOM_AUDIT_RETENTION_DAYS"
_DEFAULT_RETENTION_DAYS: Final = 365
_PURGE_EVERY_SECONDS: Final = 3600.0


@dataclass(frozen=True, slots=True)
class Origin:
    """Where a write came from: the web, or MCP with the connected app and grant that called."""

    via: Via = "web"
    client_id: str | None = None
    grant_id: str | None = None

    @property
    def actor(self) -> Actor:
        return "agent" if self.via == "mcp" else "user"


WEB: Final = Origin()
MCP: Final = Origin("mcp")
"""An MCP call whose app is not known: tests, and a ``ToolCall`` built outside ``call_tool``."""


def retention() -> timedelta:
    """``MAILROOM_AUDIT_RETENTION_DAYS`` (default 365, clamped to 1-3650)."""
    raw = os.environ.get(RETENTION_DAYS_ENV, "").strip()
    try:
        days = int(raw) if raw else _DEFAULT_RETENTION_DAYS
    except ValueError:
        days = _DEFAULT_RETENTION_DAYS
    return timedelta(days=min(max(days, 1), 3650))


@dataclass(frozen=True, slots=True, kw_only=True)
class Entry:
    """One record to write."""

    actor: Actor
    action: str
    account_id: str
    result: str
    detail: dict[str, Any]
    client_id: str | None = None
    grant_id: str | None = None
    rule_set_id: int | None = None
    run_id: int | None = None


class _Purge:
    """When this process last purged; at most one purge an hour, across threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: float | None = None

    def due(self) -> bool:
        with self._lock:
            now = time.monotonic()
            if self._last is not None and now - self._last < _PURGE_EVERY_SECONDS:
                return False
            self._last = now
            return True

    def reset(self) -> None:
        """For tests: the next write purges."""
        with self._lock:
            self._last = None


PURGE: Final = _Purge()


def purge_old(db: Session) -> None:
    """Delete every user's records older than the retention."""
    db.execute(delete(AuditRecord).where(AuditRecord.at < func.now() - retention()))


def write_records(sessions: sessionmaker[Session], user_id: int, entries: Iterable[Entry]) -> None:
    """Write ``entries`` for ``user_id`` in a session and transaction of their own, then purge in
    another when due. Never raises: a failure is logged and the records are lost (a failed purge
    waits for the next one)."""
    rows = [_row(user_id, entry) for entry in entries]
    if not rows:
        return
    try:
        db = sessions()
    except Exception as exc:
        _log_failure("audit_write_failed", user_id, len(rows), exc)
        return
    with db:
        _write(db, user_id, rows)


def write_records_in(db: Session, user_id: int, entries: Iterable[Entry]) -> None:
    """``write_records`` in ``db``, which must hold no open work: each step committed, or rolled
    back on a failure."""
    rows = [_row(user_id, entry) for entry in entries]
    if rows:
        _write(db, user_id, rows)


def _write(db: Session, user_id: int, rows: list[dict[str, Any]]) -> None:
    if not _committed(db, user_id, len(rows), "audit_write_failed", lambda: _insert(db, rows)):
        return
    if PURGE.due():
        _committed(db, user_id, 0, "audit_purge_failed", lambda: purge_old(db))


def _insert(db: Session, rows: list[dict[str, Any]]) -> None:
    db.execute(insert(AuditRecord), rows)


def _committed(
    db: Session, user_id: int, records: int, event: str, work: Callable[[], None]
) -> bool:
    """``work`` committed; whether it was. A failure, the rollback's own included, is logged as
    ``event`` and goes no further: the write it records already happened."""
    try:
        work()
        db.commit()
    except Exception as exc:
        # The failure above is the one to report, whatever the rollback does.
        with contextlib.suppress(Exception):
            db.rollback()
        _log_failure(event, user_id, records, exc)
        return False
    return True


def _log_failure(event: str, user_id: int, records: int, exc: Exception) -> None:
    logger.warning(
        event,
        extra={"event": event, "user_id": user_id, "records": records, "error": type(exc).__name__},
    )


def _row(user_id: int, entry: Entry) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "actor": entry.actor,
        "client_id": entry.client_id,
        "grant_id": entry.grant_id,
        "rule_set_id": entry.rule_set_id,
        "run_id": entry.run_id,
        "action": entry.action,
        "account_id": entry.account_id,
        "result": entry.result,
        "detail": entry.detail,
    }


@dataclass(slots=True)
class AuditTrail:
    """The records of one write call: ``note`` is mailroom-core's callback, called from whichever
    thread does the work (the mail pool's, or a send's after the request ended), and writes at
    once; ``notes`` writes a batch (``OrganizeResult.items``)."""

    sessions: sessionmaker[Session]
    user_id: int
    origin: Origin
    action: str

    def note(self, item: AuditItem, extra: Mapping[str, object] | None = None) -> None:
        """``item``'s record, with ``extra`` added to its detail: what the app did beside it (the
        rule sets a folder's rename changed)."""
        write_records(self.sessions, self.user_id, [self._entry(item, extra)])

    def notes(self, items: Iterable[AuditItem]) -> None:
        entries = [self._entry(item) for item in items]
        write_records(self.sessions, self.user_id, entries)

    def _entry(self, item: AuditItem, extra: Mapping[str, object] | None = None) -> Entry:
        detail = {
            key: value for key, value in item.record.items() if key not in {"account", "result"}
        }
        if extra:
            detail.update(extra)
        return Entry(
            actor=self.origin.actor,
            action=self.action,
            account_id=item.account,
            result=item.result,
            detail=detail,
            client_id=self.origin.client_id,
            grant_id=self.origin.grant_id,
        )


def kept_nowhere(_item: AuditItem) -> None:
    """The callback for a write that keeps no record: the web composer's autosave, the user
    typing (docs/requirements/mail-client.md, *Drafts*)."""
