"""Flag-based email filtering criteria."""

from __future__ import annotations

from imap_thingy.core import Flag, Message, Q
from imap_thingy.filters.criteria.criterion import Criterion


class HasFlag(Criterion):
    """Criterion that matches messages that have the given IMAP flag."""

    def __init__(self, flag: Flag) -> None:
        r"""Initialize a HasFlag criterion.

        Args:
            flag: A Flag enum member (e.g. Flag.SEEN). Uses efficient SEARCH keys
                (SEEN, FLAGGED, ANSWERED).

        """
        flagname = flag.value.flagname
        imap_key = flag.value.imap_true

        def func(msg: Message) -> bool:
            return flagname in msg.flags

        super().__init__(func, Q(imap_key), is_efficient=True)


class LacksFlag(Criterion):
    """Criterion that matches messages that do not have the given IMAP flag."""

    def __init__(self, flag: Flag) -> None:
        r"""Initialize a LacksFlag criterion.

        Args:
            flag: A Flag enum member. Uses efficient SEARCH keys
                (UNSEEN, UNFLAGGED, UNANSWERED).

        """
        flagname = flag.value.flagname
        imap_key = flag.value.imap_false

        def func(msg: Message) -> bool:
            return flagname not in msg.flags

        super().__init__(func, Q(imap_key), is_efficient=True)


class IsRead(HasFlag):
    """Match messages that have been read."""

    def __init__(self) -> None:
        r"""Match \Seen."""
        super().__init__(Flag.SEEN)


class IsUnread(LacksFlag):
    """Match messages that are unread."""

    def __init__(self) -> None:
        r"""Match absence of \Seen."""
        super().__init__(Flag.SEEN)


class IsStarred(HasFlag):
    """Match messages that are starred."""

    def __init__(self) -> None:
        r"""Match \Flagged."""
        super().__init__(Flag.FLAGGED)


class IsUnstarred(LacksFlag):
    """Match messages that are not starred."""

    def __init__(self) -> None:
        r"""Match absence of \Flagged."""
        super().__init__(Flag.FLAGGED)


class IsAnswered(HasFlag):
    """Match messages that have been answered."""

    def __init__(self) -> None:
        r"""Match \Answered."""
        super().__init__(Flag.ANSWERED)


class IsUnanswered(LacksFlag):
    """Match messages that have not been answered."""

    def __init__(self) -> None:
        r"""Match absence of \Answered."""
        super().__init__(Flag.ANSWERED)
