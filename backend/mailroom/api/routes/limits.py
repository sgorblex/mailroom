"""The signed-in user's limits on sending (``mailroom.accounts.limits``), read and changed from
the web. Mounted where the instance runs the mail layer (mail or MCP)."""

from __future__ import annotations

from fastapi import APIRouter

from mailroom.accounts import limits
from mailroom.accounts.limits import UserLimits
from mailroom.api.deps import Context, CurrentUser

limits_router = APIRouter(prefix="/me/limits")


@limits_router.get("")
def user_limits(ctx: Context, user: CurrentUser) -> UserLimits:
    return limits.user_limits(ctx, user.id)


@limits_router.put("")
def change_limits(body: UserLimits, ctx: Context, user: CurrentUser) -> UserLimits:
    return limits.change_limits(ctx, user.id, body)
