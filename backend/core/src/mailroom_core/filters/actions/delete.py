"""Actions that permanently delete messages."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailroom_core.filters.actions.action import Action
from mailroom_core.mail import delete_uids

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mailroom_core.filters.selected import Selected


class Delete(Action):
    """Action that permanently deletes messages: the user's own rule, Mailroom's one permanent
    delete of mail it did not write (docs/core.md, *Safety*). Exactly those UIDs are expunged,
    which needs UIDPLUS: without it the action fails `unsupported` before anything is marked."""

    spec_type = "delete"

    def __init__(self) -> None:
        """Initialize a Delete action."""

        def func(selected: Selected, uids: Sequence[int]) -> None:
            delete_uids(selected.context, uids)

        super().__init__(func, name="delete")
