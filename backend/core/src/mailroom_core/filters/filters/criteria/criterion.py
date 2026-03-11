"""Base criterion type: predicate over Message, composable with &, |, ~."""

from __future__ import annotations

from collections.abc import Callable

from imap_thingy.core import All, IMAPQuery, Message


class Criterion:
    """Condition on messages; has imap_query for server-side prefilter and select() for client-side filter."""

    func: Callable[[Message], bool]

    def __init__(self, func: Callable[[Message], bool], imap_query: IMAPQuery | None = All, is_efficient: bool = False) -> None:
        """Build a criterion from a predicate and optional IMAP query; is_efficient marks server-only criteria."""
        self.func = func
        self.imap_query = imap_query if imap_query is not None else All
        self.is_efficient = is_efficient

    def select(self, messages: dict[int, Message]) -> dict[int, Message]:
        """Return the subset of messages for which this criterion's predicate is true."""
        return {msgid: msg for msgid, msg in messages.items() if self.func(msg)}

    def __and__(self, other: Criterion) -> Criterion:
        def func(msg: Message) -> bool:
            return self.func(msg) and other.func(msg)

        is_efficient = self.is_efficient and other.is_efficient
        return Criterion(func, self.imap_query & other.imap_query, is_efficient)

    def __or__(self, other: Criterion) -> Criterion:
        def func(msg: Message) -> bool:
            return self.func(msg) or other.func(msg)

        is_efficient = self.is_efficient and other.is_efficient
        return Criterion(func, self.imap_query | other.imap_query, is_efficient)

    def __invert__(self) -> Criterion:
        def func(msg: Message) -> bool:
            return not self.func(msg)

        return Criterion(func, ~self.imap_query, self.is_efficient)


class Anything(Criterion):
    """Criterion that matches every message (IMAP ALL)."""

    def __init__(self) -> None:
        """Build an Anything criterion."""
        super().__init__(lambda _: True, All, is_efficient=True)
