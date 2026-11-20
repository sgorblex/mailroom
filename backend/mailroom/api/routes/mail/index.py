"""The mail index's settings: each account's folders as the index keeps them, and switching one on
or off. The age limit is the user's own, under ``/auth/me``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool

from mailroom.api.deps import AccountId, Context, CurrentUser, DbSession
from mailroom.api.routes.mail._shared import mail_router
from mailroom.api.routes.mail.folders import FolderPath
from mailroom.mail import index
from mailroom.mail.index import IndexFolderSwitched, IndexStatus


class IndexFolderChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder: FolderPath
    enabled: StrictBool


@mail_router.get("/index/accounts/{account_id}")
def index_status(
    account_id: AccountId,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
    totals: Literal["all", "waiting"] = "all",
) -> IndexStatus:
    """The account's selectable folders, each with whether and how it is indexed; with
    ``totals=waiting``, the server's count of the folders waiting for their sync only."""
    return index.index_status(
        ctx, db, user.id, account_id, user.index_max_age_days, waiting_only=totals == "waiting"
    )


@mail_router.put("/index/accounts/{account_id}/folders")
def change_index_folder(
    account_id: AccountId,
    request: IndexFolderChange,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
) -> IndexFolderSwitched:
    """Switch a folder on or off the index; off deletes what it kept of the folder."""
    return index.set_folder_enabled(ctx, db, user.id, account_id, request.folder, request.enabled)
