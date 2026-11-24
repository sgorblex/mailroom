"""Keeping the index in step with what the app itself writes (``docs/requirements/mail-client.md``,
*Index*): after a move, an archive, a report as spam, a trash, a flag change, a bulk call, a move
to another account or a folder marked read, from the web or an agent, in the same request.

The rows change from the server's answer, never from a read of the server: a message moved leaves
its folder's rows and, when the server said where it landed (COPYUID) and the destination is
indexed under that UIDVALIDITY, a copy of its row takes the new UID there (a move keeps the
arrival time and the flags); a flag change sets the flags asked on the messages it changed; a
folder marked read gets ``\\Seen`` below the UIDNEXT it was marked to. What the answer leaves
unsure queues the folder's sync instead (``mailroom.worker.tasks.queue_folder_sync``): a group
that failed or was refused, a move without COPYUID or whose source rows are missing, a ref whose
UIDVALIDITY the index does not hold, a bulk call (its UIDs come without one), a move to another
account (both its destination and the Trash the originals went to). A folder with no row of its
own on an account whose rows carry Gmail's message ids (``X-GM-MSGID``, read only with
``X-GM-EXT-1``) queues the account's labelled folders: All Mail holds those messages.

A write whose answer names only the folders it changed queues their syncs the same way
(``resync``): a send delivered (Sent, the draft it sent, what it answers or forwards), a draft
saved, imported or removed, and one an agent's send mode kept in Drafts; and a filter run
(``resync_in``: the folders its actions changed, another account's included).

Every folder whose rows changed or whose sync was queued is announced on ``NOTIFY_CHANNEL``
(``notify``). An index write that fails is logged by its exception's type only and leaves the mail
call's answer as it was: the next sync puts the rows right.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    any_,
    bindparam,
    delete,
    exists,
    func,
    literal,
    not_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY

from mailroom.mail.index.rows import SEEN, apply_flags, count_rows, upsert_rows
from mailroom.models import MailIndexFolder, MailIndexMessage
from mailroom_core import mail as core
from mailroom_core.mail import TO_SORT, MailError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from sqlalchemy.orm import Session, sessionmaker

    from mailroom_core.mail import AuditItem, GroupResult

logger = logging.getLogger(__name__)

NOTIFY_CHANNEL: Final = "mailroom_index"
"""The Postgres channel a folder's index change is announced on: ``{"user_id", "account_id",
"folder_id"}`` as JSON, ids only, with ``"new": [low, high]`` when a sync stored new mail: the
UIDs it stored from the folder's previous UIDNEXT on lie between the two."""
_FLAGGED: Final = "\\Flagged"

type _Key = tuple[int, str]
"""An account's folder: its id and the folder's path."""


def notify(
    db: Session,
    user_id: int,
    account_id: int,
    folder_id: int,
    *,
    new: tuple[int, int] | None = None,
) -> None:
    """Announce on ``NOTIFY_CHANNEL`` that the folder's index changed (``new``: the range of the
    UIDs of new mail stored); Postgres delivers it once ``db``'s transaction commits, and drops it
    with a rollback."""
    note: dict[str, object] = {"user_id": user_id, "account_id": account_id, "folder_id": folder_id}
    if new is not None:
        note["new"] = list(new)
    payload = json.dumps(note)
    db.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": NOTIFY_CHANNEL, "payload": payload},
    )


def organized(
    sessions: sessionmaker[Session],
    user_id: int,
    items: Sequence[AuditItem],
    groups: Sequence[GroupResult],
    *,
    refs: Sequence[str] | None = None,
    unsure: Iterable[_Key] = (),
) -> None:
    """Bring the index in step with an organize or bulk call: its audit ``items`` (one per
    message) and its ``groups``. ``refs``: the call's refs, whose UIDVALIDITY each folder's rows
    must hold to change; None for a bulk call, whose folders are synced again besides. ``unsure``:
    folders the call changed without saying how, besides the Trash a move to another account
    sent originals to (``GroupResult.trash``)."""
    validities = None if refs is None else _validities(refs)
    try:
        with sessions() as db:
            trashes = {(group.account, group.trash) for group in groups if group.trash is not None}
            _Write(db, user_id, set(unsure) | trashes).organized(items, groups, validities)
            db.commit()
    except Exception as exc:
        _failed(user_id, exc)


