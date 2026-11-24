"""Keeping an indexed folder in step with its server: what the worker's index jobs run
(``mailroom.worker.tasks``), under the mailbox's lock: a folder asked for in a job of its own, the
folders a turn finds due several to a job.

A job surveys the folder under EXAMINE: its UIDVALIDITY, UIDNEXT and HIGHESTMODSEQ, and the UIDs the
age limit keeps (``mailroom_core.mail.plan_index``). The rows whose UIDs it no longer lists go
(expunged, or now older than the age limit). The UIDs the index lacks, new mail included, are
fetched newest first, ``INDEX_BATCH`` at a time with a pause between batches, at most
``SLICE_BATCHES`` batches per job and none begun past the job's time (``until``): a job with more to
fetch asks for another, which waits behind the mailbox's other jobs. The job that ends a pass brings
the stored flags up to date: those changed since the stored HIGHESTMODSEQ where the folder has
mod-sequences (CONDSTORE), else every message's, and on Gmail the labels with them. A UIDVALIDITY
that changed drops the folder's rows, which are then built again. A message that cannot be fetched
or stored is skipped, its UID alone logged, and stays readable live. Nothing here changes mail:
every read is under EXAMINE.

Each commit that changed the folder's rows or its state announces it on ``NOTIFY_CHANNEL``
(``notify``), a batch with the range of the new mail it stored: the UIDs from the folder's
UIDNEXT at its last pass on, none on a first sync or a rebuild. The same new mail, in INBOX,
queues the account's vacation replies (``mailroom.mail.vacation``).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import BigInteger, any_, bindparam, delete, or_, select
from sqlalchemy.dialects.postgresql import ARRAY

from mailroom.auth.users import time_zone_of
from mailroom.mail import vacation
from mailroom.mail.connections import PASSWORD_MISSING, PasswordMissing
from mailroom.mail.index import threads
from mailroom.mail.index.rows import apply_flags, count_rows, flag_kinds, upsert_rows
from mailroom.mail.index.settings import effective_max_age
from mailroom.mail.index.writes import notify
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
    from collections.abc import Sequence
    from datetime import date

    from sqlalchemy.orm import Session

    from mailroom_core.config import TimeZoneName
    from mailroom_core.mail import FolderFlags, IndexedMessage, MailboxContext, MailConnections
    from mailroom_core.render import Address

logger = logging.getLogger(__name__)

RESYNC_EVERY: Final = timedelta(minutes=5)
"""How often a ready folder is synced again."""
RESYNC_SLACK: Final = timedelta(minutes=4)
"""A folder is due this much early: it is synced after the dispatcher's turn, behind the
account's listing and the other folders, yet it is due again at the next turn."""
SLICE_BATCHES: Final = 20
"""Batches one job fetches at most before it hands the mailbox to the next job."""
BATCH_PAUSE_SECONDS: Final = 0.5
"""The pause between two batches of a job, so a first sync does not hammer the server."""
SYNC_FAILED: Final = "failed"
"""The error of a sync that failed other than on the mailbox; its exception's type is logged."""
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
    new_from: int | None
    """The UIDNEXT of the folder's last pass, under the same UIDVALIDITY: the UIDs from it on are
    new mail; None on a first sync or a rebuild."""
    compare: bool
    """Rows were stored under this UIDVALIDITY: the pass compares their flags before it ends."""
    flags: FolderFlags | None
    """The flags (on Gmail the labels too) to compare with the stored ones; None unless one batch
    at most is missing, which the job surely fetches. Else the job that ends the pass reads them."""


