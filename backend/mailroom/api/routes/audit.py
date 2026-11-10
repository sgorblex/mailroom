"""The signed-in user's audit trail (``mailroom.audit.listing``), for the *Activity* page.
Mounted where the instance writes mail: the mail layer or filters."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from mailroom.api.deps import CurrentUser, DbSession
from mailroom.audit import listing
from mailroom.audit.listing import MAX_PAGE_SIZE, PAGE_SIZE, RecordPage

audit_router = APIRouter(prefix="/me/audit")

Text = Annotated[str | None, Query(min_length=1, max_length=200)]
Short = Annotated[str | None, Query(min_length=1, max_length=64)]


@audit_router.get("")
def audit_records(
    db: DbSession,
    user: CurrentUser,
    response: Response,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE_SIZE,
    account_id: Short = None,
    action: Text = None,
    actor: Literal["user", "agent", "filter"] | None = None,
    result: Short = None,
) -> RecordPage:
    # Addresses of the user's correspondents: kept out of shared caches, as the mail routes are.
    response.headers["Cache-Control"] = "private, no-store"
    return listing.records(
        db,
        user.id,
        cursor=cursor,
        limit=limit,
        account=account_id,
        action=action,
        actor=actor,
        result=result,
    )
