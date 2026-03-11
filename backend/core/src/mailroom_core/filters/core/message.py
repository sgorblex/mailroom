"""Message wrapper: parsed email plus IMAP id and flags."""

from __future__ import annotations

from mailparser.core import MailParser


class Message:
    """A fetched message: IMAP id, parsed body/headers, and flags."""

    __slots__ = ("id", "parsed", "flags")

    def __init__(self, id: int, parsed: MailParser, flags: list[bytes]) -> None:
        """Create a Message with IMAP id, parsed body/headers, and flags."""
        self.id = id
        self.parsed = parsed
        self.flags = flags
