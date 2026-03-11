"""Actions that permanently delete messages."""

from __future__ import annotations

from collections.abc import Iterable

from imapclient import IMAPClient

from imap_thingy.filters.actions.action import Action


class Delete(Action):
    """Action that permanently deletes messages."""

    def __init__(self) -> None:
        """Initialize a Delete action."""

        def func(
            conn: IMAPClient,
            msgids: Iterable[int],
            *,
            delimiter: str = "/",
        ) -> None:
            conn.add_flags(msgids, [b"\\Deleted"])
            conn.expunge(msgids)

        super().__init__(func)
