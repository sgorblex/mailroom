"""Watching a folder through IMAP IDLE, on a connection of the watch's own.

A watch lives as long as its host wants it, for months, so its connection is outside
`MailConnections`' slots: a slot is closed after `Timeouts.idle` unused, and the app's API has
only two per account. It is opened with the same TLS and login rules (`_open_unpooled`), and the
folder is selected with EXAMINE, so the watch can change nothing.

The watch's thread does all the IMAP: it waits in IDLE in slices of `IDLE_POLL`, so `stop()` only
sets an event and the thread ends IDLE and logs out within a slice. After `IDLE_TIMEOUT` (servers
may drop an IDLE after 30 minutes) or any response, it ends IDLE, hands the responses to
`on_change`, then sends a NOOP and hands its responses too: whatever happened while `on_change`
ran comes with them. A host acts on another connection, never on the watch's.
"""

import logging
import select
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Self

from mailroom_core.mail.errors import MailError, mail_error
from mailroom_core.mail.imap import _drop, _log_out

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from imapclient import IMAPClient

    from mailroom_core.mail.errors import MailErrorCode
    from mailroom_core.mail.imap import MailConnections

_log = logging.getLogger("mailroom_core.mail")

IDLE_TIMEOUT: Final = 25 * 60.0
"""Seconds in one IDLE before it is ended and started again: RFC 2177 lets a server drop a client
idle for 29 minutes."""
IDLE_POLL: Final = 1.0
"""Seconds between checks of the stop request while idling: what bounds how long a stop takes."""
RECONNECT_MIN_BACKOFF: Final = 5.0
"""Seconds before the second reconnect in a row; the first one is immediate."""
RECONNECT_MAX_BACKOFF: Final = 300.0
"""The longest wait between reconnects; the wait doubles up to it and resets after a cycle of
IDLE and NOOP that went through."""

_FATAL: Final[frozenset[MailErrorCode]] = frozenset(
    {"auth_failed", "mailbox_missing", "invalid_argument", "unsupported"}
)
"""Errors no reconnect mends: the watch ends with its `failure`."""


@dataclass(frozen=True, slots=True)
class IdleChange:
    """What the server announced about the watched folder, in one batch of untagged responses."""

    exists: int | None = None
    """The folder's message count, from the last EXISTS; None without one."""
    expunged: tuple[int, ...] = ()
    """The sequence numbers EXPUNGE announced, in order."""
    flags: Mapping[int, frozenset[str]] = field(default_factory=dict)
    """The flags a FETCH announced, by sequence number, e.g. `{3: frozenset({"\\Seen"})}`."""
    responses: tuple[tuple[object, ...], ...] = ()
    """Every untagged response as imapclient parsed it, those above included: `(3, b"EXISTS")`,
    `(b"OK", b"Still here")`…"""

    @property
    def new_mail(self) -> bool:
        """Whether an EXISTS came: the folder grew, or the server restated its size."""
        return self.exists is not None

    @classmethod
    def of(cls, responses: Sequence[tuple[object, ...]]) -> Self:
        """The change `responses` announce; responses it does not know are only kept raw."""
        exists = None
        expunged: list[int] = []
        flags: dict[int, frozenset[str]] = {}
        for response in responses:
            if len(response) < 2 or not isinstance(response[0], int):
                continue
            number, kind = response[0], response[1]
            if isinstance(kind, bytes):
                kind = kind.upper()  # IMAP atoms are case-insensitive.
            if kind == b"EXISTS":
                exists = number
            elif kind == b"EXPUNGE":
                expunged.append(number)
            elif kind == b"FETCH" and len(response) > 2:
                found = _fetched_flags(response[2])
                if found is not None:
                    flags[number] = found
        return cls(exists, tuple(expunged), flags, tuple(responses))


def _fetched_flags(data: object) -> frozenset[str] | None:
    """FLAGS in a FETCH's data items, `(b"FLAGS", (…), b"UID", 7)`."""
    if not isinstance(data, tuple | list):
        return None
    items = list(data)
    for key, value in zip(items[::2], items[1::2], strict=False):
        if isinstance(key, bytes) and key.upper() == b"FLAGS":
            values = value if isinstance(value, tuple | list) else (value,)
            return frozenset(
                v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v) for v in values
            )
    return None


