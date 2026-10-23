"""Filter criteria: Criterion, address/body/subject/date/flags criteria."""

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
from imap_thingy.filters.criteria.body import BodyContains, BodyMatches
from imap_thingy.filters.criteria.criterion import Anything, Criterion
from imap_thingy.filters.criteria.date import OlderThan, OlderThanDays
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
    "Flag",
    "FromContains",
    "FromIs",
    "FromMatches",
    "FromMatchesName",
    "HasFlag",
    "IsAnswered",
    "IsRead",
    "IsStarred",
    "IsUnanswered",
    "IsUnread",
    "IsUnstarred",
    "LacksFlag",
    "OlderThan",
    "OlderThanDays",
    "SubjectContains",
    "SubjectIs",
    "SubjectMatches",
    "ToContains",
    "ToIs",
    "ToMatches",
]
