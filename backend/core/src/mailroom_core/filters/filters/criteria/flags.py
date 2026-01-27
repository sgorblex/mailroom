"""Flag-based email filtering criteria."""

from imap_thingy.filters.criteria.base import EfficientCriterion, ParsedMail


class IsRead(EfficientCriterion):
    """Matches messages that have been read."""

    def __init__(self) -> None:
        """Initialize an IsRead criterion."""

        def func(msg: ParsedMail) -> bool:
            flags = getattr(msg, "_imap_flags", [])
            return b"\\Seen" in flags

        super().__init__(func, imap_query=["SEEN"])


class IsUnread(EfficientCriterion):
    """Matches messages that are unread."""

    def __init__(self) -> None:
        """Initialize an IsUnread criterion."""

        def func(msg: ParsedMail) -> bool:
            flags = getattr(msg, "_imap_flags", [])
            return b"\\Seen" not in flags

        super().__init__(func, imap_query=["UNSEEN"])
