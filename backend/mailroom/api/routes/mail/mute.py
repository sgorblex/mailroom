"""Muted threads: muting the conversation a message belongs to, listing and unmuting them
(``mailroom.filters.mute``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Path
from pydantic import BaseModel

from mailroom.api.deps import Context, CurrentUser, DbSession
from mailroom.api.routes.mail._shared import Ref, mail_router
from mailroom.api.token_access import open_to_tokens
from mailroom.filters import mute
from mailroom.filters.mute import MutedThreadOut

MutedThreadId = Annotated[int, Path(ge=1, le=2**63 - 1)]


class MutedThreads(BaseModel):
    threads: list[MutedThreadOut]


@mail_router.post("/messages/{ref}/mute", status_code=201)
@open_to_tokens("mail_organize")
def mute_thread(ref: Ref, ctx: Context, db: DbSession, user: CurrentUser) -> MutedThreadOut:
    return mute.mute(ctx, db, user.id, ref, origin=ctx.origin)


@mail_router.get("/muted")
@open_to_tokens("mail_organize")
def muted_threads(ctx: Context, db: DbSession, user: CurrentUser) -> MutedThreads:
    return MutedThreads(threads=mute.muted_threads(ctx, db, user.id))


@mail_router.delete("/muted/{muted_thread_id}", status_code=204)
@open_to_tokens("mail_organize")
def unmute_thread(
    muted_thread_id: MutedThreadId, ctx: Context, db: DbSession, user: CurrentUser
) -> None:
    mute.unmute(ctx, db, user.id, muted_thread_id, origin=ctx.origin)
