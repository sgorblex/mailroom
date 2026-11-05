"""Tool groups: the unit an instance turns on (``MAILROOM_MCP_TOOL_GROUPS``). A ``stable`` group is
on by default, a ``preview`` one only when named, a ``planned`` one never."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

GroupStatus = Literal["stable", "preview", "planned"]
GroupName = Literal[
    "filters_read",
    "filters_write",
    "filters_run",
    "mail_read",
    "mail_organize",
    "mail_draft",
    "mail_send",
]

TOOL_GROUPS_ENV: Final = "MAILROOM_MCP_TOOL_GROUPS"


@dataclass(frozen=True, slots=True)
class ToolGroup:
    name: GroupName
    status: GroupStatus
    description: str


GROUPS: Final[tuple[ToolGroup, ...]] = (
    ToolGroup("filters_read", "stable", "accounts, folders, rule sets, filters and runs"),
    ToolGroup("filters_write", "stable", "create and edit disabled rule sets and their filters"),
    ToolGroup("filters_run", "stable", "dry runs, enabling rule sets, Run now, Run filters"),
    ToolGroup("mail_read", "stable", "list, search and read mail"),
    ToolGroup("mail_organize", "stable", "move, trash and flag mail"),
    ToolGroup("mail_draft", "planned", "write drafts"),
    ToolGroup("mail_send", "planned", "send mail"),
)
_BY_NAME: Final[dict[str, ToolGroup]] = {group.name: group for group in GROUPS}


def selected_groups(raw: str | None) -> tuple[ToolGroup, ...]:
    """The groups to register for ``raw`` (``MAILROOM_MCP_TOOL_GROUPS``, comma separated): unset
    or blank, every ``stable`` one; otherwise those named that are ``stable`` or ``preview``, in
    registry order. An unknown or ``planned`` name is skipped with a warning."""
    names = [] if raw is None else [name.strip() for name in raw.split(",") if name.strip()]
    if not names:
        return tuple(group for group in GROUPS if group.status == "stable")
    wanted: set[str] = set()
    for name in names:
        group = _BY_NAME.get(name)
        if group is None:
            logger.warning("%s names an unknown tool group, skipped: %r", TOOL_GROUPS_ENV, name)
        elif group.status == "planned":
            logger.warning(
                "%s names tool group %s, which is planned and has no tools yet: skipped",
                TOOL_GROUPS_ENV,
                name,
            )
        else:
            wanted.add(name)
    if not wanted:
        logger.warning("%s selects no tool group: the MCP server has no tools", TOOL_GROUPS_ENV)
    if "mail_organize" in wanted and "mail_read" not in wanted:
        logger.warning(
            "%s selects mail_organize without mail_read: no served tool gives the refs it takes",
            TOOL_GROUPS_ENV,
        )
    return tuple(group for group in GROUPS if group.name in wanted)


def group_names(groups: Sequence[ToolGroup]) -> list[str]:
    """The names of ``groups``, in their order (for logs)."""
    return [group.name for group in groups]
