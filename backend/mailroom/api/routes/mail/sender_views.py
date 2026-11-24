"""The senders whose mail the user reads as text: listed, set from a message the server reads
itself, and removed. Addresses travel in bodies, never in a URL."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from mailroom.api.deps import Context, CurrentUser, DbSession
from mailroom.api.routes.mail._shared import BatchRef, mail_router
from mailroom.errors import Refusal
from mailroom.mail import remote_senders, sender_views, service
from mailroom.models import SenderViewPreference

logger = logging.getLogger(__name__)


class TextSender(BaseModel):
    address: str
    created_at: datetime

    @classmethod
    def of(cls, row: SenderViewPreference) -> TextSender:
        return cls(address=row.address, created_at=row.created_at)


class TextSenders(BaseModel):
    senders: list[TextSender]


class SenderViewChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: BatchRef
    view: sender_views.View


class SenderViewOut(BaseModel):
    address: str
    view: sender_views.View


class SenderViewRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: Annotated[str, Field(min_length=1, max_length=remote_senders.MAX_ADDRESS)]


def _log(event: str, user_id: int) -> None:
    logger.info(event, extra={"event": event, "user_id": user_id})


@mail_router.get("/sender-views")
def list_sender_views(db: DbSession, user: CurrentUser) -> TextSenders:
    return TextSenders(senders=[TextSender.of(row) for row in sender_views.senders(db, user.id)])


@mail_router.post("/sender-views")
def choose_sender_view(
    request: SenderViewChoice, ctx: Context, db: DbSession, user: CurrentUser
) -> SenderViewOut:
    """The view the From of the message ``ref`` names opens in, read here: ``text`` remembers
    it, ``html`` forgets it."""
    address = sender_views.sender(service.read_message(ctx, user.id, request.ref))
    if address is None:
        msg = "The message has no sender."
        raise Refusal("invalid", "no_sender", msg)
    if request.view == "text":
        sender_views.remember(db, user.id, address)
        _log("sender_view_text", user.id)
    elif sender_views.forget(db, user.id, address):
        _log("sender_view_html", user.id)
    return SenderViewOut(address=address, view=request.view)


@mail_router.post("/sender-views/remove", status_code=204)
def remove_sender_view(request: SenderViewRemove, db: DbSession, user: CurrentUser) -> None:
    if not sender_views.forget(db, user.id, request.address):
        msg = "No such sender."
        raise Refusal("not_found", "not_found", msg)
    _log("sender_view_html", user.id)
