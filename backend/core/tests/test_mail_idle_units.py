"""IdleChange from imapclient's parsed responses, and the watch's loop over a fake connection:
the NOOP's responses reach `on_change`, a failing callback does not end the watch, a server
without IDLE does, and so does a fatal reconnect, while a transient one backs off and goes on."""

import logging
import socket
import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from mailroom_core.mail import IdleChange, IdleWatch, MailError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mailroom_core.mail import MailConnections


def test_idle_change_reads_exists_expunge_and_flags() -> None:
    responses = [
        (b"OK", b"Still here"),
        (3, b"EXPUNGE"),
        (5, b"EXPUNGE"),
        (7, b"EXISTS"),
        (1, b"RECENT"),
        (2, b"FETCH", (b"UID", 12, b"FLAGS", (b"\\Seen", b"$Label"))),
        (4, b"FETCH", (b"FLAGS", b"\\Flagged")),
        (6, b"FETCH", (b"UID", 9)),
        (8, b"EXISTS"),
    ]
    change = IdleChange.of(responses)
    assert change.exists == 8
    assert change.new_mail
    assert change.expunged == (3, 5)
    assert change.flags == {2: frozenset({"\\Seen", "$Label"}), 4: frozenset({"\\Flagged"})}
    assert change.responses == tuple(responses)


def test_idle_change_reads_the_kind_in_any_case() -> None:
    change = IdleChange.of(
        [(3, b"expunge"), (7, b"Exists"), (2, b"fetch", (b"flags", (b"\\Seen",)))]
    )
    assert change.new_mail
    assert change.exists == 7
    assert change.expunged == (3,)
    assert change.flags == {2: frozenset({"\\Seen"})}


def test_idle_change_without_exists_is_no_new_mail() -> None:
    change = IdleChange.of([(b"OK", b"Still here"), (2, b"EXPUNGE"), (b"BYE",), ()])
    assert not change.new_mail
    assert change.exists is None
    assert change.expunged == (2,)
    assert change.flags == {}
    assert IdleChange() == IdleChange.of([])


class FakeClient:
    """An IDLE connection whose IDLE and NOOP responses come from scripts."""

    def __init__(
        self,
        idle: list[list[tuple[object, ...]]],
        noop: list[list[tuple[object, ...]]],
        capabilities: tuple[bytes, ...] = (b"IMAP4REV1", b"IDLE"),
    ) -> None:
        self._idle = idle
        self._noop = noop
        self._capabilities = capabilities
        self._socket, self._peer = socket.socketpair()
        self.calls: list[str] = []

    def capabilities(self) -> tuple[bytes, ...]:
        return self._capabilities

    def select_folder(self, path: str, readonly: bool) -> dict[bytes, object]:
        self.calls.append(f"examine {path}" if readonly else f"select {path}")
        return {b"EXISTS": 0}

    def idle(self) -> None:
        self.calls.append("idle")

    def idle_check(self, timeout: float) -> list[tuple[object, ...]]:
        if self._idle:
            return self._idle.pop(0)
        time.sleep(min(timeout, 0.01))
        return []

    def idle_done(self) -> tuple[bytes, list[tuple[object, ...]]]:
        self.calls.append("done")
        return b"Idle completed", []

    def noop(self) -> tuple[bytes, list[tuple[object, ...]]]:
        self.calls.append("noop")
        return b"NOOP completed", self._noop.pop(0) if self._noop else []

    def socket(self) -> socket.socket:
        return self._socket

    def logout(self) -> None:
        self.calls.append("logout")
        self.shutdown()

    def shutdown(self) -> None:
        self._socket.close()
        self._peer.close()


class CutClient(FakeClient):
    """A connection the server cuts as soon as the watch starts its IDLE."""

    def idle(self) -> None:
        self.calls.append("idle")
        raise OSError("connection reset")


@pytest.fixture
def watches() -> Iterator[list[IdleWatch]]:
    made: list[IdleWatch] = []
    yield made
    for watch in made:
        watch.stop()
        watch.join(5)


def _watch(
    client: FakeClient | list[FakeClient | Exception],
    watches: list[IdleWatch],
    **callbacks: object,
) -> IdleWatch:
    """A started watch whose connections open as `client`, or as each of a list in turn (an
    exception is raised instead)."""
    opens = client if isinstance(client, list) else None

    def open_unpooled(_account_id: str) -> FakeClient:
        if opens is None:
            return cast("FakeClient", client)
        opened = opens.pop(0)
        if isinstance(opened, Exception):
            raise opened
        return opened

    connections = cast("MailConnections", SimpleNamespace(_open_unpooled=open_unpooled))
    watch = IdleWatch(connections, "acc", "INBOX", **callbacks)  # type: ignore[arg-type]
    watches.append(watch)
    return watch.start()


