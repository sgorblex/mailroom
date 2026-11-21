"""Keeping an indexed folder in step with its server: what the worker's index jobs run
(``mailroom.worker.tasks``), one folder per job, under the mailbox's lock.

A job surveys the folder under EXAMINE: its UIDVALIDITY, UIDNEXT and HIGHESTMODSEQ, and the UIDs
the age limit keeps (``mailroom_core.mail.plan_index``). The rows whose UIDs it no longer lists
go (expunged, or now older than the age limit). The UIDs the index lacks, new mail included, are
fetched newest first, ``INDEX_BATCH`` at a time with a pause between batches, at most
``SLICE_BATCHES`` batches per job: a job with more to fetch asks for another, which waits behind
the other mailboxes' jobs. The job that ends a pass brings the stored flags up to date: those
changed since the stored HIGHESTMODSEQ where the folder has mod-sequences (CONDSTORE), else every
message's, and on Gmail the labels with them. A UIDVALIDITY that changed drops the folder's rows,
which are then built again. A message that cannot be fetched or stored is skipped, its UID alone
logged, and stays readable live. Nothing here changes mail: every read is under EXAMINE.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from sqlalchemy import BigInteger, any_, bindparam, delete, func, literal, not_, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY, insert

from mailroom.mail.connections import PASSWORD_MISSING, PasswordMissing
from mailroom.mail.index.settings import effective_max_age
from mailroom.models import MailIndexFolder, MailIndexMessage, User
from mailroom_core.mail import (
    INDEX_BATCH,
    MailError,
    changed_flags_of,
    flags_of,
    indexed_messages,
    plan_index,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from sqlalchemy.orm import Session

    from mailroom_core.mail import IndexedMessage, MailboxContext, MailConnections
    from mailroom_core.render import Address

logger = logging.getLogger(__name__)

RESYNC_EVERY: Final = timedelta(minutes=5)
"""How often a ready folder is synced again."""
RESYNC_SLACK: Final = timedelta(minutes=4)
"""A folder is due this much early: its job starts after the dispatcher's turn, behind the
account's listing and the other folders' jobs, yet it is due again at the next turn."""
SLICE_BATCHES: Final = 20
"""Batches one job fetches at most before it hands the mailbox to the next job."""
BATCH_PAUSE_SECONDS: Final = 0.5
"""The pause between two batches of a job, so a first sync does not hammer the server."""
SYNC_FAILED: Final = "failed"
"""The error of a sync that failed other than on the mailbox; its exception's type is logged."""
_SEEN: Final = "\\Seen"
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
"""The arrival time of a message whose server gave none, nor a Date header."""


@dataclass(frozen=True, slots=True)
class _Survey:
    uid_validity: int
    uid_next: int | None
    highest_modseq: int | None
    missing: tuple[int, ...]
    """The UIDs to fetch, newest first: all of the listed ones once UIDVALIDITY changed."""
    gone: frozenset[int]
    """Stored UIDs no longer listed."""
    flags: Mapping[int, frozenset[str]] | None
    """Flags to compare with the stored ones; None but on a pass's last job."""
    labels: Mapping[int, tuple[str, ...]] | None
    """On Gmail, the labels of the same messages as ``flags``."""


def since_day(max_age_days: int | None, now: datetime) -> date | None:
    """The first day the age limit keeps (UTC), None without a limit."""
    return None if max_age_days is None else now.date() - timedelta(days=max_age_days)


def due_folders(db: Session, user_id: int, account_id: int, now: datetime) -> list[int]:
    """The ids of the account's indexed folders to sync at ``now``: not ready, or last synced
    ``RESYNC_EVERY`` ago (less ``RESYNC_SLACK``)."""
    query = (
        select(MailIndexFolder.id)
        .where(
            MailIndexFolder.user_id == user_id,
            MailIndexFolder.account_id == account_id,
            MailIndexFolder.enabled,
            or_(
                MailIndexFolder.state != "ready",
                MailIndexFolder.last_synced_at.is_(None),
                MailIndexFolder.last_synced_at <= now - RESYNC_EVERY + RESYNC_SLACK,
            ),
        )
        .order_by(MailIndexFolder.id)
    )
    return list(db.scalars(query))


