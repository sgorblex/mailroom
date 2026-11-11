"""IMAP flag definitions: flagname and SEARCH keys when set or unset."""

from enum import Enum
from typing import NamedTuple


class FlagTriplet(NamedTuple):
    """IMAP flag name and its SEARCH keys when set or unset."""

    flagname: bytes
    imap_true: str
    imap_false: str


class Flag(Enum):
    """Standard IMAP system flags with (flagname, imap_when_set, imap_when_unset)."""

    SEEN = FlagTriplet(b"\\Seen", "SEEN", "UNSEEN")
    FLAGGED = FlagTriplet(b"\\Flagged", "FLAGGED", "UNFLAGGED")
    ANSWERED = FlagTriplet(b"\\Answered", "ANSWERED", "UNANSWERED")