def test_hands_the_noop_responses_to_on_change(watches) -> None:
    client = FakeClient(idle=[[(1, b"EXISTS")]], noop=[[(2, b"EXISTS")]])
    changes: list[IdleChange] = []
    got_both = threading.Event()

    def on_change(change: IdleChange) -> None:
        changes.append(change)
        if len(changes) == 2:
            got_both.set()

    watch = _watch(client, watches, on_change=on_change)
    assert got_both.wait(5)
    assert [change.exists for change in changes] == [1, 2]
    watch.stop()
    watch.join(5)
    assert not watch.alive
    assert client.calls[:4] == ["examine INBOX", "idle", "done", "noop"]
    assert client.calls[-1] == "logout"


def test_a_failing_callback_does_not_end_the_watch(watches, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="mailroom_core.mail")
    client = FakeClient(idle=[[(1, b"EXISTS")], [(2, b"EXISTS")]], noop=[])
    seen: list[int | None] = []
    second = threading.Event()

    def on_change(change: IdleChange) -> None:
        seen.append(change.exists)
        if len(seen) == 1:
            raise RuntimeError("Subject: a secret")
        second.set()

    def on_connect() -> None:
        raise ValueError("Subject: a secret")

    watch = _watch(client, watches, on_change=on_change, on_connect=on_connect)
    assert second.wait(5)
    assert seen == [1, 2]
    assert watch.alive
    assert watch.connected
    failures = [r for r in caplog.records if r.getMessage() == "idle_callback_failed"]
    assert [r.error_type for r in failures] == ["ValueError", "RuntimeError"]  # type: ignore[attr-defined]
    assert "a secret" not in caplog.text


def test_ends_unsupported_on_a_server_without_idle(watches) -> None:
    client = FakeClient(idle=[], noop=[], capabilities=(b"IMAP4REV1",))
    connects: list[None] = []
    watch = _watch(
        client, watches, on_change=lambda _c: None, on_connect=lambda: connects.append(None)
    )
    watch.join(5)
    assert not watch.alive
    assert isinstance(watch.failure, MailError)
    assert watch.failure.code == "unsupported"
    assert not watch.connected
    assert connects == []


def test_starts_once(watches) -> None:
    watch = _watch(FakeClient(idle=[], noop=[]), watches, on_change=lambda _c: None)
    with pytest.raises(RuntimeError):
        watch.start()
    watch.stop()
    watch.join(5)
    assert not watch.alive
    assert watch.failure is None


def test_a_reconnect_the_server_refuses_ends_the_watch(watches, caplog) -> None:
    caplog.set_level(logging.INFO, logger="mailroom_core.mail")
    connects: list[None] = []
    watch = _watch(
        [CutClient(idle=[], noop=[]), MailError("auth_failed")],
        watches,
        on_change=lambda _c: None,
        on_connect=lambda: connects.append(None),
    )
    watch.join(5)
    assert not watch.alive
    assert isinstance(watch.failure, MailError)
    assert watch.failure.code == "auth_failed"
    assert watch.connected
    assert connects == [None]
    [ended] = [r for r in caplog.records if r.getMessage() == "idle_ended"]
    assert (ended.account, ended.folder, ended.mapped) == ("acc", "INBOX", "auth_failed")  # type: ignore[attr-defined]


def test_a_transient_reconnect_failure_backs_off_and_goes_on(watches, monkeypatch) -> None:
    monkeypatch.setattr("mailroom_core.mail.idle.RECONNECT_MIN_BACKOFF", 0.01)
    connects: list[None] = []
    second = threading.Event()

    def on_connect() -> None:
        connects.append(None)
        if len(connects) == 2:
            second.set()

    watch = _watch(
        [CutClient(idle=[], noop=[]), MailError("unreachable"), FakeClient(idle=[], noop=[])],
        watches,
        on_change=lambda _c: None,
        on_connect=on_connect,
    )
    assert second.wait(5)
    assert watch.alive
    assert watch.failure is None
    watch.stop()
    watch.join(5)
    assert not watch.alive
    assert watch.failure is None
    assert len(connects) == 2
