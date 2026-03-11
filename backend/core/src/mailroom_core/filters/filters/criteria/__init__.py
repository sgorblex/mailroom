"""Filter criteria: Criterion, address/subject/date/flags criteria."""

from imap_thingy.core import Flag
from imap_thingy.filters.criteria.address import (
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
from imap_thingy.filters.criteria.criterion import Anything, Criterion
from imap_thingy.filters.criteria.date import OlderThan
from imap_thingy.filters.criteria.flags import (
    HasFlag,
    IsAnswered,
    IsRead,
    IsStarred,
    IsUnanswered,
    IsUnread,
    IsUnstarred,
    LacksFlag,
)
from imap_thingy.filters.criteria.subject import SubjectContains, SubjectIs, SubjectMatches

__all__ = [
    "BccContains",
    "BccIs",
    "BccMatches",
    "CcContains",
    "CcIs",
    "CcMatches",
    "Criterion",
    "FromContains",
    "FromIs",
    "FromMatches",
    "FromMatchesName",
    "Flag",
    "HasFlag",
    "IsAnswered",
    "LacksFlag",
    "IsRead",
    "IsStarred",
    "IsUnanswered",
    "IsUnread",
    "IsUnstarred",
    "Anything",
    "OlderThan",
    "SubjectContains",
    "SubjectIs",
    "SubjectMatches",
    "ToContains",
    "ToIs",
    "ToMatches",
]
