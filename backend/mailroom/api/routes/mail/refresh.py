"""Refreshing what a list shows: its indexed folders synced ahead of the worker's other jobs, and
whether that is done. Not open to API tokens."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from mailroom.api.deps import Context, CurrentUser, DbSession
from mailroom.api.routes.mail._shared import mail_router
from mailroom.api.routes.mail.folders import FolderPath
from mailroom.mail import saved_views, service
from mailroom_core.config import MAX_SAFE_INTEGER

if TYPE_CHECKING:
    from mailroom.auth.throttle import RateLimit

_Id = Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]


class RefreshList(BaseModel):
    """A folder's list, or a search's scope (as ``POST /search`` takes it)."""

    model_config = ConfigDict(extra="forbid")

    account: _Id | service.AllAccounts
    folder: FolderPath = "INBOX"
    subfolders: bool = False


class RefreshView(BaseModel):
    """A view's messages: a saved view's id, or ``all_inboxes`` (with ``subfolders``, INBOX's
    subfolders too)."""

    model_config = ConfigDict(extra="forbid")

    view: _Id | Literal["all_inboxes"]
    subfolders: bool = False


class RefreshAsked(BaseModel):
    refresh_id: UUID | None
    """None when nothing the list reads is on the index: reading it again is its refresh."""


class RefreshState(BaseModel):
    running: bool


@mail_router.post("/refresh", status_code=202)
def refresh(
    body: RefreshList | RefreshView,
    request: Request,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
) -> RefreshAsked:
    """Sync the indexed folders the list or the view reads, ahead of every other job."""
    limit: RateLimit = request.app.state.refresh_limit
    service.admit_refresh(limit, user.id)
    if isinstance(body, RefreshView):
        view = saved_views.shown_view(db, user.id, body.view, subfolders=body.subfolders)
        sources, _ = saved_views.reading(ctx, db, user.id, view, subfolders=body.subfolders)
    else:
        sources = service.listed_sources(
            ctx, user.id, body.account, body.folder, subfolders=body.subfolders
        )
    refresh_id = service.refresh(ctx, db, user.id, sources)
    return RefreshAsked(refresh_id=None if refresh_id is None else UUID(refresh_id))


@mail_router.get("/refresh/{refresh_id}")
def refresh_state(refresh_id: UUID, db: DbSession, user: CurrentUser) -> RefreshState:
    """Whether the user's refresh still has folders to sync."""
    return RefreshState(running=service.refresh_running(db, user.id, str(refresh_id)))
