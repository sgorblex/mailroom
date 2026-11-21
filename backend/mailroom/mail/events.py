"""Live updates for the web client (``docs/requirements/mail-client.md``, *Live updates*): what the
index's changes announced on ``NOTIFY_CHANNEL`` become for the browsers of their user.

Each API process LISTENs on ``NOTIFY_CHANNEL`` on one connection of its own (``EventHub``), from
startup to shutdown. A notification carries ids only; for a user with an open stream in this
process, the hub reads the folder's row (and the new mail's) after the commit that announced it
and hands the events to each of that user's streams. A stream's queue is bounded: one that falls
``QUEUE_BOUND`` events behind is dropped, and its browser connects again and reads afresh.

The events, each the user's own: ``folder_changed`` (the folder's counts), ``sync`` (its state
and how many messages it holds, while it is not ready and when it becomes ready) and ``new_mail``
(how many unread messages arrived, with the ref, sender and subject of the newest
``NEW_MAIL_SHOWN``, for the user's own browser to notify). Mail-derived text goes to the stream
only, never to a log.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

import anyio.to_thread
import psycopg
from sqlalchemy import any_, literal, not_, select

from mailroom.errors import Refusal
from mailroom.mail.index import NOTIFY_CHANNEL
from mailroom.mail.index import reads as index_reads
from mailroom.models import MailIndexFolder, MailIndexMessage
from mailroom_core import mail as core

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

QUEUE_BOUND: Final = 100
"""Events a stream may fall behind before it is dropped."""
STREAMS_PER_USER: Final = 10
"""Open streams per user in one API process; one more is ``busy``."""
NEW_MAIL_SHOWN: Final = 3
"""The newest messages a ``new_mail`` event names."""
TEXT_SHOWN: Final = 200
"""Characters of a sender or subject a ``new_mail`` event carries at most."""
RELISTEN_SECONDS: Final = 5.0
"""The pause before a lost LISTEN connection is opened again."""
_TOO_MANY: Final = "Too many live update streams are open; close a tab and try again."
_SEEN: Final = "\\Seen"

type EventName = Literal["folder_changed", "new_mail", "sync"]
type Interest = tuple[int, str]
"""A folder a stream's browser is viewing: its account's id and its path."""


@dataclass(frozen=True, slots=True)
class MailEvent:
    name: EventName
    data: dict[str, Any]

    def encode(self) -> bytes:
        """As a server-sent event; JSON escapes every line break the data holds."""
        data = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        return f"event: {self.name}\ndata: {data}\n\n".encode()


@dataclass(frozen=True, slots=True)
class IndexNote:
    """A notification on ``NOTIFY_CHANNEL``."""

    user_id: int
    account_id: int
    folder_id: int
    new: tuple[int, int] | None = None

    @classmethod
    def parse(cls, payload: str) -> IndexNote | None:
        try:
            note = json.loads(payload)
            new = note.get("new")
            return cls(
                user_id=int(note["user_id"]),
                account_id=int(note["account_id"]),
                folder_id=int(note["folder_id"]),
                new=None if new is None else (int(new[0]), int(new[1])),
            )
        except ValueError, TypeError, KeyError, IndexError, AttributeError:
            return None


def folder_events(db: Session, note: IndexNote, states: dict[int, str]) -> list[MailEvent]:
    """The events ``note`` stands for, read from the index now. ``states``: each folder's state
    as last seen, kept up to date here, so that a ``sync`` event goes out while a folder is not
    ready and once when it becomes ready."""
    row = db.scalar(
        select(MailIndexFolder).where(
            MailIndexFolder.id == note.folder_id,
            MailIndexFolder.user_id == note.user_id,
            MailIndexFolder.account_id == note.account_id,
        )
    )
    if row is None or not row.enabled:
        return []
    events = [
        MailEvent(
            "folder_changed",
            {
                "account": row.account_id,
                "folder": row.path,
                "unread": row.unread_count,
                "total": row.message_count,
            },
        )
    ]
    last = states.get(row.id)
    states[row.id] = row.state
    if row.state != "ready" or (last is not None and last != "ready"):
        events.append(
            MailEvent(
                "sync",
                {
                    "account": row.account_id,
                    "folder": row.path,
                    "state": row.state,
                    "progress": row.message_count,
                },
            )
        )
    if note.new is not None and row.uid_validity is not None:
        arrived = _new_mail(db, row, note.new)
        if arrived is not None:
            events.append(arrived)
    return events


def _new_mail(db: Session, row: MailIndexFolder, new: tuple[int, int]) -> MailEvent | None:
    """The unread messages stored with UIDs in ``new``; on Gmail (rows with a message id), All
    Mail's that carry INBOX's label, as INBOX's."""
    low, high = new
    query = (
        select(MailIndexMessage)
        .where(
            MailIndexMessage.folder_id == row.id,
            MailIndexMessage.user_id == row.user_id,
            MailIndexMessage.uid.between(low, high),
            not_(literal(_SEEN) == any_(MailIndexMessage.flags)),
        )
        .order_by(MailIndexMessage.received.desc(), MailIndexMessage.uid.desc())
    )
    found: Sequence[MailIndexMessage] = db.scalars(query).all()
    folder = row.path
    if any(message.gm_msgid is not None for message in found):
        found = [message for message in found if index_reads.INBOX_LABEL in message.labels]
        folder = "INBOX"
    if not found:
        return None
    assert row.uid_validity is not None
    return MailEvent(
        "new_mail",
        {
            "account": row.account_id,
            "folder": folder,
            "count": len(found),
            "messages": [
                {
                    "ref": core.format_ref(
                        core.MessageRef(row.account_id, row.path, row.uid_validity, message.uid)
                    ),
                    "sender": _sender(message)[:TEXT_SHOWN],
                    "subject": message.subject[:TEXT_SHOWN],
                }
                for message in found[:NEW_MAIL_SHOWN]
            ],
        },
    )


