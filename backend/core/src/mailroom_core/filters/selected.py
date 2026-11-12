"""The folder a run has selected, as the filter engine's actions see it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailroom_core.mail import listed_folders

if TYPE_CHECKING:
    from mailroom_core.filters.path import Path
    from mailroom_core.mail import Folder, MailboxContext


class Selected:
    """The folder a run has selected, as an action sees it: core's session on it
    (`context`, a `mailroom_core.mail.MailboxContext`) and the account's hierarchy delimiter.

    An action runs inside the run's one session, which cannot take a second connection of the
    account: it lists the folders on the connection it holds (`folders`).
    """

    def __init__(self, context: MailboxContext, delimiter: str | None) -> None:
        """The run's session on the folder, and the delimiter its paths are joined with (None
        when the server has no hierarchy)."""
        self.context = context
        self.delimiter = delimiter
        self._folders: list[Folder] | None = None

    def path(self, path: Path) -> str:
        """`path` as the server names it: its segments joined with the delimiter."""
        return path.as_string(self.delimiter or ".")

    def folders(self) -> list[Folder]:
        """The account's folders, listed once per run on the session's connection."""
        if self._folders is None:
            self._folders = listed_folders(self.context)
        return self._folders
