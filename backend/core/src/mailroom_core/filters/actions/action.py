"""Base action type: a function over (selected folder, UIDs), composable with + and .then()."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from mailroom_core.filters.spec.nodes import SpecSerializable, combine, wrap_repr

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mailroom_core.filters.selected import Selected


class Action(SpecSerializable):
    """Represents an action to perform on email messages.

    Built-in actions and their sequences serialise with ``to_spec()``
    (see :mod:`mailroom_core.filters.spec`).
    """

    _spec_registry: ClassVar[dict[str, type[Any]]] = {}

    def __init__(
        self, func: Callable[[Selected, Sequence[int]], None], name: str | None = None
    ) -> None:
        """Create an action from a function (selected, uids) -> None.

        name defaults to the class name.
        """
        self.func = func
        self.name = name if name is not None else type(self).__name__
        self._spec = None

    def execute(self, selected: Selected, uids: Sequence[int]) -> None:
        """Run this action on the messages `uids` of the selected folder."""
        self.func(selected, uids)

    def __add__(self, other: Action) -> Action:
        def newfunc(selected: Selected, uids: Sequence[int]) -> None:
            self.func(selected, uids)
            other.func(selected, uids)

        result = Action(newfunc, self.name + "; " + other.name)
        result._spec = combine("seq", self, other)
        return result

    def then(self, other: Action) -> Action:
        """Chain this action with another (run both in order)."""
        return self + other

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return wrap_repr(self, self._spec_registry)
