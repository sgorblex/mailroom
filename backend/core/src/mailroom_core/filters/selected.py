"""The folder a run has selected, as the filter engine's actions see it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailroom_core.mail import listed_folders

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mailroom_core.filters.path import Path
    from mailroom_core.filters.report import TransferReport
    from mailroom_core.mail import Folder, MailboxContext


class Selected:
    """The folder a run has selected, as an action sees it: core's session on it
    (`context`, a `mailroom_core.mail.MailboxContext`) and the account's hierarchy delimiter.

    An action runs inside the run's one session, which cannot take a second connection of the
    account: it lists the folders on the connection it holds (`folders`), and leaves what needs
    other sessions to run after it (`defer`).
    """

    def __init__(
        self,
        context: MailboxContext,
        delimiter: str | None,
        changed: set[tuple[int, str]] | None = None,
    ) -> None:
        """The run's session on the folder, the delimiter its paths are joined with (None when
        the server has no hierarchy), and the host's set of the folders the run changed
        (`Folder.run_into`'s ``changed``), which actions add to."""
        self.context = context
        self.delimiter = delimiter
        self._folders: list[Folder] | None = None
        self.changed: set[tuple[int, str]] = set() if changed is None else changed
        self.deferred: list[Callable[[], TransferReport]] = []
        """What actions left to run once the session has closed, in order."""
        self.left: set[int] = set()
        """The UIDs given to `deferred`: later filters of the run do not see them."""

    def path(self, path: Path) -> str:
        """`path` as the server names it: its segments joined with the delimiter."""
        return path.as_string(self.delimiter or ".")

    def folders(self) -> list[Folder]:
        """The account's folders, listed once per run on the session's connection."""
        if self._folders is None:
            self._folders = listed_folders(self.context)
        return self._folders

    def changes(self, path: str | None = None) -> None:
        """Note that an action is about to change mail in the selected folder, and in the
        account's folder `path`. Noted first: an action that fails may have acted on some."""
        self.changed.add((self.context.account, self.context.path))
        if path is not None:
            self.changed.add((self.context.account, path))

    def defer(self, uids: Sequence[int], finish: Callable[[], TransferReport]) -> None:
        """`finish` runs once the run's session has closed, for `uids`, which later filters of
        the run no longer match, as if moved."""
        self.deferred.append(finish)
        self.left.update(uids)