def sync_folder(
    db: Session,
    connections: MailConnections,
    user_id: int,
    account_id: int,
    folder_id: int,
    *,
    now: datetime | None = None,
) -> bool:
    """One job of the sync of the user's indexed folder ``folder_id``; whether another job has
    more to fetch. A folder gone or switched off meanwhile is left alone. A mailbox failure fails
    the folder (``state`` failed, ``error`` its code), as anything unexpected does
    (``SYNC_FAILED``). Commits ``db``; no transaction stays open while the mailbox works."""
    now = now or datetime.now(UTC)
    row = _folder(db, user_id, account_id, folder_id)
    if row is None:
        db.rollback()
        return False
    user = db.get(User, user_id)
    since = since_day(effective_max_age(None if user is None else user.index_max_age_days), now)
    stored = frozenset(
        db.scalars(
            select(MailIndexMessage.uid).where(
                MailIndexMessage.folder_id == folder_id, MailIndexMessage.user_id == user_id
            )
        )
    )
    path, validity, modseq = row.path, row.uid_validity, row.highest_modseq
    db.commit()
    capacity = SLICE_BATCHES * INDEX_BATCH

    def survey(context: MailboxContext) -> _Survey:
        return _survey(context, since, stored, validity, modseq, capacity)

    try:
        found = connections.with_mailbox(account_id, path, True, survey)
        rebuilt, removed, changed = _reconcile(db, user_id, account_id, folder_id, found)
        if rebuilt is None:
            return False
        fetched = _fetch(db, connections, user_id, account_id, folder_id, path, found, capacity)
        if fetched is None:
            return False
        more = len(found.missing) > capacity or fetched < min(len(found.missing), capacity)
        if not _finish(db, user_id, account_id, folder_id, found, since, now, more=more):
            return False
    except MailError as exc:
        fail_folder(db, user_id, account_id, folder_id, exc.code)
        return False
    except PasswordMissing:
        fail_folder(db, user_id, account_id, folder_id, PASSWORD_MISSING)
        return False
    except Exception as exc:
        # Its text may quote anything, mail included: the log names its type only.
        logger.error("A sync of index folder %s failed: %s.", folder_id, type(exc).__name__)
        fail_folder(db, user_id, account_id, folder_id, SYNC_FAILED)
        return False
    if not (rebuilt or fetched or removed or changed or more):
        return False
    logger.info(
        "mail_index_synced",
        extra={
            "event": "mail_index_synced",
            "user_id": user_id,
            "account_id": account_id,
            "folder_id": folder_id,
            "rebuilt": rebuilt,
            "fetched": fetched,
            "removed": removed,
            "flags_changed": changed,
            "more": more,
        },
    )
    return more


def _survey(
    context: MailboxContext,
    since: date | None,
    stored: frozenset[int],
    validity: int | None,
    modseq: int | None,
    capacity: int,
) -> _Survey:
    plan = plan_index(context, since=since)
    listed = [uid for batch in plan.batches for uid in batch]
    held = stored if context.uid_validity == validity else frozenset()
    missing = tuple(uid for uid in listed if uid not in held)
    flags = None
    if held and len(missing) <= capacity:
        if modseq is not None and context.highest_modseq is not None:
            flags = changed_flags_of(context, modseq)
        else:
            flags = flags_of(context)
    return _Survey(
        uid_validity=plan.uid_validity,
        uid_next=context.uid_next,
        highest_modseq=plan.highest_modseq,
        missing=missing,
        gone=held.difference(listed),
        flags=None if flags is None else flags.flags,
        labels=None if flags is None else flags.labels,
    )


def _reconcile(
    db: Session, user_id: int, account_id: int, folder_id: int, found: _Survey
) -> tuple[bool | None, int, int]:
    """Drop what the survey says went (every row, once UIDVALIDITY changed) and apply the flag
    and label changes; whether the folder was rebuilt (None when it went off meanwhile), the rows
    removed and the flags changed."""
    row = _folder(db, user_id, account_id, folder_id, lock=True)
    if row is None:
        db.rollback()
        return None, 0, 0
    rebuilt = row.uid_validity is not None and row.uid_validity != found.uid_validity
    messages = delete(MailIndexMessage).where(
        MailIndexMessage.folder_id == folder_id, MailIndexMessage.user_id == user_id
    )
    removed = 0
    if row.uid_validity != found.uid_validity:
        removed = len(db.scalars(messages.returning(MailIndexMessage.uid)).all())
        row.highest_modseq = row.uid_next = row.oldest_indexed = None
        if rebuilt:
            row.state = "syncing"
    elif found.gone:
        # One array parameter: an age limit made shorter may drop more UIDs than a query binds.
        uids = bindparam("gone", sorted(found.gone), type_=ARRAY(BigInteger))
        gone = messages.where(MailIndexMessage.uid == any_(uids))
        removed = len(db.scalars(gone.returning(MailIndexMessage.uid)).all())
    changed = 0
    if found.flags and row.uid_validity == found.uid_validity:
        changed = _apply_flags(db, user_id, folder_id, found.flags, found.labels)
    row.uid_validity = found.uid_validity
    if row.state != "ready":
        row.state, row.error = "syncing", None
    _count(db, row)
    db.commit()
    return rebuilt, removed, changed


