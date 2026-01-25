"""Base classes for email actions."""

from collections.abc import Callable

from imap_thingy.accounts import EMailAccount


class Action:
    """Represents an action to perform on email messages.

    Actions can be combined using the & operator to chain multiple actions.
    """

    def __init__(self, func: Callable[[EMailAccount, list[int]], None], name: str = "<no name>") -> None:
        """Initialize a mail action.

        Args:
            func: Function that performs the action on a list of message IDs.
            name: Human-readable name for the action (default: "<no name>").

        """
        self.func = func
        self.name = name

    def execute(self, account: EMailAccount, msgids: list[int]) -> None:
        """Execute this action on the given message IDs.

        Args:
            account: Email account to perform the action on.
            msgids: List of message IDs to act upon.

        """
        self.func(account, msgids)

    def __and__(self, other: "Action") -> "Action":
        """Chain two actions together."""

        def newfunc(account: EMailAccount, msg: list[int]) -> None:
            self.func(account, msg)
            other.func(account, msg)

        return Action(newfunc, self.name + "; " + other.name)

    def __str__(self) -> str:
        return self.name
