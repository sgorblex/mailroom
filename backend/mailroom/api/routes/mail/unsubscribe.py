"""One-click unsubscribing from the list a message came from (RFC 8058), for the signed-in user
only: no MCP tool reaches it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from mailroom.api.deps import Context, CurrentUser
from mailroom.api.routes.mail._shared import Ref, mail_router
from mailroom.mail import unsubscribe as one_click


class UnsubscribeAnswer(BaseModel):
    outcome: Literal["ok", "rejected", "refused", "failed"]
    status: int | None
    list_id: str | None


@mail_router.post("/messages/{ref}/unsubscribe")
async def unsubscribe(ref: Ref, ctx: Context, user: CurrentUser) -> UnsubscribeAnswer:
    done = await one_click.unsubscribe(ctx, user.id, ref)
    return UnsubscribeAnswer(outcome=done.outcome, status=done.status, list_id=done.list_id)
