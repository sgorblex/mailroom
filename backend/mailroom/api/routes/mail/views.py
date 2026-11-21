"""The user's saved views and the built-in *All inboxes*: listing, creating, changing, deleting
and ordering them, and a view's messages, read from the mail index only."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from fastapi import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mailroom.api.deps import Context, CurrentUser, DbSession
from mailroom.api.routes.mail._shared import mail_router
from mailroom.api.routes.mail.folders import FolderPath
from mailroom.api.routes.mail.read import PAGE_SIZE, Cursor, Group, Limit, MessagePage
from mailroom.mail import saved_views, service
from mailroom.models import SavedView
from mailroom_core.config import MAX_SAFE_INTEGER

ViewId = Annotated[int, Path(ge=1, le=MAX_SAFE_INTEGER)]
ViewName = Annotated[str, Field(min_length=1, max_length=saved_views.MAX_NAME)]
ViewQuery = Annotated[str, Field(min_length=1, max_length=service.MAX_TERM)]


class ViewSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
    folder: FolderPath


ViewSources = Annotated[list[ViewSource], Field(min_length=1, max_length=saved_views.MAX_SOURCES)]


class ViewOut(BaseModel):
    id: int
    name: str
    sources: list[ViewSource] | None
    query: str | None
    position: int

    @classmethod
    def of(cls, view: SavedView) -> ViewOut:
        sources = (
            None
            if view.sources is None
            else [ViewSource(account_id=account, folder=path) for account, path in view.sources]
        )
        return cls(
            id=view.id, name=view.name, sources=sources, query=view.query, position=view.position
        )


class Views(BaseModel):
    views: list[ViewOut]


def _pairs(sources: list[ViewSource] | None) -> list[tuple[int, str]] | None:
    return None if sources is None else [(s.account_id, s.folder) for s in sources]


class ViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ViewName
    sources: ViewSources | None = None
    query: ViewQuery | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        if (self.sources is None) == (self.query is None):
            msg = "Give the view sources or a query, not both."
            raise ValueError(msg)
        return self


class ViewChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ViewName | None = None
    sources: ViewSources | None = None
    query: ViewQuery | None = None

    @model_validator(mode="after")
    def _something(self) -> Self:
        if self.sources is not None and self.query is not None:
            msg = "Give the view sources or a query, not both."
            raise ValueError(msg)
        if self.name is None and self.sources is None and self.query is None:
            msg = "Change the name, the sources or the query."
            raise ValueError(msg)
        return self


class ViewOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view_ids: list[int] = Field(max_length=saved_views.MAX_VIEWS)
    """Every one of the user's views, each once, in the new order."""


class NotIndexed(BaseModel):
    account_id: int
    folder: str
    """``*``: the account's every folder (a search's ``in:*``)."""


class ViewPage(MessagePage):
    not_indexed: list[NotIndexed]
    """The view's folders left out, off the index or not ready yet; on the first page only."""


@mail_router.get("/views")
def list_views(db: DbSession, user: CurrentUser) -> Views:
    """The user's saved views, in their order; *All inboxes* is built in and not listed."""
    return Views(views=[ViewOut.of(view) for view in saved_views.views_of(db, user.id)])


@mail_router.post("/views", status_code=201)
def create_view(request: ViewCreate, ctx: Context, db: DbSession, user: CurrentUser) -> ViewOut:
    view = saved_views.create_view(
        db,
        user.id,
        service.account_ids(ctx, user.id),
        request.name,
        sources=_pairs(request.sources),
        query=request.query,
    )
    return ViewOut.of(view)


@mail_router.put("/views/order")
def reorder_views(request: ViewOrder, db: DbSession, user: CurrentUser) -> Views:
    views = saved_views.reorder_views(db, user.id, request.view_ids)
    return Views(views=[ViewOut.of(view) for view in views])


@mail_router.patch("/views/{view_id}")
def change_view(
    view_id: ViewId, request: ViewChange, ctx: Context, db: DbSession, user: CurrentUser
) -> ViewOut:
    view = saved_views.change_view(
        db,
        user.id,
        service.account_ids(ctx, user.id),
        view_id,
        name=request.name,
        sources=_pairs(request.sources),
        query=request.query,
    )
    return ViewOut.of(view)


@mail_router.delete("/views/{view_id}", status_code=204)
def delete_view(view_id: ViewId, db: DbSession, user: CurrentUser) -> None:
    saved_views.remove_view(db, user.id, view_id)


@mail_router.get("/views/{view_id}/messages")
def view_messages(
    view_id: Annotated[
        Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)] | Literal["all_inboxes"],
        Path(description="A saved view's id, or all_inboxes: INBOX of every account."),
    ],
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
    limit: Limit = PAGE_SIZE,
    cursor: Cursor = None,
    group: Group = None,
) -> ViewPage:
    """The view's messages, newest first, from the mail index only."""
    view = saved_views.owned_view(db, user.id, view_id) if isinstance(view_id, int) else None
    sources, query = saved_views.reading(ctx, user.id, view)
    read = service.view_messages(
        ctx, user.id, sources, query, limit=limit, cursor=cursor, group=group == "thread"
    )
    page = MessagePage.of(read.page)
    return ViewPage(
        messages=page.messages,
        next_cursor=page.next_cursor,
        failures=page.failures,
        conversations=page.conversations,
        not_indexed=[NotIndexed(account_id=s.account, folder=s.folder) for s in read.not_indexed],
    )
