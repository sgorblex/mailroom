"""The senders whose remote content the user shows without asking: listed, added from a message
the server reads and checks itself, and removed. Addresses travel in bodies, never in a URL."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from mailroom.api.deps import Context, CurrentUser, DbSession
from mailroom.api.routes.mail._shared import BatchRef, mail_router
from mailroom.errors import Refusal
from mailroom.mail import remote_senders, service
from mailroom.models import RemoteContentSender

logger = logging.getLogger(__name__)


class RemoteSender(BaseModel):
    address: str
    created_at: datetime

    @classmethod
    def of(cls, row: RemoteContentSender) -> RemoteSender:
        return cls(address=row.address, created_at=row.created_at)


class RemoteSenders(BaseModel):
    senders: list[RemoteSender]


class RemoteSenderAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: BatchRef


class RemoteSenderRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: Annotated[str, Field(min_length=1, max_length=remote_senders.MAX_ADDRESS)]


def _log(event: str, user_id: int) -> None:
    logger.info(event, extra={"event": event, "user_id": user_id})


@mail_router.get("/remote-senders")
def list_remote_senders(db: DbSession, user: CurrentUser) -> RemoteSenders:
    return RemoteSenders(
        senders=[RemoteSender.of(row) for row in remote_senders.senders(db, user.id)]
    )


@mail_router.post("/remote-senders", status_code=201)
def add_remote_sender(
    request: RemoteSenderAdd, ctx: Context, db: DbSession, user: CurrentUser
) -> RemoteSender:
    """Remember the From of the message ``ref`` names, read here: only when DMARC passed for its
    one From at the account's own authentication server."""
    view = service.read_message(ctx, user.id, request.ref)
    address = remote_senders.verified_sender(view)
    if address is None:
        msg = "Only a sender the account's authentication server verified can be remembered."
        raise Refusal("invalid", "sender_not_verified", msg)
    row = remote_senders.remember(db, user.id, address)
    _log("remote_sender_added", user.id)
    return RemoteSender.of(row)


@mail_router.post("/remote-senders/remove", status_code=204)
def remove_remote_sender(request: RemoteSenderRemove, db: DbSession, user: CurrentUser) -> None:
    if not remote_senders.forget(db, user.id, request.address):
        msg = "No such sender."
        raise Refusal("not_found", "not_found", msg)
    _log("remote_sender_removed", user.id)
