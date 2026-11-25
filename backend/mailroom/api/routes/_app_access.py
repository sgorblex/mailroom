"""What a user gives an app or a personal API token: its tool groups, and its limits."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from mailroom.errors import Refusal
from mailroom.mcp.groups import UnapprovableGroupsError, approvable
from mailroom.reach import Reach
from mailroom_core.config import MAX_SAFE_INTEGER, SendMode

if TYPE_CHECKING:
    from mailroom.context import AppContext
    from mailroom.mcp.groups import ToolGroup

MAX_GROUPS = 32
MAX_ACCOUNTS = 1000
AccountIdIn = Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)]
GroupNameIn = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ToolGroupOut(BaseModel):
    name: str
    description: str
    requires: list[str]
    """Groups it can be approved only with."""
    default: bool
    """Whether the consent screen offers it checked: not for groups that send or delete mail for
    good."""


class AccountChoice(BaseModel):
    """One of the user's accounts an app can be narrowed to."""

    id: int
    name: str


class Limits(BaseModel):
    """What an app is limited to, beyond its tool groups."""

    model_config = ConfigDict(extra="forbid")

    account_ids: list[AccountIdIn] | None = Field(default=None, max_length=MAX_ACCOUNTS)
    """The accounts it sees, at least one, each the user's; null for every one, those added
    later too."""
    send_ceiling: SendMode | None = None
    """The most it may do when it sends, whatever an account's send mode; null for no ceiling."""


def group_out(group: ToolGroup) -> ToolGroupOut:
    return ToolGroupOut(
        name=group.name,
        description=group.description,
        requires=list(group.requires),
        default=not group.asks_first,
    )


def approvable_groups(chosen: list[str], served: tuple[ToolGroup, ...]) -> list[str]:
    try:
        return list(approvable(chosen, served))
    except UnapprovableGroupsError as exc:
        raise Refusal("invalid", "groups_not_approvable", str(exc)) from None


def account_choices(ctx: AppContext, user_id: int) -> list[AccountChoice]:
    return [
        AccountChoice(id=account.id, name=account.name or account.login)
        for account in ctx.accounts(user_id).settings.account
    ]


def limits_reach(ctx: AppContext, user_id: int, limits: Limits) -> Reach:
    """The reach ``limits`` give: 400 ``accounts_not_approvable`` for accounts that are not all the
    user's, or none."""
    if limits.account_ids is None:
        return Reach(None, limits.send_ceiling)
    chosen = frozenset(limits.account_ids)
    owned = {account.id for account in ctx.accounts(user_id).settings.account}
    if not chosen or not chosen <= owned:
        msg = "Choose at least one of your accounts" if not chosen else "Not all your accounts"
        raise Refusal("invalid", "accounts_not_approvable", msg)
    return Reach(chosen, limits.send_ceiling)