class IdleWatch:
    """Watches one folder of an account through IDLE on a thread of its own, until stopped.

    `on_connect` runs on every connect, the first one included, since anything may have
    arrived while the folder was not watched; `on_change` gets each batch of responses. Both
    run on the watch's thread, which waits for them: a slow callback delays the next IDLE, and
    what arrives meanwhile comes with the NOOP after it. An exception they raise is logged and
    the watch goes on.

    A lost connection is opened again, at once and then backing off from
    `RECONNECT_MIN_BACKOFF` to `RECONNECT_MAX_BACKOFF`. The watch ends on its own, with
    `failure` set, when its first connect fails (its host's own backoff then applies), or
    whenever the server refuses the login, the folder is missing, the account is unknown, or
    the server cannot IDLE.
    """

    def __init__(
        self,
        connections: MailConnections,
        account_id: str,
        path: str,
        *,
        on_change: Callable[[IdleChange], None],
        on_connect: Callable[[], None] | None = None,
        name: str | None = None,
    ) -> None:
        """Watches `path` of `account_id` once started; `name` names the thread (by default
        `idle:<account>:<path>`)."""
        self._connections = connections
        self._account = account_id
        self._path = path
        self._on_change = on_change
        self._on_connect = on_connect
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._failure: MailError | None = None
        self._client: IMAPClient | None = None
        """Only the watch's thread touches it."""
        self._started = False
        self._thread = threading.Thread(
            target=self._run, name=name or f"idle:{account_id}:{path}", daemon=True
        )

    def start(self) -> Self:
        """Starts the thread and returns the watch. One-shot: never again after a start or a
        stop."""
        if self._started or self._stop.is_set():
            raise RuntimeError("an IdleWatch starts once")
        self._started = True
        self._thread.start()
        return self

    def stop(self) -> None:
        """Asks the watch to end; never waits. The thread notices within `IDLE_POLL` seconds,
        or once a running callback returns, then ends IDLE and logs out itself."""
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        """Waits for the thread to end, at most `timeout` seconds; returns at once if it never
        started."""
        if self._started:
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        """Whether the thread still runs."""
        return self._thread.is_alive()

    @property
    def connected(self) -> bool:
        """Whether the watch ever reached its folder."""
        return self._connected.is_set()

    @property
    def failure(self) -> MailError | None:
        """Why the watch ended on its own; None while it runs, or after a stop."""
        return self._failure

    # The watch's thread

    def _run(self) -> None:
        backoff = 0.0
        try:
            while not self._stop.is_set():
                client = self._client
                if client is None:
                    if self._connected.is_set():
                        if backoff:
                            _log.info(
                                "idle_reconnecting",
                                extra={
                                    "event": "idle_reconnecting",
                                    "account": self._account,
                                    "backoff": backoff,
                                },
                            )
                            if self._stop.wait(backoff):
                                break
                        backoff = min(
                            max(backoff * 2, RECONNECT_MIN_BACKOFF), RECONNECT_MAX_BACKOFF
                        )
                    if not self._connect():
                        return
                    continue
                try:
                    self._cycle(client)
                    backoff = 0.0
                except Exception as error:
                    if self._stop.is_set():
                        break
                    mapped = mail_error(error, self._account)
                    self._discard()
                    if mapped.code in _FATAL:
                        self._end(mapped)
                        return
                    _log.info(
                        "idle_disconnected",
                        extra={
                            "event": "idle_disconnected",
                            "account": self._account,
                            "mapped": mapped.code,
                        },
                    )
        finally:
            self._close()

    def _connect(self) -> bool:
        """Opens the connection and EXAMINEs the folder, then runs `on_connect`; False when the
        watch must end."""
        try:
            # Outside the slots, which idle-close: the module docstring says why.
            client = self._connections._open_unpooled(self._account)  # noqa: SLF001
        except Exception as error:
            return self._connect_failed(mail_error(error, self._account))
        try:
            offered = {bytes(cap).upper() for cap in client.capabilities()}
            if b"IDLE" not in offered:
                raise MailError("unsupported", "The server does not offer IDLE.")
            client.select_folder(self._path, readonly=True)
        except Exception as error:
            _drop(client)
            return self._connect_failed(mail_error(error, self._account))
        self._client = client
        if self._stop.is_set():
            return False
        self._connected.set()
        self._call(self._on_connect)
        return True

    def _connect_failed(self, error: MailError) -> bool:
        if self._stop.is_set():
            return False
        if not self._connected.is_set() or error.code in _FATAL:
            self._end(error)
            return False
        return True

    def _cycle(self, client: IMAPClient) -> None:
        """One IDLE until a response, `IDLE_TIMEOUT` or a stop, then `on_change` with what came,
        then a NOOP and `on_change` with what it brought."""
        client.idle()
        deadline = time.monotonic() + IDLE_TIMEOUT
        responses: list[tuple[object, ...]] = []
        while not responses and not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            responses = client.idle_check(min(IDLE_POLL, remaining))
            if not responses and _hung_up(client):
                # imapclient's idle_check answers a closed connection with no responses.
                raise MailError("unreachable")
        _text, late = client.idle_done()
        responses.extend(late)
        if self._stop.is_set():
            return
        if responses:
            change = IdleChange.of(responses)
            self._call(lambda: self._on_change(change))
        if self._stop.is_set():
            return
        _text, after = client.noop()
        if after and not self._stop.is_set():
            noticed = IdleChange.of(after)
            self._call(lambda: self._on_change(noticed))

    def _call(self, callback: Callable[[], None] | None) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception as error:
            # The host's code: its text may quote anything, so only its type, at any level.
            _log.warning(
                "idle_callback_failed",
                extra={
                    "event": "idle_callback_failed",
                    "account": self._account,
                    "error_type": type(error).__name__,
                },
            )

    def _end(self, error: MailError) -> None:
        self._failure = error
        _log.warning(
            "idle_ended",
            extra={"event": "idle_ended", "account": self._account, "mapped": error.code},
        )

    def _discard(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            _drop(client)

    def _close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            _log_out(client)


def _hung_up(client: IMAPClient) -> bool:
    """Whether the server closed the connection: the socket reads as ready, and a peek below TLS
    finds its end."""
    try:
        sock = client.socket()
        poller = select.poll()
        poller.register(sock.fileno(), select.POLLIN)
        if not poller.poll(0):
            return False
        # Ready, so the peek does not wait; the TLS layer never sees it.
        return socket.socket.recv(sock, 1, socket.MSG_PEEK) == b""
    except OSError, ValueError:
        return True
