"""The signed-in user's personal API tokens (``mailroom.auth.api_tokens``): made, listed and
revoked on the Profile. With the session cookie only, as every route is that a token could widen
itself through."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Path, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from mailroom.api.deps import Context, CurrentUser, DbSession
from mailroom.api.routes._app_access import (
    MAX_GROUPS,
    AccountChoice,
    GroupNameIn,
    Limits,
    ToolGroupOut,
    account_choices,
    approvable_groups,
    group_out,
    limits_reach,
)
from mailroom.auth.api_tokens import create_token, revoke_token, user_tokens
from mailroom.errors import Refusal
from mailroom.reach import reach_of
from mailroom_core.config import MAX_SAFE_INTEGER, SendMode

if TYPE_CHECKING:
    from mailroom.mcp.groups import ToolGroup
    from mailroom.models import ApiToken

api_tokens_router = APIRouter(prefix="/me/api-tokens")

TokenId = Annotated[int, Path(ge=1, le=MAX_SAFE_INTEGER)]


class NewApiToken(Limits):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    groups: list[GroupNameIn] = Field(max_length=MAX_GROUPS)
    """At least one, each offered, with what it requires."""
    expires_in_days: Literal[30, 90, 365] | None
    """Null: never."""


class ApiTokenOut(BaseModel):
    id: int
    name: str
    prefix: str
    """What the token starts with, ``mrt_`` included."""
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    expired: bool
    groups: list[str]
    """The tool groups it holds that the instance offers."""
    account_ids: list[int] | None
    """The accounts it is narrowed to (none, when they were all removed); null for every one."""
    send_ceiling: SendMode | None

    @classmethod
    def of(cls, token: ApiToken, offered: tuple[ToolGroup, ...]) -> ApiTokenOut:
        reach = reach_of(token.account_ids, token.send_ceiling)
        return cls(
            id=token.id,
            name=token.name,
            prefix=f"mrt_{token.prefix}",
            created_at=token.created_at,
            last_used_at=token.last_used_at,
            expires_at=token.expires_at,
            expired=token.expires_at is not None and token.expires_at <= datetime.now(UTC),
            groups=[group.name for group in offered if group.name in token.tool_groups],
            account_ids=reach.account_ids,
            send_ceiling=reach.ceiling,
        )


class CreatedApiToken(ApiTokenOut):
    token: str
    """Shown this once: only its hash is kept."""


class ApiTokensOut(BaseModel):
    tokens: list[ApiTokenOut]
    groups: list[ToolGroupOut]
    """The tool groups a token can hold here."""
    accounts: list[AccountChoice]
    """The user's accounts, which a token can be narrowed to."""


def _offered(request: Request) -> tuple[ToolGroup, ...]:
    groups: tuple[ToolGroup, ...] = request.app.state.tool_groups
    return groups


@api_tokens_router.get("")
def api_tokens(request: Request, ctx: Context, db: DbSession, user: CurrentUser) -> ApiTokensOut:
    """The user's tokens, newest first, expired ones too until revoked."""
    offered = _offered(request)
    return ApiTokensOut(
        tokens=[ApiTokenOut.of(token, offered) for token in user_tokens(db, user.id)],
        groups=[group_out(group) for group in offered],
        accounts=account_choices(ctx, user.id),
    )


@api_tokens_router.post("", status_code=201)
def add_api_token(
    request: Request,
    body: NewApiToken,
    response: Response,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
) -> CreatedApiToken:
    """A new token, its text in this answer only. 400 ``groups_not_approvable`` or
    ``accounts_not_approvable`` as for a connected app, 409 ``too_many_tokens``."""
    offered = _offered(request)
    groups = approvable_groups(body.groups, offered)
    reach = limits_reach(ctx, user.id, body)
    days = body.expires_in_days
    token, raw = create_token(
        db,
        user.id,
        name=body.name,
        tool_groups=groups,
        reach=reach,
        expires_in=None if days is None else timedelta(days=days),
    )
    response.headers["Cache-Control"] = "private, no-store"
    return CreatedApiToken(**ApiTokenOut.of(token, offered).model_dump(), token=raw)


@api_tokens_router.delete("/{token_id}", status_code=204)
def revoke_api_token(token_id: TokenId, db: DbSession, user: CurrentUser) -> None:
    """End it: its next request is a 401, and the sends it scheduled fail when they are due."""
    if not revoke_token(db, user.id, token_id):
        raise Refusal("not_found", "api_token_not_found", "No such API token")
