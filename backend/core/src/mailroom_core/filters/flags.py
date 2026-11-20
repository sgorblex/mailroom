"""IMAP flag definitions: flagname and SEARCH keys when set or unset."""

from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

from mailroom_core.mail import TO_SORT as KEYWORD

if TYPE_CHECKING:
    from mailroom_core.filters.query import BaseQuery


class FlagTriplet(NamedTuple):
    """IMAP flag name and its SEARCH keys when set or unset."""

    flagname: bytes
    imap_true: BaseQuery
    imap_false: BaseQuery


class Flag(Enum):
    """The IMAP system flags Mailroom sets, and the To sort keyword, with (flagname,
    imap_when_set, imap_when_unset). A keyword needs a server that keeps keywords (PERMANENTFLAGS
    holding `\\*`); its actions are refused elsewhere."""

    SEEN = FlagTriplet(b"\\Seen", "SEEN", "UNSEEN")
    FLAGGED = FlagTriplet(b"\\Flagged", "FLAGGED", "UNFLAGGED")
    ANSWERED = FlagTriplet(b"\\Answered", "ANSWERED", "UNANSWERED")
    TO_SORT = FlagTriplet(KEYWORD.encode(), ("KEYWORD", KEYWORD), ("UNKEYWORD", KEYWORD))
