"""Actions that move messages to a folder."""

from __future__ import annotations

from collections.abc import Iterable

from imapclient import IMAPClient
from imapclient.imapclient import TRASH

from imap_thingy.core import Path
from imap_thingy.filters.actions.action import Action


class MoveTo(Action):
    """Action that moves messages to the specified folder. Delimiter is forwarded from the account folder when the filter is applied."""

    def __init__(self, folder: Path) -> None:
        """Initialize a MoveTo action.

        Args:
            folder: Destination folder. Resolved with the account's delimiter at run time.

        """
        self._folder = folder
        super().__init__(
            lambda conn, msgids, *, delimiter: conn.move(msgids, self._folder.as_string(delimiter)),
            name=f"move to {folder.as_string()}",
        )


class Trash(Action):
    """Action that moves messages to the trash folder. Delimiter is forwarded from the account folder when the filter is applied."""

    def __init__(self, folder: Path | None = None) -> None:
        """Initialize a Trash action.

        Args:
            folder: Optional trash folder; if None, auto-detect via find_special_folder(TRASH). Resolved with the account's delimiter at run time.

        """
        self._folder = folder
        super().__init__(self._trash_func, name="trash")

    def _trash_func(
        self,
        conn: IMAPClient,
        msgids: Iterable[int],
        *,
        delimiter: str = "/",
    ) -> None:
        if self._folder is not None:
            dest = self._folder.as_string(delimiter)
        else:
            dest = conn.find_special_folder(TRASH)
            if dest is None:
                raise ValueError("Trash folder not found; specify a trash folder via the folder= argument")
        conn.move(msgids, dest)
