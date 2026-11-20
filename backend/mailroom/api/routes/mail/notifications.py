"""The folders whose new mail the user's browser shows as desktop notifications, per account."""

from __future__ import annotations

import logging
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from mailroom.accounts.service import account_not_found
from mailroom.api.deps import AccountId, Context, CurrentUser, DbSession
from mailroom.api.routes.mail._shared import mail_router
from mailroom.api.routes.mail.folders import FolderPath
from mailroom.mail import notifications, service

logger = logging.getLogger(__name__)


class AccountNotifications(BaseModel):
    account_id: int
    folders: list[str]


class Notifications(BaseModel):
    accounts: list[AccountNotifications]


class NotificationFoldersChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folders: Annotated[list[FolderPath], Field(max_length=notifications.MAX_FOLDERS)]


@mail_router.get("/notifications")
def notification_folders(ctx: Context, db: DbSession, user: CurrentUser) -> Notifications:
    """Each of the user's accounts with the folders whose new mail is notified, INBOX alone
    until the user chooses."""
    chosen = notifications.folders_by_account(db, user.id, service.account_ids(ctx, user.id))
    return Notifications(
        accounts=[
            AccountNotifications(account_id=account_id, folders=folders)
            for account_id, folders in chosen.items()
        ]
    )


@mail_router.put("/notifications/accounts/{account_id}")
def change_notification_folders(
    account_id: AccountId,
    request: NotificationFoldersChange,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
) -> AccountNotifications:
    """The account's folders whose new mail is notified, all of them at once (none: no
    notification from it)."""
    if account_id not in service.account_ids(ctx, user.id):
        raise account_not_found(coded=True)
    folders = notifications.set_folders(db, user.id, account_id, request.folders)
    logger.info(
        "notification_folders_changed",
        extra={
            "event": "notification_folders_changed",
            "user_id": user.id,
            "account_id": account_id,
            "folders": len(folders),
        },
    )
    return AccountNotifications(account_id=account_id, folders=folders)