def _fetch(
    db: Session,
    connections: MailConnections,
    user_id: int,
    account_id: int,
    folder_id: int,
    path: str,
    found: _Survey,
    capacity: int,
) -> int | None:
    """Fetch and store the job's share of the missing UIDs, batch by batch, each committed; how
    many UIDs were asked (short of the share when UIDVALIDITY changed meanwhile: the next job
    rebuilds), None when the folder went off meanwhile. A batch that fails other than on the
    mailbox is fetched again one UID at a time, and a UID that still fails is skipped."""
    share = found.missing[:capacity]
    asked = 0
    for start in range(0, len(share), INDEX_BATCH):
        if start:
            time.sleep(BATCH_PAUSE_SECONDS)
        batch = share[start : start + INDEX_BATCH]
        try:
            outcome = _stored_batch(
                db, connections, user_id, account_id, folder_id, path, found, batch
            )
        except Exception as exc:
            if isinstance(exc, MailError):
                raise
            db.rollback()
            outcome = _stored_one_by_one(
                db, connections, user_id, account_id, folder_id, path, found, batch
            )
        if outcome == "stale":
            return asked
        if outcome == "off":
            return None
        asked += len(batch)
    return asked


type _Outcome = Literal["stored", "stale", "off"]
"""A batch stored, or not: the folder's UIDVALIDITY changed, or the folder went off."""


def _stored_batch(
    db: Session,
    connections: MailConnections,
    user_id: int,
    account_id: int,
    folder_id: int,
    path: str,
    found: _Survey,
    batch: Sequence[int],
) -> _Outcome:
    try:
        messages = indexed_messages(
            connections, account_id, path, batch, uid_validity=found.uid_validity
        )
    except MailError as exc:
        if exc.code == "stale_reference":
            return "stale"
        raise
    row = _folder(db, user_id, account_id, folder_id, lock=True)
    if row is None or row.uid_validity != found.uid_validity:
        db.rollback()
        return "off"
    _store(db, user_id, folder_id, messages)
    _count(db, row)
    db.commit()
    return "stored"


def _stored_one_by_one(
    db: Session,
    connections: MailConnections,
    user_id: int,
    account_id: int,
    folder_id: int,
    path: str,
    found: _Survey,
    batch: Sequence[int],
) -> _Outcome:
    """``batch`` stored a UID at a time, so a message that cannot be fetched or stored (a parsing
    edge case, a value the database refuses) holds up no other: it is skipped, logged by its UID
    and its exception's type only, and stays readable live."""
    for uid in batch:
        try:
            outcome = _stored_batch(
                db, connections, user_id, account_id, folder_id, path, found, (uid,)
            )
        except Exception as exc:
            if isinstance(exc, MailError):
                raise
            db.rollback()
            logger.warning(
                "Index folder %s skipped UID %s: %s.", folder_id, uid, type(exc).__name__
            )
            continue
        if outcome != "stored":
            return outcome
    return "stored"


def _finish(
    db: Session,
    user_id: int,
    account_id: int,
    folder_id: int,
    found: _Survey,
    since: date | None,
    now: datetime,
    *,
    more: bool,
) -> bool:
    """Mark a pass that fetched all it lacked ready, at the state it surveyed; whether the
    folder was still on."""
    row = _folder(db, user_id, account_id, folder_id, lock=True)
    if row is None:
        db.rollback()
        return False
    if not more:
        row.uid_next = found.uid_next
        row.highest_modseq = found.highest_modseq
        row.oldest_indexed = since
        row.state, row.error, row.last_synced_at = "ready", None, now
    db.commit()
    return True


def fail_folder(db: Session, user_id: int, account_id: int, folder_id: int, code: str) -> None:
    """Fail the folder's sync with ``code``, a mailbox failure's (or the app's own when the
    account cannot log in: ``password_missing``, ``secrets_key_missing``). Commits ``db``."""
    db.rollback()
    row = _folder(db, user_id, account_id, folder_id, lock=True)
    if row is not None:
        row.state, row.error = "failed", code
    db.commit()
    logger.info(
        "mail_index_sync_failed",
        extra={
            "event": "mail_index_sync_failed",
            "user_id": user_id,
            "account_id": account_id,
            "folder_id": folder_id,
            "error": code,
        },
    )


