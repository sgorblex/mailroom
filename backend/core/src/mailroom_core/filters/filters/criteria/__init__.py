"""Email filtering criteria for matching messages."""

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
from imap_thingy.filters.criteria.base import Criterion, EfficientCriterion, SelectAll
from imap_thingy.filters.criteria.date import OlderThan
from imap_thingy.filters.criteria.duplicate import DuplicateCriterion
from imap_thingy.filters.criteria.flags import IsAnswered, IsRead, IsStarred, IsUnanswered, IsUnread, IsUnstarred
from imap_thingy.filters.criteria.subject import SubjectContains, SubjectIs, SubjectMatches

__all__ = [
    "Criterion",
    "EfficientCriterion",
    "SelectAll",
    "FromContains",
    "FromIs",
    "FromMatches",
    "FromMatchesName",
    "ToContains",
    "ToIs",
    "ToMatches",
    "CcContains",
    "CcIs",
    "CcMatches",
    "BccContains",
    "BccIs",
    "BccMatches",
    "SubjectContains",
    "SubjectIs",
    "SubjectMatches",
    "OlderThan",
    "DuplicateCriterion",
    "IsAnswered",
    "IsRead",
    "IsStarred",
    "IsUnanswered",
    "IsUnread",
    "IsUnstarred",
]
