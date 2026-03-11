"""No-op action."""

from __future__ import annotations

from imap_thingy.filters.actions.action import Action


class NoOp(Action):
    """Action that does nothing."""

    def __init__(self) -> None:
        """Create a no-op action."""
        super().__init__(lambda conn, msgids, *, delimiter="/": None)