def _folder(
    db: Session, user_id: int, account_id: int, folder_id: int, *, lock: bool = False
) -> MailIndexFolder | None:
    """The user's folder row while it is on the index; locked against a switch meanwhile."""
    query = select(MailIndexFolder).where(
        MailIndexFolder.id == folder_id,
        MailIndexFolder.user_id == user_id,
        MailIndexFolder.account_id == account_id,
    )
    row = db.scalar(query.with_for_update() if lock else query)
    return row if row is not None and row.enabled else None


def _count(db: Session, row: MailIndexFolder) -> None:
    unread = not_(literal(_SEEN) == any_(MailIndexMessage.flags))
    total, unseen = db.execute(
        select(func.count(), func.count().filter(unread)).where(
            MailIndexMessage.folder_id == row.id, MailIndexMessage.user_id == row.user_id
        )
    ).one()
    row.message_count, row.unread_count = total, unseen


def _split(flags: frozenset[str]) -> tuple[list[str], list[str]]:
    """System flags (``\\Seen``...) and keywords, each sorted."""
    return (
        sorted(flag for flag in flags if flag.startswith("\\")),
        sorted(flag for flag in flags if not flag.startswith("\\")),
    )


def _apply_flags(
    db: Session,
    user_id: int,
    folder_id: int,
    flags: Mapping[int, frozenset[str]],
    labels: Mapping[int, tuple[str, ...]] | None = None,
) -> int:
    """Store the server's flags (and on Gmail labels) of the rows they differ on; how many."""
    # One array parameter, of the reported UIDs only: CHANGEDSINCE reports a handful.
    reported = bindparam("reported", sorted(flags), type_=ARRAY(BigInteger))
    held = db.execute(
        select(
            MailIndexMessage.uid,
            MailIndexMessage.flags,
            MailIndexMessage.keywords,
            MailIndexMessage.labels,
        ).where(
            MailIndexMessage.folder_id == folder_id,
            MailIndexMessage.user_id == user_id,
            MailIndexMessage.uid == any_(reported),
        )
    )
    current = {
        uid: (sorted(system), sorted(keywords), list(stored_labels))
        for uid, system, keywords, stored_labels in held
    }
    changes: list[dict[str, Any]] = []
    for uid, server in flags.items():
        if uid not in current:
            continue
        system, keywords = _split(server)
        held_labels = current[uid][2]
        new_labels = held_labels if labels is None else list(labels.get(uid, ()))
        if current[uid] != (system, keywords, new_labels):
            changes.append(
                {"at": uid, "new_flags": system, "new_keywords": keywords, "new_labels": new_labels}
            )
    if changes:
        statement = (
            update(MailIndexMessage)
            .where(
                MailIndexMessage.folder_id == folder_id,
                MailIndexMessage.user_id == user_id,
                MailIndexMessage.uid == bindparam("at"),
            )
            .values(
                flags=bindparam("new_flags"),
                keywords=bindparam("new_keywords"),
                labels=bindparam("new_labels"),
            )
        )
        db.connection().execute(statement, changes)
    return len(changes)


def _people(addresses: Sequence[Address]) -> list[dict[str, str | None]]:
    return [{"name": a.name, "address": a.address} for a in addresses]


def _text(addresses: Sequence[Address]) -> str:
    return " ".join(part for a in addresses for part in (a.name, a.address) if part)


def _row(user_id: int, folder_id: int, message: IndexedMessage) -> dict[str, Any]:
    system, keywords = _split(message.flags)
    return {
        "folder_id": folder_id,
        "uid": message.uid,
        "user_id": user_id,
        "received": message.received or message.date or _EPOCH,
        "date": message.date,
        "sender": _people(message.sender),
        "recipients": {"to": _people(message.to), "cc": _people(message.cc)},
        "sender_text": _text(message.sender),
        "to_text": _text(message.to),
        "subject": message.subject,
        "flags": system,
        "keywords": keywords,
        "size": message.size,
        "has_attachments": message.has_attachments,
        "snippet": message.snippet,
        "message_id": message.message_id,
        "in_reply_to": message.in_reply_to,
        "references_root": message.references_root,
        "thread_key": message.thread_key,
        "list_id": message.list_id,
        "gm_msgid": message.gmail_message_id,
        "gm_thrid": message.gmail_thread_id,
        "labels": list(message.gmail_labels),
    }


def _store(db: Session, user_id: int, folder_id: int, messages: Sequence[IndexedMessage]) -> None:
    if not messages:
        return
    statement = insert(MailIndexMessage).values(
        [_row(user_id, folder_id, message) for message in messages]
    )
    kept = ("folder_id", "uid", "user_id")
    columns = [column.name for column in MailIndexMessage.__table__.columns]
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["folder_id", "uid"],
            set_={name: statement.excluded[name] for name in columns if name not in kept},
        )
    )