def _sender(message: MailIndexMessage) -> str:
    for person in message.sender:
        shown = person.get("name") or person.get("address")
        if shown:
            return str(shown)
    return ""


@dataclass(eq=False, slots=True)
class Stream:
    """One open stream of ``user_id``'s; ``interest``, the folder its browser views."""

    user_id: int
    interest: Interest | None
    queue: asyncio.Queue[MailEvent | None] = field(
        default_factory=lambda: asyncio.Queue(QUEUE_BOUND)
    )
    ended: bool = False
    """Dropped or shut down: the stream ends at its next read."""

    def end(self) -> None:
        self.ended = True
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(None)


class EventHub:
    """One API process's LISTEN connection and its users' open streams. ``sessions`` gives the
    app's session factory (None while the database is unavailable); ``connect`` opens the
    autocommit connection that LISTENs."""

    def __init__(
        self,
        sessions: Callable[[], sessionmaker[Session] | None],
        connect: Callable[[], Any],
    ) -> None:
        self._sessions = sessions
        self._connect = connect
        self._streams: dict[int, list[Stream]] = {}
        self._states: dict[int, str] = {}
        self._task: asyncio.Task[None] | None = None
        self.listening = asyncio.Event()
        """Set while the LISTEN connection is open."""

    def start(self) -> None:
        self._task = asyncio.create_task(self._listen(), name="mail-events")

    async def close(self) -> None:
        """Stop listening and end every stream."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for streams in self._streams.values():
            for stream in streams:
                stream.end()

    def check_capacity(self, user_id: int) -> None:
        """``busy`` when the user already holds ``STREAMS_PER_USER`` streams."""
        if len(self._streams.get(user_id, ())) >= STREAMS_PER_USER:
            raise Refusal("busy", "too_many_streams", _TOO_MANY)

    def open(self, user_id: int, interest: Interest | None) -> Stream:
        """A new stream of the user's; ``busy`` past ``STREAMS_PER_USER``."""
        self.check_capacity(user_id)
        stream = Stream(user_id, interest)
        self._streams.setdefault(user_id, []).append(stream)
        return stream

    def close_stream(self, stream: Stream) -> None:
        streams = self._streams.get(stream.user_id, [])
        if stream in streams:
            streams.remove(stream)
        if not streams:
            self._streams.pop(stream.user_id, None)

    def viewing(self, user_id: int, interest: Interest) -> bool:
        """Whether one of the user's open streams views ``interest``."""
        return any(stream.interest == interest for stream in self._streams.get(user_id, ()))

    def publish(self, user_id: int, events: Sequence[MailEvent]) -> None:
        """Hand ``events`` to each of the user's streams; drop one whose queue is full."""
        for stream in list(self._streams.get(user_id, ())):
            for event in events:
                try:
                    stream.queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(
                        "mail_events_dropped",
                        extra={"event": "mail_events_dropped", "user_id": user_id},
                    )
                    stream.end()
                    self.close_stream(stream)
                    break

    async def _listen(self) -> None:
        failed = False
        while True:
            try:
                async with await self._connect() as connection:
                    await connection.execute(f"LISTEN {NOTIFY_CHANNEL}")
                    self.listening.set()
                    failed = False
                    async for notification in connection.notifies():
                        await self._dispatch(notification.payload)
            except Exception as error:
                self.listening.clear()
                if not failed:
                    logger.warning(
                        "Could not listen for mail index changes: %s", type(error).__name__
                    )
                failed = True
                await asyncio.sleep(RELISTEN_SECONDS)

    async def _dispatch(self, payload: str) -> None:
        note = IndexNote.parse(payload)
        if note is None or note.user_id not in self._streams:
            return
        sessions = self._sessions()
        if sessions is None:
            return

        def read() -> list[MailEvent]:
            with sessions() as db:
                return folder_events(db, note, self._states)

        try:
            events = await anyio.to_thread.run_sync(read)
        except Exception as error:
            # Its text may quote anything, mail included: the log names its type only.
            logger.warning(
                "mail_events_read_failed",
                extra={
                    "event": "mail_events_read_failed",
                    "user_id": note.user_id,
                    "error": type(error).__name__,
                },
            )
            return
        self.publish(note.user_id, events)


def listen_connection(url: str) -> Callable[[], Any]:
    """Opens the hub's autocommit connection to ``url`` (libpq's form)."""

    async def connect() -> psycopg.AsyncConnection[Any]:
        return await psycopg.AsyncConnection.connect(url, autocommit=True)

    return connect
