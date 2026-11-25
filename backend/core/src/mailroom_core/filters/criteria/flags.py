"""Flag-based email filtering criteria."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailroom_core.filters.criteria.criterion import Criterion
from mailroom_core.filters.flags import Flag
from mailroom_core.filters.query import Q
from mailroom_core.filters.spec.nodes import FLAG, codecs
from mailroom_core.mail import HandledKey

if TYPE_CHECKING:
    from mailroom_core.filters.message import Message


def _holds(msg: Message, flagname: bytes) -> bool:
    """Whether the message has the flag; IMAP compares flags and keywords ignoring case."""
    wanted = flagname.lower()
    return any(flag.lower() == wanted for flag in msg.flags)


class HasFlag(Criterion):
    """Criterion that matches messages that have the given IMAP flag."""

    spec_type = "has_flag"
    spec_codecs = codecs(flag=FLAG)

    def __init__(self, flag: Flag) -> None:
        r"""Initialize a HasFlag criterion.

        Args:
            flag: A Flag enum member (e.g. Flag.SEEN). Uses efficient SEARCH keys
                (SEEN, FLAGGED, ANSWERED, KEYWORD $MailroomSort).

        """
        flagname = flag.value.flagname
        imap_key = flag.value.imap_true

        def func(msg: Message) -> bool:
            return _holds(msg, flagname)

        super().__init__(func, Q(imap_key), is_efficient=True)


class LacksFlag(Criterion):
    """Criterion that matches messages that do not have the given IMAP flag."""

    spec_type = "lacks_flag"
    spec_codecs = codecs(flag=FLAG)

    def __init__(self, flag: Flag) -> None:
        r"""Initialize a LacksFlag criterion.

        Args:
            flag: A Flag enum member. Uses efficient SEARCH keys
                (UNSEEN, UNFLAGGED, UNANSWERED, UNKEYWORD $MailroomSort).

        """
        flagname = flag.value.flagname
        imap_key = flag.value.imap_false

        def func(msg: Message) -> bool:
            return not _holds(msg, flagname)

        super().__init__(func, Q(imap_key), is_efficient=True)


class IsRead(HasFlag):
    """Match messages that have been read."""

    spec_type = "is_read"

    def __init__(self) -> None:
        r"""Match \Seen."""
        super().__init__(Flag.SEEN)


class IsUnread(LacksFlag):
    """Match messages that are unread."""

    spec_type = "is_unread"

    def __init__(self) -> None:
        r"""Match absence of \Seen."""
        super().__init__(Flag.SEEN)


class IsStarred(HasFlag):
    """Match messages that are starred."""

    spec_type = "is_starred"

    def __init__(self) -> None:
        r"""Match \Flagged."""
        super().__init__(Flag.FLAGGED)


class IsUnstarred(LacksFlag):
    """Match messages that are not starred."""

    spec_type = "is_unstarred"

    def __init__(self) -> None:
        r"""Match absence of \Flagged."""
        super().__init__(Flag.FLAGGED)


class IsAnswered(HasFlag):
    """Match messages that have been answered."""

    spec_type = "is_answered"

    def __init__(self) -> None:
        r"""Match \Answered."""
        super().__init__(Flag.ANSWERED)


class IsUnanswered(LacksFlag):
    """Match messages that have not been answered."""

    spec_type = "is_unanswered"

    def __init__(self) -> None:
        r"""Match absence of \Answered."""
        super().__init__(Flag.ANSWERED)


class IsHandled(Criterion):
    """Match messages marked Handled, as the account keeps it when the filter runs."""

    spec_type = "is_handled"

    def __init__(self) -> None:
        """Match the account's Handled mark."""
        super().__init__(lambda msg: msg.handled, Q(HandledKey.HANDLED), is_efficient=True)


class IsNotHandled(Criterion):
    """Match messages not marked Handled, as the account keeps it when the filter runs."""

    spec_type = "is_not_handled"

    def __init__(self) -> None:
        """Match absence of the account's Handled mark."""
        super().__init__(lambda msg: not msg.handled, Q(HandledKey.NOT_HANDLED), is_efficient=True)


class IsToSort(HasFlag):
    """Match messages with the To sort keyword, whatever the account keeps Handled as."""

    spec_type = "is_to_sort"

    def __init__(self) -> None:
        """Match the $MailroomSort keyword."""
        super().__init__(Flag.TO_SORT)


class IsNotToSort(LacksFlag):
    """Match messages without the To sort keyword, whatever the account keeps Handled as."""

    spec_type = "is_not_to_sort"

    def __init__(self) -> None:
        """Match absence of the $MailroomSort keyword."""
        super().__init__(Flag.TO_SORT)