def since_day(max_age_days: int | None, now: datetime, zone: TimeZoneName) -> date | None:
    """The first day the age limit keeps (the user's, in ``zone``), None without a limit."""
    if max_age_days is None:
        return None
    return now.astimezone(ZoneInfo(zone)).date() - timedelta(days=max_age_days)


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
    until: float | None = None,
) -> bool:
    """One job of the sync of the user's indexed folder ``folder_id``; whether another job has
    more to fetch. Past ``until`` (``time.monotonic``'s) it begins no batch but its first, so that
    every job gets on. A folder gone or switched off meanwhile is left alone. A mailbox failure
    fails the folder (``state`` failed, ``error`` its code), as anything unexpected does
    (``SYNC_FAILED``). Commits ``db``; no transaction stays open while the mailbox works."""
    now = now or datetime.now(UTC)
    row = _folder(db, user_id, account_id, folder_id)
    if row is None:
        db.rollback()
        return False
    user = db.get(User, user_id)
    max_age = effective_max_age(None if user is None else user.index_max_age_days)
    since = since_day(max_age, now, time_zone_of(db, user_id))
    stored = frozenset(
        db.scalars(
            select(MailIndexMessage.uid).where(
                MailIndexMessage.folder_id == folder_id, MailIndexMessage.user_id == user_id
            )
        )
    )
    path, validity, modseq, uid_next = row.path, row.uid_validity, row.highest_modseq, row.uid_next
    db.commit()
    capacity = SLICE_BATCHES * INDEX_BATCH

    def survey(context: MailboxContext) -> _Survey:
        return _survey(context, since, stored, (validity, modseq, uid_next))

    try:
        # An EXAMINE of its own: changes are asked since the HIGHESTMODSEQ it gives.
        found = connections.with_mailbox(account_id, path, True, survey, reselect=True)
        rebuilt, removed, changed = _reconcile(db, user_id, account_id, folder_id, found)
        if rebuilt is None:
            return False
        fetched = _fetch(
            db, connections, user_id, account_id, folder_id, path, found, capacity, until
        )
        if fetched is None:
            return False
        more = len(found.missing) > capacity or fetched < min(len(found.missing), capacity)
        if not more and found.compare and found.flags is None:
            flags = connections.with_mailbox(
                account_id, path, True, lambda context: _flags(context, modseq), reselect=True
            )
            updated = _apply(db, user_id, account_id, folder_id, flags)
            if updated is None:
                return False
            changed += updated
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
    last: tuple[int | None, int | None, int | None],
) -> _Survey:
    """``last``: the folder's UIDVALIDITY, HIGHESTMODSEQ and UIDNEXT at its last pass."""
    validity, modseq, uid_next = last
    plan = plan_index(context, since=since)
    listed = [uid for batch in plan.batches for uid in batch]
    held = stored if context.uid_validity == validity else frozenset()
    missing = tuple(uid for uid in listed if uid not in held)
    return _Survey(
        uid_validity=plan.uid_validity,
        uid_next=context.uid_next,
        highest_modseq=plan.highest_modseq,
        missing=missing,
        gone=held.difference(listed),
        new_from=uid_next if context.uid_validity == validity else None,
        compare=bool(held),
        flags=_flags(context, modseq) if held and len(missing) <= INDEX_BATCH else None,
    )


def _flags(context: MailboxContext, modseq: int | None) -> FolderFlags:
    """The flags changed since ``modseq``, every message's without mod-sequences."""
    if modseq is not None and context.highest_modseq is not None:
        return changed_flags_of(context, modseq)
    return flags_of(context)


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
    state = row.state
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
    if found.flags is not None and row.uid_validity == found.uid_validity:
        changed = apply_flags(db, user_id, folder_id, found.flags.flags, found.flags.labels)
    row.uid_validity = found.uid_validity
    if row.state != "ready":
        row.state, row.error = "syncing", None
    count_rows(db, row)
    if removed or changed or row.state != state:
        notify(db, user_id, account_id, folder_id)
    db.commit()
    return rebuilt, removed, changed


