"""Message wrapper: parsed email plus IMAP id and flags."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mailroom_core.mail import ParsedMessage


class Message:
    """A fetched message: IMAP id, parsed body/headers, and flags."""

    __slots__ = ("flags", "id", "parsed")

    def __init__(self, id: int, parsed: ParsedMessage, flags: list[bytes]) -> None:  # noqa: A002 -- public keyword, mirrors the `id` slot
        """Create a Message with IMAP id, parsed body/headers, and flags."""
        self.id = id
        self.parsed = parsed
        self.flags = flags