def marked_read(
    sessions: sessionmaker[Session],
    user_id: int,
    account_id: int,
    folder: str,
    *,
    uid_validity: int,
    uid_next: int,
    done: bool,
) -> None:
    """Bring the index in step with a folder marked read below ``uid_next`` under
    ``uid_validity``; ``done`` False when the call failed, maybe partway: the folder is synced
    again instead."""
    try:
        with sessions() as db:
            _Write(db, user_id, set()).marked_read(
                (account_id, folder), uid_validity, uid_next, done=done
            )
            db.commit()
    except Exception as exc:
        _failed(user_id, exc)


def resync(
    sessions: sessionmaker[Session], user_id: int, account_id: int, paths: Iterable[str]
) -> None:
    """Queue the syncs of ``account_id``'s folders ``paths``, which a write changed without its
    answer saying how, and announce them; a Gmail account's labelled folders for one without a
    row. Never raises: it runs after the write went through, a send included."""
    folders = {(account_id, path) for path in paths}
    if not folders:
        return
    try:
        with sessions() as db:
            resync_in(db, user_id, folders)
    except Exception as exc:
        _failed(user_id, exc)


def resync_in(db: Session, user_id: int, folders: Iterable[tuple[int, str]]) -> None:
    """``resync`` of ``folders``, each an account's id and a path, in ``db``, which must hold no
    open work: committed, or rolled back on a failure. Never raises either."""
    keys = set(folders)
    if not keys:
        return
    try:
        _Write(db, user_id, set(keys)).resynced(keys)
        db.commit()
    except Exception as exc:
        # A rollback that fails too leaves nothing to undo.
        with suppress(Exception):
            db.rollback()
        _failed(user_id, exc)


def labelled_folders(
    db: Session, user_id: int, accounts: Iterable[int]
) -> Sequence[MailIndexFolder]:
    """Of ``accounts``, the folders on the index whose rows carry Gmail's message ids: All Mail's,
    which a label's folder, without a row of its own, is read from."""
    labelled = (
        select(MailIndexFolder)
        .where(
            MailIndexFolder.user_id == user_id,
            MailIndexFolder.account_id.in_(sorted(accounts)),
            MailIndexFolder.enabled,
            exists().where(
                MailIndexMessage.folder_id == MailIndexFolder.id,
                MailIndexMessage.gm_msgid.is_not(None),
            ),
        )
        .order_by(MailIndexFolder.id)
    )
    return db.scalars(labelled).all()


def _failed(user_id: int, exc: Exception) -> None:
    # Its text may quote anything, mail included: the log names its type only.
    logger.warning(
        "mail_index_write_failed",
        extra={"event": "mail_index_write_failed", "user_id": user_id, "error": type(exc).__name__},
    )


def _validities(refs: Sequence[str]) -> dict[_Key, int]:
    found: dict[_Key, int] = {}
    for text_ref in refs:
        try:
            ref = core.parse_ref(text_ref)
        except MailError:
            continue
        found[(ref.account, ref.folder)] = ref.uid_validity
    return found


