"""Thread criteria: a message of a thread the host keeps, such as one the user muted."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from mailroom_core.filters.criteria.criterion import Criterion

if TYPE_CHECKING:
    from collections.abc import Callable

    from mailroom_core.filters.message import Message


@dataclass(frozen=True, slots=True)
class ThreadMembers:
    """What names a thread's messages: their Message-IDs (angle brackets included), and on
    Gmail its thread ids (`X-GM-THRID`)."""

    message_ids: frozenset[str] = frozenset()
    gmail_threads: frozenset[int] = frozenset()


type ThreadResolver = Callable[[str], ThreadMembers | None]
"""The host's lookup of a thread an `InThread` names: its members, None when it has none."""


class InThread(Criterion):
    """Matches messages of the thread `thread` names: one whose Message-ID, In-Reply-To or a
    References id is among its Message-IDs, or, on Gmail, whose thread id is among its thread
    ids. The spec holds the name only; the members are the host's, which `threads` looks up
    when the criterion is built (`load_criterion(..., threads=...)`). Without it, or for a
    thread it does not know, it matches nothing.

    No prefilter: every candidate is fetched.
    """

    spec_type = "in_thread"
    spec_host_args: ClassVar[frozenset[str]] = frozenset({"threads"})

    def __init__(self, thread: str, *, threads: ThreadResolver | None = None) -> None:
        """Initialize an InThread criterion.

        Args:
            thread: The host's name for the thread, opaque to the engine.
            threads: The host's lookup of the thread's members.

        Raises:
            TypeError: thread is not a string.
            ValueError: thread is empty.

        """
        if not isinstance(thread, str):
            raise TypeError(f"thread must be a string, got {type(thread).__name__}")
        if not thread:
            raise ValueError("thread must not be empty")
        members = (threads(thread) if threads is not None else None) or ThreadMembers()

        def func(msg: Message) -> bool:
            if msg.gmail_thread is not None and msg.gmail_thread in members.gmail_threads:
                return True
            parsed = msg.parsed
            ids = (parsed.message_id, *parsed.in_reply_to, *parsed.references)
            return any(found in members.message_ids for found in ids if found is not None)

        super().__init__(func)
