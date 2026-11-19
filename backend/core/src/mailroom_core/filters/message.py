"""Message wrapper: parsed email plus IMAP id and flags."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mailroom_core.mail import ParsedMessage


class Message:
    """A fetched message: IMAP id, parsed body/headers, flags, and on Gmail its thread id."""

    __slots__ = ("flags", "gmail_thread", "id", "parsed")

    def __init__(
        self,
        id: int,  # noqa: A002 -- public keyword, mirrors the `id` slot
        parsed: ParsedMessage,
        flags: list[bytes],
        gmail_thread: int | None = None,
    ) -> None:
        """Create a Message with IMAP id, parsed body/headers, flags and Gmail thread id."""
        self.id = id
        self.parsed = parsed
        self.flags = flags
        self.gmail_thread = gmail_thread