def _apply(
    db: Session, user_id: int, account_id: int, folder_id: int, flags: FolderFlags
) -> int | None:
    """Apply the flag and label changes read at a pass's end; how many (none once UIDVALIDITY
    changed: the next sync rebuilds), None when the folder went off meanwhile."""
    row = _folder(db, user_id, account_id, folder_id, lock=True)
    if row is None:
        db.rollback()
        return None
    changed = 0
    if row.uid_validity == flags.uid_validity:
        changed = apply_flags(db, user_id, folder_id, flags.flags, flags.labels)
    if changed:
        count_rows(db, row)
        notify(db, user_id, account_id, folder_id)
    db.commit()
    return changed


def _fetch(
    db: Session,
    connections: MailConnections,
    user_id: int,
    account_id: int,
    folder_id: int,
    path: str,
    found: _Survey,
    capacity: int,
    until: float | None,
) -> int | None:
    """Fetch and store the job's share of the missing UIDs, batch by batch, each committed; how
    many UIDs were asked (short of the share when its time ran out, or when UIDVALIDITY changed
    meanwhile: the next job goes on, or rebuilds), None when the folder went off meanwhile.
    A batch that fails other than on the mailbox is fetched again one UID at a time, and a UID that
    still fails is skipped."""
    share = found.missing[:capacity]
    asked = 0
    for start in range(0, len(share), INDEX_BATCH):
        if start:
            if until is not None and time.monotonic() >= until:
                return asked
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
    _store(db, user_id, account_id, folder_id, messages)
    count_rows(db, row)
    start = found.new_from
    fresh = [message for message in messages if start is not None and message.uid >= start]
    new = [message.uid for message in fresh]
    notify(db, user_id, account_id, folder_id, new=(min(new), max(new)) if new else None)
    _queue_replies(db, user_id, account_id, fresh)
    db.commit()
    return "stored"


def _queue_replies(
    db: Session, user_id: int, account_id: int, fresh: Sequence[IndexedMessage]
) -> None:
    """``vacation.queue_vacation_replies`` in a savepoint: one that fails costs those replies,
    never the batch's rows."""
    try:
        with db.begin_nested():
            vacation.queue_vacation_replies(db, user_id, account_id, fresh)
    except Exception as error:
        logger.warning(
            "vacation_queue_failed",
            extra={
                "event": "vacation_queue_failed",
                "user_id": user_id,
                "account_id": account_id,
                "error_type": type(error).__name__,
            },
        )


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
        if row.state != "ready":
            notify(db, user_id, account_id, folder_id)
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
        if row.state != "failed":
            notify(db, user_id, account_id, folder_id)
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


def _people(addresses: Sequence[Address]) -> list[dict[str, str | None]]:
    return [{"name": a.name, "address": a.address} for a in addresses]


def _text(addresses: Sequence[Address]) -> str:
    return " ".join(part for a in addresses for part in (a.name, a.address) if part)


def _offset_minutes(moment: datetime | None) -> int | None:
    """``moment``'s UTC offset in minutes; None without a date."""
    offset = None if moment is None else moment.utcoffset()
    return None if offset is None else round(offset.total_seconds() / 60)


def _row(
    user_id: int, folder_id: int, message: IndexedMessage, thread_key: str | None
) -> dict[str, Any]:
    system, keywords = flag_kinds(message.flags)
    return {
        "folder_id": folder_id,
        "uid": message.uid,
        "user_id": user_id,
        "received": message.received or message.date or _EPOCH,
        "date": message.date,
        "date_offset": _offset_minutes(message.date),
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
        "thread_key": thread_key,
        "list_id": message.list_id,
        "gm_msgid": message.gmail_message_id,
        "gm_thrid": message.gmail_thread_id,
        "labels": list(message.gmail_labels),
    }


def _store(
    db: Session,
    user_id: int,
    account_id: int,
    folder_id: int,
    messages: Sequence[IndexedMessage],
) -> None:
    """``messages`` stored as rows, each keyed to its conversation (``threads``)."""
    keys = threads.thread_keys(db, user_id, account_id, messages)
    upsert_rows(db, [_row(user_id, folder_id, m, keys[m.uid]) for m in messages])
    threads.rejoin(db, user_id, account_id, [(m, keys[m.uid]) for m in messages])
