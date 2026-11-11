"""Base action type: callable over (conn, msgids), composable with + and .then()."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from imap_thingy.core.spec import SpecSerializable, combine, wrap_repr

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from imapclient import IMAPClient


class Action(SpecSerializable):
    """Represents an action to perform on email messages.

    Built-in actions and their sequences serialise with ``to_spec()``
    (see :mod:`imap_thingy.spec`).
    """

    _spec_registry: ClassVar[dict[str, type[Any]]] = {}

    def __init__(self, func: Callable[..., None], name: str | None = None) -> None:
        """Create an action from a function (conn, msgids, *, delimiter) -> None.

        name defaults to the class name.
        """
        self.func = func
        self.name = name if name is not None else type(self).__name__
        self._spec = None

    def execute(
        self,
        conn: IMAPClient,
        msgids: Iterable[int],
        *,
        delimiter: str = "/",
    ) -> None:
        """Run this action; delimiter is forwarded from the account folder the filter runs on."""
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
