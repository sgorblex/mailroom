"""Tool groups: the unit an instance turns on (``MAILROOM_MCP_TOOL_GROUPS``). A ``stable`` group is
on by default, a ``preview`` one only when named, a ``planned`` one never; a group that ``needs`` a
feature the instance does not run (``MAILROOM_FEATURES``), never."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from mailroom.config.features import ALL_FEATURES, FEATURES_ENV

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mailroom.config.features import Feature, Features

logger = logging.getLogger(__name__)

GroupStatus = Literal["stable", "preview", "planned"]
GroupName = Literal[
    "filters_read",
    "filters_write",
    "filters_run",
    "mail_read",
    "mail_organize",
    "mail_transfer",
    "mail_folders",
    "mail_draft",
    "mail_send",
    "audit_read",
]

TOOL_GROUPS_ENV: Final = "MAILROOM_MCP_TOOL_GROUPS"


@dataclass(frozen=True, slots=True)
class ToolGroup:
    name: GroupName
    status: GroupStatus
    description: str
    needs: Feature | None = None
    """The feature without which the group is not served."""
    asks_first: bool = False
    """Its tools send mail or delete it for good: the consent screen offers it unchecked."""
    requires: tuple[GroupName, ...] = ()
    """The groups whose tools give the refs this one's take: approved only with them."""


_READ: Final[tuple[GroupName, ...]] = ("mail_read",)

GROUPS: Final[tuple[ToolGroup, ...]] = (
    ToolGroup(
        "filters_read",
        "stable",
        "accounts, folders, rule sets, filters and runs",
        needs="filters",
    ),
    ToolGroup(
        "filters_write",
        "stable",
        "create and edit disabled rule sets and their filters",
        needs="filters",
    ),
    ToolGroup(
        "filters_run",
        "stable",
        "dry runs, enabling rule sets, Run now, Run filters",
        needs="filters",
    ),
    ToolGroup("mail_read", "stable", "list, search and read mail"),
    ToolGroup("mail_organize", "stable", "move, trash and flag mail", requires=_READ),
    ToolGroup(
        "mail_transfer",
        "preview",
        "move mail to another of the user's accounts",
        asks_first=True,
        requires=_READ,
    ),
    ToolGroup("mail_folders", "stable", "create, rename and delete folders", asks_first=True),
    ToolGroup("mail_draft", "stable", "write drafts", requires=_READ),
    ToolGroup("mail_send", "stable", "send mail", asks_first=True, requires=_READ),
    ToolGroup("audit_read", "stable", "the audit trail of what was done to mail"),
)
_BY_NAME: Final[dict[str, ToolGroup]] = {group.name: group for group in GROUPS}


def _served(group: ToolGroup, features: Features) -> bool:
    return group.needs is None or group.needs in features.names


def selected_groups(raw: str | None, features: Features = ALL_FEATURES) -> tuple[ToolGroup, ...]:
    """The groups to register for ``raw`` (``MAILROOM_MCP_TOOL_GROUPS``, comma separated): unset
    or blank, every ``stable`` one; otherwise those named that are ``stable`` or ``preview``, in
    registry order. An unknown or ``planned`` name is skipped with a warning. A group needing a
    feature that is off is left out: with a warning when named, with one line for them all when
    it would have been a default."""
    names = [] if raw is None else [name.strip() for name in raw.split(",") if name.strip()]
    if not names:
        stable = [group for group in GROUPS if group.status == "stable"]
        dropped = [group for group in stable if not _served(group, features)]
        if dropped:
            logger.info(
                "Tool groups %s are not served: %s leaves out what they need",
                ", ".join(group_names(dropped)),
                FEATURES_ENV,
            )
        return tuple(group for group in stable if _served(group, features))
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
        elif not _served(group, features):
            logger.warning(
                "%s names tool group %s, which needs %s, left out of %s: skipped",
                TOOL_GROUPS_ENV,
                name,
                group.needs,
                FEATURES_ENV,
            )
        else:
            wanted.add(name)
    if not wanted:
        logger.warning("%s selects no tool group: the MCP server has no tools", TOOL_GROUPS_ENV)
    for group in GROUPS:
        missing = [name for name in group.requires if name not in wanted]
        if group.name in wanted and missing:
            logger.warning(
                "%s selects %s without %s: no served tool gives the refs it takes",
                TOOL_GROUPS_ENV,
                group.name,
                ", ".join(missing),
            )
    return tuple(group for group in GROUPS if group.name in wanted)


def group_names(groups: Sequence[ToolGroup]) -> list[str]:
    """The names of ``groups``, in their order (for logs)."""
    return [group.name for group in groups]


class UnapprovableGroupsError(ValueError):
    """A choice of tool groups that cannot be approved; the message says why."""


def approvable(chosen: Sequence[str], served: Sequence[ToolGroup]) -> list[GroupName]:
    """``chosen`` as the names to approve, in registry order: each one ``served``, with the groups
    it ``requires``. Raises :class:`UnapprovableGroupsError` for an empty choice, a group that is
    not served, or one chosen without a group it requires."""
    if not chosen:
        raise UnapprovableGroupsError("Choose at least one tool group")
    offered: dict[str, ToolGroup] = {group.name: group for group in served}
    unknown = sorted({name for name in chosen if name not in offered})
    if unknown:
        raise UnapprovableGroupsError(f"Not offered here: {', '.join(unknown)}")
    wanted = set(chosen)
    for name in wanted:
        missing = [needed for needed in offered[name].requires if needed not in wanted]
        if missing:
            raise UnapprovableGroupsError(f"{name} needs {', '.join(missing)}")
    return [group.name for group in served if group.name in wanted]
