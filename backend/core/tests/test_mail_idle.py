"""Watching a folder through IDLE, and the login test, against the test Dovecot: a connect runs
`on_connect`, an APPEND comes as new mail, a stop ends IDLE and logs out within a slice, a cut
connection is opened again, and the errors no reconnect mends end the watch."""

import logging
import socket
import threading
import time
from typing import TYPE_CHECKING

import pytest

from mailroom_core.mail import IdleChange, IdleWatch, MailConnections, MailError, Timeouts
from mailroom_core.testing import ImapTestServer, connections_for, mime, seed, test_account

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.integration


class Recorder:
    """The callbacks of one watch, and a way to wait for them."""

    def __init__(self) -> None:
        self.connects = 0
        self.changes: list[IdleChange] = []
        self._seen = threading.Condition()

    def on_connect(self) -> None:
        with self._seen:
            self.connects += 1
            self._seen.notify_all()

    def on_change(self, change: IdleChange) -> None:
        with self._seen:
            self.changes.append(change)
            self._seen.notify_all()

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 15.0) -> bool:
        with self._seen:
            return self._seen.wait_for(predicate, timeout)

    def new_mail(self) -> bool:
        return any(change.new_mail for change in self.changes)


@pytest.fixture
def opened() -> Iterator[list[MailConnections | IdleWatch]]:
    made: list[MailConnections | IdleWatch] = []
    yield made
    for thing in made:
        if isinstance(thing, IdleWatch):
            thing.stop()
            thing.join(10)
        else:
            thing.close_all()


@pytest.fixture
def connections(imap_server: ImapTestServer, opened: list[MailConnections | IdleWatch]):
    def make(user: str, **options: object) -> MailConnections:
        made = connections_for(imap_server, [test_account("acc", user, imap_server)], **options)  # type: ignore[arg-type]
        opened.append(made)
        return made

    return make


@pytest.fixture
def watch(opened: list[MailConnections | IdleWatch]):
    def start(connections: MailConnections, path: str, recorder: Recorder) -> IdleWatch:
        made = IdleWatch(
            connections,
            "acc",
            path,
            on_change=recorder.on_change,
            on_connect=recorder.on_connect,
            name="idle:test",
        )
        opened.append(made)
        return made.start()

    return start


def test_reports_new_mail_and_stops_within_a_slice(imap_server, connections, watch, caplog) -> None:
    user = imap_server.unique_user("idle")
    seed(imap_server, user, "INBOX", mime("before"))
    mail = connections(user, timeouts=Timeouts(lock=1))
    recorder = Recorder()
    watching = watch(mail, "INBOX", recorder)
    assert recorder.wait_for(lambda: recorder.connects == 1)
    assert watching.connected
    assert watching.alive
    # The watch's connection is not one of the pool's single slot.
    assert mail.with_mailbox("acc", "INBOX", True, lambda context: context.exists) == 1

    seed(imap_server, user, "INBOX", mime("arrived"))
    assert recorder.wait_for(recorder.new_mail)
    assert any(change.exists == 2 for change in recorder.changes)

    caplog.set_level(logging.DEBUG, logger="imapclient")
    started = time.monotonic()
    watching.stop()
    watching.join(5)
    assert time.monotonic() - started < 2
    assert not watching.alive
    assert watching.failure is None
    logouts = [r for r in caplog.records if r.getMessage() == "Logged out, connection closed"]
    assert len(logouts) == 1
    assert logouts[0].threadName == "idle:test"
    assert recorder.connects == 1


def test_opens_a_cut_connection_again(imap_server, connections, watch) -> None:
    user = imap_server.unique_user("idle-cut")
    recorder = Recorder()
    watching = watch(connections(user), "INBOX", recorder)
    assert recorder.wait_for(lambda: recorder.connects == 1)

    client = watching._client  # noqa: SLF001 - the watch's own connection, cut as a server would
    assert client is not None
    socket.socket.shutdown(client.socket(), socket.SHUT_RDWR)

    assert recorder.wait_for(lambda: recorder.connects == 2)
    assert watching.alive
    seed(imap_server, user, "INBOX", mime("after the cut"))
    assert recorder.wait_for(recorder.new_mail)
    assert watching.failure is None


@pytest.mark.parametrize(
    ("path", "passwords", "code"),
    [
        ("INBOX", {"acc": "wrong"}, "auth_failed"),
        ("Nowhere", None, "mailbox_missing"),
    ],
)
def test_ends_on_errors_no_reconnect_mends(
    imap_server, connections, watch, path, passwords, code
) -> None:
    recorder = Recorder()
    mail = connections(imap_server.unique_user("idle-fail"), passwords=passwords)
    watching = watch(mail, path, recorder)
    watching.join(15)
    assert not watching.alive
    assert isinstance(watching.failure, MailError)
    assert watching.failure.code == code
    assert not watching.connected
    assert recorder.connects == 0


def test_login(imap_server, opened) -> None:
    user = imap_server.unique_user("login")
    accounts = [
        test_account("ok", user, imap_server),
        test_account("wrong", user, imap_server),
        test_account(
            "down", user, imap_server, imap={"host": "127.0.0.1", "port": 9, "tls": "implicit"}
        ),
    ]
    mail = connections_for(imap_server, accounts, {"wrong": "nope"}, timeouts=Timeouts(probe=5))
    opened.append(mail)

    mail.test_login("ok")
    for account_id, code in (("wrong", "auth_failed"), ("down", "unreachable")):
        with pytest.raises(MailError) as caught:
            mail.test_login(account_id)
        assert caught.value.code == code
