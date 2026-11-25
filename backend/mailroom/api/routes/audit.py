"""The signed-in user's audit trail (``mailroom.audit.listing``), for the *Activity* page.
Mounted where the instance writes mail: the mail layer or filters."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from mailroom.api.deps import Context, CurrentUser, DbSession
from mailroom.api.token_access import open_to_tokens
from mailroom.audit import listing
from mailroom.audit.listing import CLIENT_PATTERN, MAX_PAGE_SIZE, PAGE_SIZE, RecordPage
from mailroom_core.config import MAX_SAFE_INTEGER

audit_router = APIRouter(prefix="/me/audit")

Text = Annotated[str | None, Query(min_length=1, max_length=200)]
Short = Annotated[str | None, Query(min_length=1, max_length=64)]
Id = Annotated[int | None, Query(ge=1, le=MAX_SAFE_INTEGER)]
Client = Annotated[str | None, Query(min_length=1, max_length=2048, pattern=CLIENT_PATTERN)]


@audit_router.get("")
@open_to_tokens("audit_read")
def audit_records(
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
    response: Response,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE_SIZE,
    account_id: Id = None,
    action: Text = None,
    actor: Literal["user", "agent", "filter"] | None = None,
    result: Short = None,
    client_id: Client = None,
    grant_id: Client = None,
    token_id: Id = None,
) -> RecordPage:
    # Addresses of the user's correspondents: kept out of shared caches, as the mail routes are.
    response.headers["Cache-Control"] = "private, no-store"
    return listing.records(
        db,
        user.id,
        reach=ctx.reach,
        cursor=cursor,
        limit=limit,
        account=account_id,
        action=action,
        actor=actor,
        result=result,
        client_id=client_id,
        grant_id=grant_id,
        token_id=token_id,
    )
