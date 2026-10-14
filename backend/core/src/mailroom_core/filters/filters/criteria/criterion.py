"""Base criterion type: predicate over Message, composable with &, |, ~."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from imap_thingy.core import All, IMAPQuery, Message
from imap_thingy.core.spec import SpecSerializable, combine, wrap_repr

if TYPE_CHECKING:
    from collections.abc import Callable


class Criterion(SpecSerializable):
    """Condition on messages.

    Has imap_query for the server-side prefilter and select() for the client-side filter.
    Built-in criteria and their combinations serialise with ``to_spec()``
    (see :mod:`imap_thingy.spec`).
    """

    _spec_registry: ClassVar[dict[str, type[Any]]] = {}
    func: Callable[[Message], bool]

    def __init__(
        self,
        func: Callable[[Message], bool],
        imap_query: IMAPQuery | None = All,
        is_efficient: bool = False,
    ) -> None:
        """Build a criterion from a predicate and optional IMAP query.

        is_efficient marks criteria the IMAP query answers exactly (no fetch needed).
        """
        self.func = func
        self.imap_query = imap_query if imap_query is not None else All
        self.is_efficient = is_efficient
        self._spec = None

    def select(self, messages: dict[int, Message]) -> dict[int, Message]:
        """Return the subset of messages for which this criterion's predicate is true."""
        return {msgid: msg for msgid, msg in messages.items() if self.func(msg)}

    def __and__(self, other: Criterion) -> Criterion:
        def func(msg: Message) -> bool:
            return self.func(msg) and other.func(msg)

        is_efficient = self.is_efficient and other.is_efficient
        result = Criterion(func, self.imap_query & other.imap_query, is_efficient)
        result._spec = combine("and", self, other)
        return result

    def __or__(self, other: Criterion) -> Criterion:
        def func(msg: Message) -> bool:
            return self.func(msg) or other.func(msg)

        is_efficient = self.is_efficient and other.is_efficient
        result = Criterion(func, self.imap_query | other.imap_query, is_efficient)
        result._spec = combine("or", self, other)
        return result

    def __invert__(self) -> Criterion:
        def func(msg: Message) -> bool:
            return not self.func(msg)

        result = Criterion(func, ~self.imap_query, self.is_efficient)
        result._spec = None if self._spec is None else {"not": self._spec}
        return result

    def __repr__(self) -> str:
        return wrap_repr(self, self._spec_registry)


class Anything(Criterion):
    """Criterion that matches every message (IMAP ALL)."""

    spec_type = "anything"

    def __init__(self) -> None:
        """Build an Anything criterion."""
        super().__init__(lambda _: True, All, is_efficient=True)
