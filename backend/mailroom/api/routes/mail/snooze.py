"""Snooze: a message moved to its account's Snoozed folder until a time, when the worker moves
it back unread (``mailroom.mail.service.snooze``); the user's snoozed messages, and bringing one
back now."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import Path
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from mailroom.api.deps import Context, CurrentUser
from mailroom.api.routes.mail._shared import Ref, mail_router
from mailroom.api.token_access import open_to_tokens
from mailroom.mail import service

if TYPE_CHECKING:
    from mailroom.mail.service import SnoozeView

SnoozeId = Annotated[str, Path(max_length=64)]


class SnoozeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    until: AwareDatetime
    source_folder: Annotated[str, Field(min_length=1, max_length=service.MAX_FOLDER)] | None = None
    """The folder the user acted from (INBOX or a label's), as a move's."""


class SnoozeAnswer(BaseModel):
    snooze_id: str
    account: int
    subject: str
    sender: str | None
    folder: str
    until: datetime
    code: str | None

    @classmethod
    def of(cls, view: SnoozeView) -> SnoozeAnswer:
        return cls(
            snooze_id=view.snooze_id,
            account=view.account,
            subject=view.subject,
            sender=view.sender,
            folder=view.folder,
            until=view.until,
            code=view.code,
        )


class SnoozeList(BaseModel):
    snoozes: list[SnoozeAnswer]


@mail_router.post("/messages/{ref}/snooze", status_code=201)
@open_to_tokens("mail_organize")
def snooze_message(
    ref: Ref, request: SnoozeRequest, ctx: Context, user: CurrentUser
) -> SnoozeAnswer:
    return SnoozeAnswer.of(
        service.snooze(
            ctx,
            user.id,
            ref,
            request.until,
            source_folder=request.source_folder,
            origin=ctx.origin,
        )
    )


@mail_router.get("/snoozes")
@open_to_tokens("mail_organize")
def list_snoozes(ctx: Context, user: CurrentUser) -> SnoozeList:
    """The user's snoozed messages, soonest back first."""
    listed = service.list_snoozes(ctx, user.id)
    return SnoozeList(snoozes=[SnoozeAnswer.of(view) for view in listed])


@mail_router.post("/snoozes/{snooze_id}/wake")
@open_to_tokens("mail_organize")
def wake_snooze(snooze_id: SnoozeId, ctx: Context, user: CurrentUser) -> SnoozeAnswer:
    """Bring the snoozed message back now: the worker moves it, in a moment."""
    return SnoozeAnswer.of(service.wake_now(ctx, user.id, snooze_id, origin=ctx.origin))
