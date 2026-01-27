"""IMAP-based email actions."""

import imapclient

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.actions.base import Action


class MoveTo(Action):
    """Action that moves messages to the specified folder."""

    def __init__(self, folder: str) -> None:
        """Initialize a MoveTo action.

        Args:
            folder: Destination folder name.

        """

        def func(account: EMailAccount, msgids: list[int]) -> None:
            account.connection.move(msgids, folder)

        super().__init__(func, name=f"move to {folder}")


class Trash(Action):
    """Action that moves messages to the trash folder."""

    def __init__(self) -> None:
        """Initialize a Trash action."""

        def func(account: EMailAccount, msgids: list[int]) -> None:
            account.connection.move(msgids, account.connection.find_special_folder(imapclient.TRASH))

        super().__init__(func, name="trash")


class MarkAsRead(Action):
    """Action that marks messages as read."""

    def __init__(self) -> None:
        """Initialize a MarkAsRead action."""

        def func(account: EMailAccount, msgids: list[int]) -> None:
            account.connection.add_flags(msgids, [b"\\Seen"])

        super().__init__(func, name="mark as read")


class MarkAsUnread(Action):
    """Action that marks messages as unread."""

    def __init__(self) -> None:
        """Initialize a MarkAsUnread action."""

        def func(account: EMailAccount, msgids: list[int]) -> None:
            account.connection.remove_flags(msgids, [b"\\Seen"])

        super().__init__(func, name="mark as unread")


class Star(Action):
    """Action that stars messages."""

    def __init__(self) -> None:
        """Initialize a Star action."""

        def func(account: EMailAccount, msgids: list[int]) -> None:
            account.connection.add_flags(msgids, [b"\\Flagged"])

        super().__init__(func, name="star")


class Unstar(Action):
    """Action that unstars messages."""

    def __init__(self) -> None:
        """Initialize an Unstar action."""

        def func(account: EMailAccount, msgids: list[int]) -> None:
            account.connection.remove_flags(msgids, [b"\\Flagged"])

        super().__init__(func, name="unstar")
