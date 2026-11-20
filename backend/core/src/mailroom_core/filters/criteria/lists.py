"""Mailing-list criteria."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailroom_core.filters.criteria.criterion import Criterion

if TYPE_CHECKING:
    from mailroom_core.filters.message import Message


class ListIs(Criterion):
    """Matches messages of the mailing list `list_id` (the List-Id field's identifier, RFC 2919),
    in any case.

    No prefilter: a server's header search can miss (Gmail ignores it, a full-text index may
    leave the field out), so every candidate is fetched.
    """

    spec_type = "list_is"

    def __init__(self, list_id: str) -> None:
        """Initialize a ListIs criterion.

        Args:
            list_id: The list's identifier, with or without its angle brackets
                (``<news.example.com>`` or ``news.example.com``).

        Raises:
            TypeError: list_id is not a string.
            ValueError: list_id is empty.

        """
        if not isinstance(list_id, str):
            raise TypeError(f"list_id must be a string, got {type(list_id).__name__}")
        wanted = list_id.strip().removeprefix("<").removesuffix(">").strip().casefold()
        if not wanted:
            raise ValueError("list_id must not be empty")

        def func(msg: Message) -> bool:
            found = msg.parsed.list_id
            return found is not None and found.casefold() == wanted

        super().__init__(func)