class _Write:
    """One request's index write, in ``db``'s transaction: the folders' rows locked, the changes
    made, then the counts, the syncs queued and the notifications."""

    def __init__(self, db: Session, user_id: int, unsure: set[_Key]) -> None:
        self.db = db
        self.user_id = user_id
        self.unsure = unsure
        self.keys: set[_Key] = set()
        self.rows: dict[_Key, MailIndexFolder] = {}
        self.changed: set[int] = set()

    def organized(
        self,
        items: Sequence[AuditItem],
        groups: Sequence[GroupResult],
        validities: Mapping[_Key, int] | None,
    ) -> None:
        moved: dict[_Key, list[int]] = defaultdict(list)
        flagged: dict[_Key, list[AuditItem]] = defaultdict(list)
        for item in items:
            if item.folder is None or item.uid is None:
                continue
            key = (item.account, item.folder)
            destination = (
                None
                if item.destination is None
                else (
                    item.account if item.destination_account is None else item.destination_account,
                    item.destination,
                )
            )
            if item.result != "ok" or item.copied:
                self.unsure.update(k for k in (key, destination) if k is not None)
            elif item.source_account is not None:
                # The copy a move from another account made: its row is the sync's to read.
                self.unsure.add(key)
            elif destination is not None:
                moved[key].append(item.uid)
                if item.destination_account is not None:
                    self.unsure.add(destination)
            else:
                flagged[key].append(item)
        landed: list[tuple[_Key, _Key, list[tuple[int, int]], int | None]] = []
        for group in groups:
            source = (group.account, group.folder)
            if group.destination is None or group.destination_account is not None:
                if not group.ok:
                    self.unsure.add(source)
                continue
            destination = (group.account, group.destination)
            new = [core.parse_ref(ref) for ref in group.new_refs or ()]
            uids = sorted(moved.get(source, ()))
            if not group.ok:
                self.unsure.update((source, destination))
                continue
            if group.new_refs is None or len(new) != len(uids):
                self.unsure.add(destination)
                continue
            pairs = list(zip(uids, (ref.uid for ref in new), strict=True))
            landed.append((source, destination, pairs, new[0].uid_validity if new else None))

        touched = {*moved, *flagged, *(destination for _, destination, _, _ in landed)}
        if validities is None:
            self.unsure.update(touched)
        self._lock(touched | self.unsure)

        def held(key: _Key) -> MailIndexFolder | None:
            """The folder's row when it is indexed and holds the refs' UIDVALIDITY."""
            row = self._indexed(key)
            if row is None:
                return None
            expected = row.uid_validity if validities is None else validities.get(key)
            if row.uid_validity is None or row.uid_validity != expected:
                self.unsure.add(key)
                return None
            return row

        # Copies first: they are made from the rows the moves then delete.
        for source, destination, pairs, validity in landed:
            target = self._indexed(destination)
            if target is None or not pairs:
                continue
            origin = held(source)
            if origin is None or target.uid_validity is None or target.uid_validity != validity:
                self.unsure.add(destination)
                continue
            self._copy(origin, target, pairs)
        for key, uids in moved.items():
            row = held(key)
            if row is not None:
                self._delete(row, uids)
        for key, noted in flagged.items():
            row = held(key)
            if row is not None:
                self._flag(row, noted)
        self._settle()

    def marked_read(self, key: _Key, uid_validity: int, uid_next: int, *, done: bool) -> None:
        self._lock({key})
        row = self._indexed(key)
        if not done or row is None or row.uid_validity != uid_validity:
            self.unsure.add(key)
        else:
            self.db.execute(
                update(MailIndexMessage)
                .where(
                    MailIndexMessage.folder_id == row.id,
                    MailIndexMessage.user_id == self.user_id,
                    MailIndexMessage.uid < uid_next,
                    not_(literal(SEEN) == any_(MailIndexMessage.flags)),
                )
                .values(flags=func.array_append(MailIndexMessage.flags, SEEN))
            )
            self.changed.add(row.id)
        self._settle()

    def resynced(self, keys: set[_Key]) -> None:
        self._lock(keys)
        self._settle()

    def _lock(self, keys: set[_Key]) -> None:
        """The user's rows of ``keys``' folders, locked against a switch or a sync meanwhile."""
        self.keys = keys
        if not keys:
            return
        query = (
            select(MailIndexFolder)
            .where(
                MailIndexFolder.user_id == self.user_id,
                tuple_(MailIndexFolder.account_id, MailIndexFolder.path).in_(sorted(keys)),
            )
            .order_by(MailIndexFolder.id)
            .with_for_update()
        )
        self.rows = {(row.account_id, row.path): row for row in self.db.scalars(query)}

    def _indexed(self, key: _Key) -> MailIndexFolder | None:
        row = self.rows.get(key)
        return row if row is not None and row.enabled else None

    def _messages(self, row: MailIndexFolder, uids: Iterable[int]) -> list[ColumnElement[bool]]:
        listed = bindparam("uids", sorted(set(uids)), type_=ARRAY(BigInteger))
        return [
            MailIndexMessage.folder_id == row.id,
            MailIndexMessage.user_id == self.user_id,
            MailIndexMessage.uid == any_(listed),
        ]

    def _copy(
        self, origin: MailIndexFolder, target: MailIndexFolder, pairs: Sequence[tuple[int, int]]
    ) -> None:
        """Rows in ``target`` for the messages moved from ``origin``, each under its new UID;
        those older than the target's age limit are left to its sync, as are those ``origin``
        had no row of."""
        new_uid = dict(pairs)
        found = self.db.scalars(select(MailIndexMessage).where(*self._messages(origin, new_uid)))
        columns = [column.key for column in MailIndexMessage.__table__.columns]
        copies: list[dict[str, Any]] = []
        for message in found:
            if target.oldest_indexed is None or message.received.date() >= target.oldest_indexed:
                values = {name: getattr(message, name) for name in columns}
                copies.append(values | {"folder_id": target.id, "uid": new_uid[message.uid]})
        if len(copies) < len(pairs):
            self.unsure.add((target.account_id, target.path))
        if copies:
            upsert_rows(self.db, copies)
            self.changed.add(target.id)

    def _delete(self, row: MailIndexFolder, uids: Sequence[int]) -> None:
        self.db.execute(delete(MailIndexMessage).where(*self._messages(row, uids)))
        self.changed.add(row.id)

    def _flag(self, row: MailIndexFolder, noted: Sequence[AuditItem]) -> None:
        """The flags each item asked for (``seen``, ``flagged``, ``to_sort``) on its row."""
        asked = {item.uid: item for item in noted if item.uid is not None}
        held = self.db.execute(
            select(MailIndexMessage.uid, MailIndexMessage.flags, MailIndexMessage.keywords).where(
                *self._messages(row, asked)
            )
        )
        flags: dict[int, frozenset[str]] = {}
        for uid, system, keywords in held:
            item = asked[uid]
            now = {*system, *keywords}
            asks = ((SEEN, item.seen), (_FLAGGED, item.flagged), (TO_SORT, item.to_sort))
            for flag, wanted in asks:
                if wanted is True:
                    now.add(flag)
                elif wanted is False:
                    now.discard(flag)
            flags[uid] = frozenset(now)
        if apply_flags(self.db, self.user_id, row.id, flags):
            self.changed.add(row.id)

    def _settle(self) -> None:
        """Count the folders changed, queue the syncs of the unsure ones (a Gmail account's
        labelled folders for a folder without a row) and of the changed ones a sync is running
        on (it may have read their flags before this write, and store them over it), and announce
        both."""
        # Imported here: the worker's tasks import the index's sync, which this package holds.
        from mailroom.worker.tasks import folder_syncs_running, queue_folder_sync

        rows = {row.id: row for row in self.rows.values()}
        for folder_id in sorted(self.changed):
            count_rows(self.db, rows[folder_id])
        queued = {
            row.id: row
            for row in (self.rows.get(key) for key in self.unsure)
            if row is not None and row.enabled
        }
        rowless = {account for account, path in self.keys if (account, path) not in self.rows}
        if rowless:
            labelled = labelled_folders(self.db, self.user_id, rowless)
            queued.update((row.id, row) for row in labelled)
        for folder_id in folder_syncs_running(self.db, sorted(self.changed - set(queued))):
            queued[folder_id] = rows[folder_id]
        for row in queued.values():
            queue_folder_sync(self.db, self.user_id, row.account_id, row.id)
        for folder_id in sorted(self.changed | set(queued)):
            row = rows.get(folder_id) or queued[folder_id]
            notify(self.db, self.user_id, row.account_id, folder_id)
