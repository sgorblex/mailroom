"""No-op action."""

from __future__ import annotations

from mailroom_core.filters.actions.action import Action


class NoOp(Action):
    """Action that does nothing."""

    spec_type = "no_op"

    def __init__(self) -> None:
        """Create a no-op action."""
        super().__init__(lambda _selected, _uids: None, name="do nothing")
