"""Every message a search matches (*select all matching*): counting them, which changes nothing,
and flagging, moving or trashing them, a batch a call, with the count the user confirmed."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from mailroom.api.deps import Context, CurrentUser
from mailroom.api.routes.mail._shared import MatchRequest, mail_router
from mailroom.api.routes.mail.organize import GroupOutcome
from mailroom.api.routes.mail.read import SourceFailure
from mailroom.api.token_access import open_to_tokens
from mailroom.mail import service
from mailroom_core.mail import BulkAction, BulkFlag, BulkMove, BulkTrash


class FlagAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["flag"]
    seen: bool | None = None
    flagged: bool | None = None
    handled: bool | None = None


class MoveAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["move"]
    folder: Annotated[str, Field(min_length=1, max_length=service.MAX_FOLDER)]


class TrashAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["trash"]


Action = Annotated[FlagAction | MoveAction | TrashAction, Field(discriminator="kind")]


def _action(action: FlagAction | MoveAction | TrashAction) -> BulkAction:
    if isinstance(action, FlagAction):
        return BulkFlag(seen=action.seen, flagged=action.flagged, handled=action.handled)
    if isinstance(action, MoveAction):
        return BulkMove(action.folder)
    return BulkTrash()


class CountRequest(MatchRequest):
    action: Action | None = None
    """Count only the messages it would change: those a flag change has not set yet."""


class BulkRequest(MatchRequest):
    action: Action
    expected: Annotated[StrictInt, Field(ge=0, le=service.BULK_MAX)]
    """The count the user confirmed, of what remains: the first count's, then each answer's
    ``remaining``."""


class MatchCount(BaseModel):
    total: int
    failures: list[SourceFailure]


class BulkAnswer(BaseModel):
    done: int
    remaining: int
    """What the next call's ``expected`` is: 0 once every message is done."""
    results: list[GroupOutcome]
    failures: list[SourceFailure]


@mail_router.post("/messages/count")
@open_to_tokens("mail_organize")
def count(request: CountRequest, ctx: Context, user: CurrentUser) -> MatchCount:
    matching = service.count_matching(
        ctx,
        user.id,
        request.account,
        request.folder,
        request.query(),
        subfolders=request.subfolders,
        action=None if request.action is None else _action(request.action),
        via=ctx.origin.via,
    )
    return MatchCount(
        total=matching.total,
        failures=[SourceFailure.of(failure) for failure in matching.failures],
    )


@mail_router.post("/messages/bulk")
@open_to_tokens("mail_organize")
def bulk(request: BulkRequest, ctx: Context, user: CurrentUser) -> BulkAnswer:
    result = service.act_on_matching(
        ctx,
        user.id,
        request.account,
        request.folder,
        request.query(),
        _action(request.action),
        expected=request.expected,
        subfolders=request.subfolders,
        origin=ctx.origin,
    )
    return BulkAnswer(
        done=result.done,
        remaining=result.remaining,
        results=[GroupOutcome.of(group) for group in result.groups],
        failures=[SourceFailure.of(failure) for failure in result.failures],
    )
