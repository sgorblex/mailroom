"""The organize routes, which change folders and flags of batches of refs: move (to another of
the user's accounts too), archive, trash and flag."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from mailroom.api.deps import Context, CurrentUser
from mailroom.api.routes.mail._shared import BatchRef, mail_router
from mailroom.errors import mail_refusal
from mailroom.mail import service

if TYPE_CHECKING:
    from mailroom_core.mail import FlagState, GroupResult, OrganizeResult


class RefsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refs: Annotated[list[BatchRef], Field(min_length=1, max_length=service.MAX_REFS)]


class MoveRequest(RefsRequest):
    folder: Annotated[str, Field(min_length=1, max_length=service.MAX_FOLDER)]


class MoveToAccountRequest(MoveRequest):
    account: Annotated[str, Field(min_length=1, max_length=64)]
    """Another of the user's accounts than any ref's."""
    removal: Literal["trash", "delete"]
    """What becomes of the originals once copied: to their account's Trash, or deleted for
    good."""


class FlagRequest(RefsRequest):
    seen: bool | None = None
    flagged: bool | None = None


class MessageFlags(BaseModel):
    ref: str
    seen: bool | None
    flagged: bool | None

    @classmethod
    def of(cls, state: FlagState) -> MessageFlags:
        return cls(ref=state.ref, seen=state.seen, flagged=state.flagged)


class GroupError(BaseModel):
    code: str
    message: str


class GroupOutcome(BaseModel):
    account: str
    folder: str
    ok: bool
    count: int
    new_refs: list[str] | None
    destination_account: str | None
    """On a move to another account, that account: ``new_refs`` are its."""
    junk: Literal["spam", "ham"] | None
    flags: list[MessageFlags]
    error: GroupError | None

    @classmethod
    def of(cls, group: GroupResult) -> GroupOutcome:
        error = group.error
        return cls(
            account=group.account,
            folder=group.folder,
            ok=group.ok,
            count=group.count,
            new_refs=None if group.new_refs is None else list(group.new_refs),
            destination_account=group.destination_account,
            junk=group.junk,
            flags=[MessageFlags.of(state) for state in group.flags],
            error=None if error is None else GroupError(code=error.code, message=error.message),
        )


class OrganizeAnswer(BaseModel):
    results: list[GroupOutcome]


def organize_answer(result: OrganizeResult, *, copies_answer: bool = False) -> OrganizeAnswer:
    """The groups' results, when one succeeded at least; else the first failure's refusal.
    ``copies_answer``: a failed group that made copies (``new_refs``) is answered too, so the
    client learns its messages are now in both places."""
    copied = copies_answer and any(group.new_refs for group in result.groups)
    if result.succeeded == 0 and not copied:
        error = next(group.error for group in result.groups if group.error is not None)
        raise mail_refusal(error.code, error.message)
    return OrganizeAnswer(results=[GroupOutcome.of(group) for group in result.groups])


@mail_router.post("/messages/move")
def move(request: MoveRequest, ctx: Context, user: CurrentUser) -> OrganizeAnswer:
    return organize_answer(service.move(ctx, user.id, request.refs, request.folder))


@mail_router.post("/messages/move-to-account")
def move_to_account(
    request: MoveToAccountRequest, ctx: Context, user: CurrentUser
) -> OrganizeAnswer:
    return organize_answer(
        service.move_to_account(
            ctx, user.id, request.refs, request.account, request.folder, request.removal
        ),
        copies_answer=True,
    )


@mail_router.post("/messages/archive")
def archive(request: RefsRequest, ctx: Context, user: CurrentUser) -> OrganizeAnswer:
    return organize_answer(service.archive(ctx, user.id, request.refs))


@mail_router.post("/messages/trash")
def trash(request: RefsRequest, ctx: Context, user: CurrentUser) -> OrganizeAnswer:
    return organize_answer(service.trash(ctx, user.id, request.refs))


@mail_router.post("/messages/flag")
def flag(request: FlagRequest, ctx: Context, user: CurrentUser) -> OrganizeAnswer:
    return organize_answer(
        service.flag(ctx, user.id, request.refs, seen=request.seen, flagged=request.flagged)
    )
