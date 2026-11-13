"""An account's folders: the listing, which changes nothing, and creating, renaming and deleting
folders."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from mailroom.api.deps import Context, CurrentUser
from mailroom.api.routes.mail._shared import AccountId, mail_router
from mailroom.mail import service

if TYPE_CHECKING:
    from mailroom_core.mail import Folder


class MailFolder(BaseModel):
    path: str
    role: str | None
    unread: int
    total: int
    delimiter: str | None
    """The account's hierarchy delimiter, None on a server without one."""
    selectable: bool
    """False for a ``\\Noselect`` folder (listed with ``all`` only), counts 0."""

    @classmethod
    def of(cls, folder: Folder) -> MailFolder:
        return cls(
            path=folder.path,
            role=folder.role,
            unread=folder.unseen or 0,
            total=folder.messages or 0,
            delimiter=folder.delimiter,
            selectable=folder.selectable,
        )


class FolderList(BaseModel):
    account_id: str
    folders: list[MailFolder]


@mail_router.get("/accounts/{account_id}/folders")
def folders(
    account_id: AccountId,
    ctx: Context,
    user: CurrentUser,
    all_: Annotated[bool, Query(alias="all")] = False,
) -> FolderList:
    """The selectable folders; ``all``: the ``\\Noselect`` ones too."""
    listed = service.folders(ctx, user.id, account_id, every=all_)
    return FolderList(account_id=account_id, folders=[MailFolder.of(f) for f in listed])


FolderPath = Annotated[str, Field(min_length=1, max_length=service.MAX_FOLDER)]
FolderName = Annotated[str, Field(min_length=1, max_length=service.MAX_FOLDER_NAME)]


class FolderCreateRequest(BaseModel):
    """Paths travel in the body: they hold the delimiter, ``/`` often."""

    model_config = ConfigDict(extra="forbid")

    parent: FolderPath | None
    """Required, null for the top level."""
    name: FolderName


class FolderRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder: FolderPath
    parent: FolderPath | None
    """Required, null for the top level: a rename that keeps its place names its parent."""
    name: FolderName


class FolderDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder: FolderPath
    messages: Annotated[StrictInt, Field(ge=0, le=service.MAX_MESSAGE_COUNT)]
    """The count the user saw (the listing's ``total``) and confirmed."""


class FolderCreated(BaseModel):
    account_id: str
    path: str


class FolderRenamedAnswer(BaseModel):
    account_id: str
    folder: str
    """The old path."""
    path: str
    rule_sets: list[int]
    filters: list[int]
    rule_sets_updated: bool


class FolderDeleted(BaseModel):
    account_id: str
    folder: str
    messages: int


@mail_router.post("/accounts/{account_id}/folders", status_code=201)
def create_folder(
    account_id: AccountId, request: FolderCreateRequest, ctx: Context, user: CurrentUser
) -> FolderCreated:
    change = service.create_folder(ctx, user.id, account_id, request.parent, request.name)
    return FolderCreated(account_id=account_id, path=change.path)


@mail_router.post("/accounts/{account_id}/folders/rename")
def rename_folder(
    account_id: AccountId, request: FolderRenameRequest, ctx: Context, user: CurrentUser
) -> FolderRenamedAnswer:
    renamed = service.rename_folder(
        ctx, user.id, account_id, request.folder, request.parent, request.name
    )
    change = renamed.change
    return FolderRenamedAnswer(
        account_id=account_id,
        folder=change.path,
        path=change.destination or change.path,
        rule_sets=list(renamed.rule_sets),
        filters=list(renamed.filters),
        rule_sets_updated=renamed.rule_sets_updated,
    )


@mail_router.post("/accounts/{account_id}/folders/delete")
def delete_folder(
    account_id: AccountId, request: FolderDeleteRequest, ctx: Context, user: CurrentUser
) -> FolderDeleted:
    change = service.delete_folder(
        ctx, user.id, account_id, request.folder, messages=request.messages
    )
    return FolderDeleted(account_id=account_id, folder=change.path, messages=request.messages)
