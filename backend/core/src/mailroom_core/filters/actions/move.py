"""Actions that move messages to a folder, as the mail client moves them."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mailroom_core.filters.actions.action import Action
from mailroom_core.filters.spec.nodes import OPTIONAL_PATH, PATH, codecs
from mailroom_core.mail import MailError, move_target, move_uids, special_folder_in

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mailroom_core.filters.path import Path
    from mailroom_core.filters.selected import Selected

log = logging.getLogger(__name__)


def _move(selected: Selected, uids: Sequence[int], target: str) -> None:
    """`uids` moved to `target` with the client's checks (`mail.move_target`): a folder of
    the account, never Drafts or Sent. A move into the folder being filtered (Trash from Trash
    included) is skipped: the messages are already there."""
    context = selected.context
    if _same_folder(target, context.path):
        log.info("skipped a move of %s message(s) to the folder they are in", len(uids))
        return
    found = move_target(context.path, target, selected.folders(), account=context.account)
    move_uids(context, uids, found.path)


def _same_folder(a: str, b: str) -> bool:
    return a == b or a.upper() == b.upper() == "INBOX"


class MoveTo(Action):
    """Action that moves messages to the specified folder.

    The folder's segments are joined with the account's delimiter when the filter runs. It must
    be a folder of the account other than Drafts and Sent: `mailbox_missing` or
    `invalid_argument` otherwise. Without MOVE, the messages are copied and exactly those UIDs
    expunged (UIDPLUS); a server with neither refuses the move (`unsupported`).
    """

    spec_type = "move_to"
    spec_codecs = codecs(folder=PATH)

    def __init__(self, folder: Path) -> None:
        """Initialize a MoveTo action.

        Args:
            folder: Destination folder. Resolved with the account's delimiter at run time.

        """
        self._folder = folder
        super().__init__(
            lambda selected, uids: _move(selected, uids, selected.path(self._folder)),
            name=f"move to {folder.as_string()}",
        )


class Trash(Action):
    r"""Action that moves messages to the trash folder: the given one, or the account's
    special-use ``\Trash`` (`unsupported` when it has none). Moved as `MoveTo` moves.
    """

    spec_type = "trash"
    spec_codecs = codecs(folder=OPTIONAL_PATH)

    def __init__(self, folder: Path | None = None) -> None:
        """Initialize a Trash action.

        Args:
            folder: Optional trash folder; if None, the account's special-use Trash.
                Resolved with the account's delimiter at run time.

        """
        self._folder = folder
        super().__init__(self._trash, name="trash")

    def _trash(self, selected: Selected, uids: Sequence[int]) -> None:
        if self._folder is not None:
            destination = selected.path(self._folder)
        else:
            trash = special_folder_in(selected.folders(), "trash")
            if trash is None:
                raise MailError(
                    "unsupported", "This account has no Trash folder: name one in the action."
                )
            destination = trash.path
        _move(selected, uids, destination)
