"""The mail index's jobs, carried out on the user's connections from the worker's pool: an
account's folders brought in step with its listing and its due folders picked, and one job of a
folder's sync (``mailroom.mail.index``). ``mailroom.worker.tasks`` queues them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from mailroom.accounts.store import AccountNotFound, load_accounts, user_store
from mailroom.mail.connections import PASSWORD_MISSING, PasswordMissing, unusable
from mailroom.mail.index import discover_folders, due_folders, fail_folder, sync_folder
from mailroom.models import User
from mailroom_core.mail import MailError

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    from mailroom.worker.context import Mailboxes

logger = logging.getLogger(__name__)


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
    db: Session, mailboxes: Mailboxes, user_id: int, account_id: int, folder_id: int
) -> bool:
    """One job of the folder's sync (``sync_folder``); whether another job has more to do. An
    account that cannot log in fails the folder with the app's code; one gone is left alone."""
    store = user_store(mailboxes.engine, user_id)
    try:
        refused = unusable(store, mailboxes.subkey, account_id)
    except AccountNotFound:
        return False
    if refused is not None:
        fail_folder(db, user_id, account_id, folder_id, refused[0])
        return False
    with mailboxes.lease(user_id, load_accounts(store).revision) as connections:
        return sync_folder(db, connections, user_id, account_id, folder_id)
