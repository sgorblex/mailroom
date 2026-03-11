"""Base action type: callable over (conn, msgids), composable with + and .then()."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from imapclient import IMAPClient


class Action:
    """Represents an action to perform on email messages."""

    def __init__(self, func: Callable[..., None], name: str | None = None) -> None:
        """Create an action from a function (conn, msgids, *, delimiter) -> None and an optional name."""
        self.func = func
        self.name = name if name is not None else type(self).__name__

    def execute(
        self,
        conn: IMAPClient,
        msgids: Iterable[int],
        *,
        delimiter: str = "/",
    ) -> None:
        """Run this action. Delimiter is forwarded from the account folder when the filter is applied."""
        self.func(conn, msgids, delimiter=delimiter)

    def __add__(self, other: Action) -> Action:
        def newfunc(
            conn: IMAPClient,
            msg: Iterable[int],
            *,
            delimiter: str = "/",
        ) -> None:
            self.func(conn, msg, delimiter=delimiter)
            other.func(conn, msg, delimiter=delimiter)

        return Action(newfunc, self.name + "; " + other.name)

    def then(self, other: Action) -> Action:
        """Chain this action with another (run both in order)."""
        return self + other

    def __str__(self) -> str:
        return self.name
