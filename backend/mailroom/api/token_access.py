"""Which routes a personal API token reaches: those marked ``open_to_tokens``, for a token holding
one of their tool groups. A route without the mark refuses every token, so that a new one is the
signed-in user's only until it says otherwise."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.requests import Request

    from mailroom.mcp.groups import GroupName

_MARK: Final = "__mailroom_token_groups__"

ACCOUNT_GROUPS: Final[tuple[GroupName, ...]] = (
    "filters_read",
    "mail_read",
    "mail_organize",
    "mail_transfer",
    "mail_folders",
    "mail_draft",
    "mail_send",
)
"""The groups that take an account's id and its folders: any of them lists these, as MCP's
``accounts_list`` and ``folders_list`` do."""


def open_to_tokens[F: Callable[..., object]](*groups: GroupName) -> Callable[[F], F]:
    """Mark a route's function (below its router's decorator) as open to a token that holds one
    of ``groups``."""

    def mark(route: F) -> F:
        setattr(route, _MARK, frozenset(groups))
        return route

    return mark


def groups_of(endpoint: object) -> frozenset[str]:
    """The groups that open ``endpoint`` to a token; none for one no token reaches."""
    groups: frozenset[str] = getattr(endpoint, _MARK, frozenset())
    return groups


def route_groups(request: Request) -> frozenset[str]:
    """``groups_of`` the route ``request`` was matched to."""
    return groups_of(request.scope.get("endpoint"))
