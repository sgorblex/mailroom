"""The mail index's jobs, carried out on the user's connections from the worker's pool: an
account's folders brought in step with its listing and its due folders picked, one share of a
turn over those, and one job of a folder's sync (``mailroom.mail.index``).
``mailroom.worker.tasks`` queues them.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from mailroom.accounts.store import AccountNotFound, load_accounts, user_store
from mailroom.errors import SECRETS_KEY_MISSING
from mailroom.mail.connections import PASSWORD_MISSING, PasswordMissing, unusable
from mailroom.mail.index import discover_folders, due_folders, fail_folder, sync_folder
from mailroom.models import MailIndexFolder, User
from mailroom_core.mail import MailError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    from mailroom.worker.context import Mailboxes

logger = logging.getLogger(__name__)

_OUT_OF_REACH: Final = frozenset(
    {"unreachable", "timeout", "auth_failed", PASSWORD_MISSING, SECRETS_KEY_MISSING}
)
"""The errors of a folder's sync that every other folder of its account would meet too."""
SHARE_SECONDS: Final = 5.0
"""How long an index job holds its mailbox before it leaves the rest to a later job: it begins no
folder and no batch past it but its first."""


def index_accounts(db: Session, engine: Engine) -> list[tuple[int, int]]:
    """Every user's accounts, as (user id, account id)."""
    return [
        (user_id, account.id)
        for user_id in db.scalars(select(User.id).order_by(User.id))
        for account in load_accounts(user_store(engine, user_id)).settings.account
    ]


def account_folders_due(
    db: Session, mailboxes: Mailboxes, user_id: int, account_id: int, now: datetime
) -> list[int]:
    """Bring the account's index folders in step with its listing (``discover_folders``), then
    the ids of those due a sync. An account that cannot log in, or whose listing failed, keeps its
    folders as they were: their syncs record why. None (an empty list) for an account gone."""
    store = user_store(mailboxes.engine, user_id)
    try:
        refused = unusable(store, mailboxes.subkey, account_id)
    except AccountNotFound:
        return []
    if refused is None:
        try:
            with mailboxes.lease(user_id, load_accounts(store).revision) as connections:
                removed = discover_folders(db, connections, user_id, account_id)
        except (MailError, PasswordMissing) as exc:
            db.rollback()
            code = exc.code if isinstance(exc, MailError) else PASSWORD_MISSING
            logger.info(
                "mail_index_listing_failed",
                extra={
                    "event": "mail_index_listing_failed",
                    "user_id": user_id,
                    "account_id": account_id,
                    "error": code,
                },
            )
        else:
            if removed:
                logger.info(
                    "mail_index_folders_gone",
                    extra={
                        "event": "mail_index_folders_gone",
                        "user_id": user_id,
                        "account_id": account_id,
                        "removed": removed,
                    },
                )
    return due_folders(db, user_id, account_id, now)


def run_folder_sync(
    db: Session,
    mailboxes: Mailboxes,
    user_id: int,
    account_id: int,
    folder_id: int,
    *,
    until: float | None = None,
) -> bool:
    """One job of the folder's sync (``sync_folder``), until ``until`` (else for
    ``SHARE_SECONDS``); whether another job has more to do. An account that cannot log in fails
    the folder with the app's code; one gone is left alone."""
    if until is None:
        until = time.monotonic() + SHARE_SECONDS
    store = user_store(mailboxes.engine, user_id)
    try:
        refused = unusable(store, mailboxes.subkey, account_id)
    except AccountNotFound:
        return False
    if refused is not None:
        fail_folder(db, user_id, account_id, folder_id, refused[0])
        return False
    with mailboxes.lease(user_id, load_accounts(store).revision) as connections:
        return sync_folder(db, connections, user_id, account_id, folder_id, until=until)


@dataclass(frozen=True, slots=True)
class TurnShare:
    """What one share of a turn left to do."""

    unfinished: tuple[int, ...]
    """The folders it synced that have more to fetch."""
    rest: tuple[int, ...]
    """The turn's folders it did not get to."""


def run_turn_share(
    db: Session,
    mailboxes: Mailboxes,
    user_id: int,
    account_id: int,
    folder_ids: Sequence[int],
    *,
    refreshing: bool = False,
) -> TurnShare:
    """Sync the turn's folders ``folder_ids`` in order for ``SHARE_SECONDS``, the first of them
    whatever it takes. One no longer due (synced meanwhile, switched off, gone) is passed over; one
    whose sync breaks outside the mailbox is logged by the exception's type and holds up no other.
    ``refreshing``, it syncs them however lately they were, and ends at once, with no rest, when
    the account cannot be reached or logged in to (``_OUT_OF_REACH``): the turn's work is to fail
    each folder in its time. Each folder's sync commits ``db``."""
    until = time.monotonic() + SHARE_SECONDS
    unfinished: list[int] = []
    waiting = list(folder_ids)
    if not refreshing:
        due = set(due_folders(db, user_id, account_id, datetime.now(UTC)))
        db.commit()
        waiting = [folder_id for folder_id in waiting if folder_id in due]
    for done, folder_id in enumerate(waiting):
        if done and time.monotonic() >= until:
            return TurnShare(tuple(unfinished), tuple(waiting[done:]))
        try:
            more = run_folder_sync(db, mailboxes, user_id, account_id, folder_id, until=until)
        except Exception as exc:
            db.rollback()
            logger.error("A sync of index folder %s failed: %s.", folder_id, type(exc).__name__)
            continue
        if more:
            unfinished.append(folder_id)
        elif refreshing and _out_of_reach(db, user_id, folder_id):
            break
    return TurnShare(tuple(unfinished), ())


def _out_of_reach(db: Session, user_id: int, folder_id: int) -> bool:
    """Whether the folder's sync just failed on its account."""
    error = db.scalar(
        select(MailIndexFolder.error).where(
            MailIndexFolder.id == folder_id,
            MailIndexFolder.user_id == user_id,
            MailIndexFolder.enabled,
        )
    )
    db.commit()
    return error in _OUT_OF_REACH
