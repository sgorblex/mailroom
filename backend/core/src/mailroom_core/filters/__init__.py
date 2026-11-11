"""The filter engine: rules (a criterion and an action) that sort mail on an IMAP server.

Rules are built in Python or loaded from their data form (`load_filter`, `json_schema`), run on a
folder (`Folder.run`, `run_rules`) or on new mail through IDLE (`monitor`), and report what they
matched and did (`FolderReport`). It was imap-thingy, a library of its own, until it joined
mailroom-core.
"""

from mailroom_core.filters.account import Account, Folder
from mailroom_core.filters.actions.action import Action
from mailroom_core.filters.actions.delete import Delete
from mailroom_core.filters.actions.flags import (
    MarkAsAnswered,
    MarkAsRead,
    MarkAsUnanswered,
    MarkAsUnread,
    Star,
    Unstar,
)
from mailroom_core.filters.actions.move import MoveTo, Trash
from mailroom_core.filters.actions.noop import NoOp
from mailroom_core.filters.criteria.address import (
    BccContains,
    BccIs,
    BccMatches,
    CcContains,
    CcIs,
    CcMatches,
    FromContains,
    FromIs,
    FromMatches,
    FromMatchesName,
    ToContains,
    ToIs,
    ToMatches,
)
from mailroom_core.filters.criteria.body import BodyContains, BodyMatches
from mailroom_core.filters.criteria.criterion import Anything, Criterion
from mailroom_core.filters.criteria.date import OlderThan, OlderThanDays
from mailroom_core.filters.criteria.flags import (
    HasFlag,
    IsAnswered,
    IsRead,
    IsStarred,
    IsUnanswered,
    IsUnread,
    IsUnstarred,
    LacksFlag,
)
from mailroom_core.filters.criteria.subject import SubjectContains, SubjectIs, SubjectMatches
from mailroom_core.filters.filter import Filter, If
from mailroom_core.filters.flags import Flag
from mailroom_core.filters.message import Message
from mailroom_core.filters.path import Path
from mailroom_core.filters.pattern import (
    DEFAULT_MATCH_TIMEOUT,
    MatchTimeoutError,
    get_match_timeout,
    set_match_timeout,
)
from mailroom_core.filters.query import All, IMAPQuery, Q
from mailroom_core.filters.report import FilterReport, FolderReport, MessageSummary
from mailroom_core.filters.runner import Rules, RunRulesError, monitor, run_rules, starred_only
from mailroom_core.filters.spec import (
    NotSerializableError,
    SpecError,
    SpecNode,
    json_schema,
    load_action,
    load_criterion,
    load_filter,
)

__all__ = [
    "DEFAULT_MATCH_TIMEOUT",
    "Account",
    "Action",
    "All",
    "Anything",
    "BccContains",
    "BccIs",
    "BccMatches",
    "BodyContains",
    "BodyMatches",
    "CcContains",
    "CcIs",
    "CcMatches",
    "Criterion",
    "Delete",
    "Filter",
    "FilterReport",
    "Flag",
    "Folder",
    "FolderReport",
    "FromContains",
    "FromIs",
    "FromMatches",
    "FromMatchesName",
    "HasFlag",
    "IMAPQuery",
    "If",
    "IsAnswered",
    "IsRead",
    "IsStarred",
    "IsUnanswered",
    "IsUnread",
    "IsUnstarred",
    "LacksFlag",
    "MarkAsAnswered",
    "MarkAsRead",
    "MarkAsUnanswered",
    "MarkAsUnread",
    "MatchTimeoutError",
    "Message",
    "MessageSummary",
    "MoveTo",
    "NoOp",
    "NotSerializableError",
    "OlderThan",
    "OlderThanDays",
    "Path",
    "Q",
    "Rules",
    "RunRulesError",
    "SpecError",
    "SpecNode",
    "Star",
    "SubjectContains",
    "SubjectIs",
    "SubjectMatches",
    "ToContains",
    "ToIs",
    "ToMatches",
    "Trash",
    "Unstar",
    "get_match_timeout",
    "json_schema",
    "load_action",
    "load_criterion",
    "load_filter",
    "monitor",
    "run_rules",
    "set_match_timeout",
    "starred_only",
]
