"""The Home page's summary of the signed-in user's mail and filters (``mailroom.home``). Mounted on
every instance: the parts of features it does not run are null."""

from __future__ import annotations

from fastapi import APIRouter

from mailroom.api.deps import Context, CurrentUser, DbSession
from mailroom.home import HomeSummary, home_summary

home_router = APIRouter(prefix="/home")


@home_router.get("")
def home(ctx: Context, db: DbSession, user: CurrentUser) -> HomeSummary:
    return home_summary(ctx, db, user.id)
