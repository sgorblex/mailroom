"""Actions that set or clear IMAP flags (read, starred, answered) and the Handled mark and the
To sort keyword it replaced."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailroom_core.filters.actions.action import Action
from mailroom_core.filters.flags import Flag
from mailroom_core.mail import MailError, store_flags, store_handled

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mailroom_core.filters.selected import Selected


class AddFlag(Action):
    """Action that adds a single IMAP flag from the Flag enum."""

    def __init__(self, flag: Flag, name: str | None = None) -> None:
        """Build an action that adds the given flag; name defaults to "add <flag>"."""
        flagname = flag.value.flagname.decode()

        def func(selected: Selected, uids: Sequence[int]) -> None:
            selected.changes()
            store_flags(selected.context, uids, add=[flagname])

        super().__init__(func, name=name if name is not None else f"add {flag.name}")


class RemoveFlag(Action):
    """Action that removes a single IMAP flag from the Flag enum."""

    def __init__(self, flag: Flag, name: str | None = None) -> None:
        """Build an action that removes the given flag; name defaults to "remove <flag>"."""
        flagname = flag.value.flagname.decode()

        def func(selected: Selected, uids: Sequence[int]) -> None:
            selected.changes()
            store_flags(selected.context, uids, remove=[flagname])

        super().__init__(func, name=name if name is not None else f"remove {flag.name}")


class MarkAsRead(AddFlag):
    """Mark messages as read."""

    spec_type = "mark_as_read"

    def __init__(self) -> None:
        r"""Add \Seen."""
        super().__init__(Flag.SEEN, "mark as read")


class MarkAsUnread(RemoveFlag):
    """Mark messages as unread."""

    spec_type = "mark_as_unread"

    def __init__(self) -> None:
        r"""Remove \Seen."""
        super().__init__(Flag.SEEN, "mark as unread")


class Star(AddFlag):
    """Star messages."""

    spec_type = "star"

    def __init__(self) -> None:
        r"""Add \Flagged."""
        super().__init__(Flag.FLAGGED, "star")


class Unstar(RemoveFlag):
    """Unstar messages."""

    spec_type = "unstar"

    def __init__(self) -> None:
        r"""Remove \Flagged."""
        super().__init__(Flag.FLAGGED, "unstar")


class MarkAsAnswered(AddFlag):
    """Mark messages as answered."""

    spec_type = "mark_as_answered"

    def __init__(self) -> None:
        r"""Add \Answered."""
        super().__init__(Flag.ANSWERED, "mark as answered")


class MarkAsUnanswered(RemoveFlag):
    """Mark messages as unanswered."""

    spec_type = "mark_as_unanswered"

    def __init__(self) -> None:
        r"""Remove \Answered."""
        super().__init__(Flag.ANSWERED, "mark as unanswered")


class MarkHandled(Action):
    """Mark messages Handled, as the account keeps it; `unsupported` where it cannot."""

    spec_type = "mark_handled"

    def __init__(self) -> None:
        """Set the account's Handled mark."""

        def func(selected: Selected, uids: Sequence[int]) -> None:
            selected.changes()
            store_handled(selected.context, uids, marked=True)

        super().__init__(func, name="mark handled")


class UnmarkHandled(Action):
    """Clear the Handled mark, as the account keeps it; `unsupported` where it cannot. Refused
    in the folder of the label it is kept as, which clearing it would take the messages out of,
    before the actions that follow."""

    spec_type = "unmark_handled"

    def __init__(self) -> None:
        """Clear the account's Handled mark."""

        def func(selected: Selected, uids: Sequence[int]) -> None:
            context = selected.context
            mark = context.handled
            if mark.kind == "label" and mark.name == context.path:
                raise MailError(
                    "invalid_argument", "A filter cannot clear Handled in its own label's folder."
                )
            selected.changes()
            store_handled(context, uids, marked=False)

        super().__init__(func, name="unmark handled")


class MarkToSort(AddFlag):
    """Set the To sort keyword, whatever the account keeps Handled as; `unsupported` in a folder
    that keeps no keywords."""

    spec_type = "mark_to_sort"

    def __init__(self) -> None:
        """Add the $MailroomSort keyword."""
        super().__init__(Flag.TO_SORT, "mark to sort")


class UnmarkToSort(RemoveFlag):
    """Clear the To sort keyword; `unsupported` in a folder that keeps no keywords."""

    spec_type = "unmark_to_sort"

    def __init__(self) -> None:
        """Remove the $MailroomSort keyword."""
        super().__init__(Flag.TO_SORT, "unmark to sort")
