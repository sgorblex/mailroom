"""Actions that set or clear IMAP flags (read, starred, answered)."""

from __future__ import annotations

from imap_thingy.core import Flag
from imap_thingy.filters.actions.action import Action


class AddFlag(Action):
    """Action that adds a single IMAP flag from the Flag enum."""

    def __init__(self, flag: Flag) -> None:
        """Build an action that adds the given flag."""
        flagname = flag.value.flagname
        super().__init__(
            lambda conn, msgids, *, delimiter="/": conn.add_flags(msgids, [flagname]),
        )


class RemoveFlag(Action):
    """Action that removes a single IMAP flag from the Flag enum."""

    def __init__(self, flag: Flag) -> None:
        """Build an action that removes the given flag."""
        flagname = flag.value.flagname
        super().__init__(
            lambda conn, msgids, *, delimiter="/": conn.remove_flags(msgids, [flagname]),
        )


class MarkAsRead(AddFlag):
    """Mark messages as read."""

    spec_type = "mark_as_read"

    def __init__(self) -> None:
        r"""Add \Seen."""
        super().__init__(Flag.SEEN)


class MarkAsUnread(RemoveFlag):
    """Mark messages as unread."""

    spec_type = "mark_as_unread"

    def __init__(self) -> None:
        r"""Remove \Seen."""
        super().__init__(Flag.SEEN)


class Star(AddFlag):
    """Star messages."""

    spec_type = "star"

    def __init__(self) -> None:
        r"""Add \Flagged."""
        super().__init__(Flag.FLAGGED)


class Unstar(RemoveFlag):
    """Unstar messages."""

    spec_type = "unstar"

    def __init__(self) -> None:
        r"""Remove \Flagged."""
        super().__init__(Flag.FLAGGED)


class MarkAsAnswered(AddFlag):
    """Mark messages as answered."""

    spec_type = "mark_as_answered"

    def __init__(self) -> None:
        r"""Add \Answered."""
        super().__init__(Flag.ANSWERED)


class MarkAsUnanswered(RemoveFlag):
    """Mark messages as unanswered."""

    spec_type = "mark_as_unanswered"

    def __init__(self) -> None:
        r"""Remove \Answered."""
        super().__init__(Flag.ANSWERED)
