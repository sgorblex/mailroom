import contextlib
import logging
import socket
import threading
from typing import TYPE_CHECKING

import pytest
from imapclient.exceptions import IMAPClientError

from mailroom_core.mail import (
    Capabilities,
    Folder,
    MailboxContext,
    MailConnections,
    MailError,
    Reachability,
    Timeouts,
    retriable,
)
from mailroom_core.mail.imap import folders_from
from mailroom_core.testing import (
    ImapTestServer,
    connections_for,
    create_folders,
    folder_contents,
    mime,
    raw_client,
    seed,
    test_account,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.mark.parametrize(
    ("stage", "code", "read_only", "expected"),
    [
        ("lock", "unreachable", False, True),
        ("lock", "unreachable", True, True),
        ("lock", "server_error", False, False),
        ("lock", "auth_failed", True, False),
        ("fn", "unreachable", True, True),
        ("fn", "server_error", True, True),
        ("fn", "busy", True, False),
        # A write that may have reached the server is never repeated.
        ("fn", "unreachable", False, False),
        ("fn", "server_error", False, False),
    ],
)
def test_retriable(stage, code, read_only, expected) -> None:
    assert retriable(stage, code, read_only) is expected


def test_folder_roles_and_order() -> None:
    listing = [
        ((b"\\HasNoChildren",), b"/", "Projects"),
        ((b"\\HasNoChildren", b"\\Trash"), b"/", "Trash"),
        ((b"\\HasChildren",), b"/", "INBOX"),
        ((b"\\HasNoChildren",), b"/", "INBOX/Clients"),
        ((b"\\HasNoChildren",), b"/", "Sent Messages"),
        ((b"\\HasNoChildren", b"\\Sent"), b"/", "Inviata"),
        ((b"\\HasNoChildren",), b"/", "Posta indesiderata"),
        ((b"\\HasNoChildren",), b"/", "Bozze"),
        ((b"\\NonExistent",), b"/", "Archive"),
        ((b"\\HasNoChildren",), None, "Archivio"),
    ]
    folders = folders_from(listing)
    assert [(f.path, f.role) for f in folders] == [
        ("INBOX", "inbox"),
        ("Inviata", "sent"),  # the flag beats the name "Sent Messages"
        ("Bozze", "drafts"),
        ("Archivio", "archive"),  # a phantom "Archive" claims nothing by its name
        ("Posta indesiderata", "junk"),
        ("Trash", "trash"),
        ("Archive", None),
        ("INBOX/Clients", None),
        ("Projects", None),
        ("Sent Messages", None),
    ]
    archive = next(f for f in folders if f.path == "Archive")
    assert archive.flags == ("\\NonExistent", "\\Noselect")
    assert not archive.selectable
    assert folders[0].delimiter == "/"
    assert next(f for f in folders if f.path == "Archivio").delimiter is None


def test_mime_builds_a_multipart_message() -> None:
    from email import message_from_string, policy

    from mailroom_core.testing import MimeAttachment

    raw = mime(
        "Ciao",
        text="plain",
        message_id="<m1@example.org>",
        in_reply_to="<m0@example.org>",
        attachments=[MimeAttachment("a.pdf", "application/pdf", b"%PDF")],
    )
    assert "\r\n" in raw
    assert "\n" not in raw.replace("\r\n", "")
    message = message_from_string(raw, policy=policy.default)
    assert message["Message-ID"] == "<m1@example.org>"
    assert message["In-Reply-To"] == "<m0@example.org>"
    parts = list(message.iter_parts())  # type: ignore[attr-defined]
    assert parts[0].get_content() == "plain"
    assert parts[1].get_filename() == "a.pdf"
    assert parts[1].get_content() == b"%PDF"


# Against Dovecot


@pytest.fixture
def opened() -> Iterator[list[MailConnections]]:
    made: list[MailConnections] = []
    yield made
    for connections in made:
        connections.close_all()


@pytest.fixture
def connections(imap_server: ImapTestServer, opened: list[MailConnections]):
    def make(*accounts, passwords=None, **options) -> MailConnections:
        made = connections_for(imap_server, accounts, passwords, **options)
        opened.append(made)
        return made

    return make


def _client_of(connections: MailConnections, path: str = "INBOX", *, read_only: bool = True):
    return connections.with_mailbox("a", path, read_only, lambda ctx: ctx.client)


@pytest.mark.integration
def test_opens_lazily_and_reports_capabilities(imap_server, connections) -> None:
    mail = connections(test_account("a", imap_server.unique_user("caps"), imap_server))
    assert mail.capabilities("a") == Capabilities(
        gmail=False, thread=True, move=True, uidplus=True, condstore=True, special_use=True
    )


@pytest.mark.integration
def test_examines_read_only_and_releases_the_lock_on_error(imap_server, connections) -> None:
    user = imap_server.unique_user("lock")
    uid = seed(imap_server, user, "INBOX", mime("one"))
    mail = connections(test_account("a", user, imap_server), timeouts=Timeouts(lock=1))

    def fail(_ctx: MailboxContext) -> None:
        raise MailError("not_found")

    with pytest.raises(MailError) as caught:
        mail.with_mailbox("a", "INBOX", True, fail)
    assert caught.value.code == "not_found"

    def mark_seen(ctx: MailboxContext) -> MailboxContext:
        ctx.client.add_flags([uid], [b"\\Seen"])
        return ctx

    seen = mail.with_mailbox("a", "INBOX", True, mark_seen)
    assert (seen.path, seen.read_only, seen.exists) == ("INBOX", True, 1)
    assert seen.uid_validity > 0
    assert seen.uid_next == uid + 1
    # STORE under EXAMINE answers OK and changes nothing.
    [message] = folder_contents(imap_server, user, "INBOX")
    assert "\\Seen" not in message.flags

    mail.with_mailbox("a", "INBOX", False, mark_seen)
    [message] = folder_contents(imap_server, user, "INBOX")
    assert "\\Seen" in message.flags


@pytest.mark.integration
@pytest.mark.parametrize("read_only", [True, False])
def test_reopens_a_connection_that_died(imap_server, connections, read_only) -> None:
    mail = connections(test_account("a", imap_server.unique_user("reopen"), imap_server))
    first = _client_of(mail, read_only=read_only)
    first.shutdown()
    second = _client_of(mail, read_only=read_only)
    assert second is not first
    assert mail.with_mailbox("a", "INBOX", read_only, lambda ctx: ctx.client.noop()) is not None


@pytest.mark.integration
def test_recovers_when_another_client_deletes_and_recreates_the_folder(
    imap_server, connections
) -> None:
    user = imap_server.unique_user("dropped")
    mail = connections(test_account("a", user, imap_server))
    create_folders(imap_server, user, "Projects")
    mail.with_mailbox("a", "Projects", True, lambda _ctx: None)
    with raw_client(imap_server, user) as setup:
        setup.delete_folder("Projects")
        setup.create_folder("Projects")
    seed(imap_server, user, "Projects", mime("after"))

    def subjects(ctx: MailboxContext) -> list[bytes]:
        fetched = ctx.client.fetch(ctx.client.search("ALL"), ["ENVELOPE"])
        return [data[b"ENVELOPE"].subject for data in fetched.values()]

    assert mail.with_mailbox("a", "Projects", True, subjects) == [b"after"]


@pytest.mark.integration
def test_retries_a_read_but_never_a_write(imap_server, connections) -> None:
    mail = connections(test_account("a", imap_server.unique_user("retry"), imap_server))
    calls = []

    def fail(_ctx: MailboxContext) -> None:
        calls.append(1)
        raise IMAPClientError("fetch failed: something")

    with pytest.raises(MailError) as caught:
        mail.with_mailbox("a", "INBOX", True, fail)
    assert (caught.value.code, len(calls)) == ("server_error", 2)
    calls.clear()
    with pytest.raises(MailError):
        mail.with_mailbox("a", "INBOX", False, fail)
    assert len(calls) == 1

    def bug(_ctx: MailboxContext) -> None:
        calls.append(1)
        raise KeyError(b"BODY[1]")

    calls.clear()
    with pytest.raises(MailError) as caught:
        mail.with_mailbox("a", "INBOX", True, bug)
    assert (caught.value.code, len(calls)) == ("server_error", 1)


@pytest.mark.integration
def test_retries_on_a_new_connection_when_every_idle_one_died(imap_server, connections) -> None:
    mail = connections(test_account("a", imap_server.unique_user("both"), imap_server), pool_size=2)
    dead, _ = _overlapping(mail)
    for client in dead:
        client.shutdown()  # type: ignore[attr-defined]
    assert _client_of(mail) not in dead
    clients, _ = _overlapping(mail)
    assert not set(map(id, clients)) & set(map(id, dead))


@pytest.mark.integration
def test_does_not_reuse_a_connection_idle_for_too_long(imap_server, connections) -> None:
    user = imap_server.unique_user("idle")
    fresh = connections(test_account("a", user, imap_server), timeouts=Timeouts(idle=0))
    assert _client_of(fresh) is not _client_of(fresh)
    kept = connections(test_account("a", user, imap_server))
    assert _client_of(kept) is _client_of(kept)


@pytest.mark.integration
def test_drops_a_connection_left_read_only(imap_server, connections) -> None:
    import imaplib

    mail = connections(test_account("a", imap_server.unique_user("ro"), imap_server))
    first = _client_of(mail, read_only=False)

    def refused(_ctx: MailboxContext) -> None:
        raise imaplib.IMAP4.readonly("INBOX is not writable")

    with pytest.raises(MailError):
        mail.with_mailbox("a", "INBOX", False, refused)
    assert _client_of(mail) is not first


@pytest.mark.integration
def test_interrupt_cuts_a_blocked_call_short(imap_server, connections) -> None:
    import time

    mail = connections(test_account("a", imap_server.unique_user("cut"), imap_server))
    waiting = threading.Event()
    calls: list[int] = []
    outcome: list[BaseException] = []

    def wait_for_mail(ctx: MailboxContext) -> None:
        calls.append(1)
        ctx.client.idle()
        waiting.set()
        ctx.client.idle_check(timeout=30)
        ctx.client.idle_done()

    def run() -> None:
        try:
            mail.with_mailbox("a", "INBOX", True, wait_for_mail)
        except MailError as error:
            outcome.append(error)

    assert mail.interrupt("a") == 0
    started = time.monotonic()
    thread = threading.Thread(target=run)
    thread.start()
    assert waiting.wait(10)
    assert mail.interrupt("a") == 1
    thread.join(10)
    assert time.monotonic() - started < 10
    [error] = outcome
    assert isinstance(error, MailError)
    assert (error.code, len(calls)) == ("unreachable", 1)
    assert mail.with_mailbox("a", "INBOX", True, lambda ctx: ctx.exists) == 0
    assert mail.interrupt("unknown") == 0


@pytest.mark.integration
def test_interrupt_spares_the_accounts_idle_connections(imap_server, connections) -> None:
    mail = connections(
        test_account("a", imap_server.unique_user("spare"), imap_server), pool_size=2
    )
    pooled, _ = _overlapping(mail)
    waiting = threading.Event()
    held: list[object] = []

    def wait_for_mail(ctx: MailboxContext) -> None:
        held.append(ctx.client)
        ctx.client.idle()
        waiting.set()
        ctx.client.idle_check(timeout=30)

    def run() -> None:
        with contextlib.suppress(MailError):
            mail.with_mailbox("a", "INBOX", True, wait_for_mail)

    thread = threading.Thread(target=run)
    thread.start()
    assert waiting.wait(10)
    assert mail.interrupt("a") == 1
    thread.join(10)
    [cut] = held
    [other] = [client for client in pooled if client is not cut]
    assert _client_of(mail) is other


@pytest.mark.integration
def test_interrupt_while_the_connection_opens(imap_server, opened) -> None:
    from mailroom_core.testing import unverified_context

    account = test_account("a", imap_server.unique_user("opening"), imap_server)
    lookups: list[str] = []
    opening, proceed = threading.Event(), threading.Event()

    def lookup(account_id: str):
        lookups.append(account_id)
        if len(lookups) == 2:  # the first creates the account's state, the second opens
            opening.set()
            proceed.wait(10)
        return account

    mail = MailConnections(
        lookup, lambda _id: imap_server.password, ssl_context=unverified_context()
    )
    opened.append(mail)
    ran: list[object] = []
    outcome: list[MailError] = []

    def run() -> None:
        try:
            mail.with_mailbox("a", "INBOX", True, lambda ctx: ran.append(ctx.client))
        except MailError as error:
            outcome.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert opening.wait(10)
    assert mail.interrupt("a") == 0  # no connection to cut yet
    proceed.set()
    thread.join(10)
    [error] = outcome
    assert (error.code, ran) == ("unreachable", [])
    assert mail.with_mailbox("a", "INBOX", True, lambda ctx: ctx.exists) == 0
    assert len(lookups) == 3  # the connection opened under the interrupt was not kept


@pytest.mark.integration
def test_a_close_during_a_probe_keeps_nothing_learnt_before_it(imap_server, opened) -> None:
    from mailroom_core.testing import unverified_context

    account = test_account("a", imap_server.unique_user("stale"), imap_server)
    lookups: list[str] = []
    close_at: set[int] = set()
    mail: list[MailConnections] = []

    def lookup(account_id: str):
        lookups.append(account_id)
        if len(lookups) in close_at:
            mail[0].close("a")
        return account

    mail.append(
        MailConnections(
            lookup,
            lambda _id: imap_server.password,
            ssl_context=unverified_context(),
            health_cache=60,
        )
    )
    opened.append(mail[0])

    close_at.add(2)  # while the first connection opens
    assert mail[0].capabilities("a").uidplus
    assert mail[0].capabilities("a").uidplus
    assert len(lookups) == 3
    assert mail[0].capabilities("a").uidplus
    assert len(lookups) == 3

    mail[0].close("a")
    close_at.add(4)
    assert mail[0].reachability("a") == Reachability(ok=True)
    assert mail[0].reachability("a") == Reachability(ok=True)
    assert len(lookups) == 5
    assert mail[0].reachability("a") == Reachability(ok=True)
    assert len(lookups) == 5


def _overlapping(mail: MailConnections) -> tuple[list[object], bool]:
    """Two concurrent calls: the connections they ran on, and whether they ran at once."""
    both = threading.Barrier(2, timeout=1)
    clients: list[object] = []
    overlapped = []

    def job() -> None:
        def fn(ctx: MailboxContext) -> None:
            clients.append(ctx.client)
            with contextlib.suppress(threading.BrokenBarrierError):
                both.wait()
                overlapped.append(True)

        mail.with_mailbox("a", "INBOX", True, fn)

    threads = [threading.Thread(target=job) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return clients, len(overlapped) == 2


@pytest.mark.integration
def test_pool_of_one_serialises_callers_on_one_connection(imap_server, connections) -> None:
    mail = connections(test_account("a", imap_server.unique_user("race"), imap_server))
    clients, overlapped = _overlapping(mail)
    assert clients[0] is clients[1]
    assert not overlapped


@pytest.mark.integration
def test_larger_pool_runs_callers_side_by_side(imap_server, connections) -> None:
    user = imap_server.unique_user("pool")
    mail = connections(test_account("a", user, imap_server), pool_size=2)
    clients, overlapped = _overlapping(mail)
    assert clients[0] is not clients[1]
    assert overlapped


@pytest.mark.integration
def test_busy_when_no_connection_frees_up(imap_server, connections) -> None:
    mail = connections(
        test_account("a", imap_server.unique_user("busy"), imap_server),
        timeouts=Timeouts(lock=0.2),
    )
    holding, release = threading.Event(), threading.Event()

    def hold(_ctx: MailboxContext) -> None:
        holding.set()
        release.wait(5)

    holder = threading.Thread(target=mail.with_mailbox, args=("a", "INBOX", True, hold))
    holder.start()
    try:
        assert holding.wait(5)
        with pytest.raises(MailError) as caught:
            mail.with_mailbox("a", "INBOX", True, lambda _ctx: None)
        assert caught.value.code == "busy"
    finally:
        release.set()
        holder.join()
    mail.with_mailbox("a", "INBOX", True, lambda _ctx: None)


@pytest.mark.integration
def test_close_drops_idle_connections_and_busy_ones_once_released(imap_server, connections) -> None:
    mail = connections(test_account("a", imap_server.unique_user("close"), imap_server))
    first = _client_of(mail)
    mail.close("a")
    second = _client_of(mail)
    assert second is not first

    def close_meanwhile(ctx: MailboxContext) -> object:
        mail.close("a")
        ctx.client.noop()  # still usable by the caller that holds it
        return ctx.client

    third = mail.with_mailbox("a", "INBOX", True, close_meanwhile)
    assert third is second
    assert _client_of(mail) is not third
    mail.close("unknown")


@pytest.mark.integration
def test_lists_folders_with_roles_and_counts(imap_server, connections) -> None:
    user = imap_server.unique_user("folders")
    create_folders(imap_server, user, "Projects", "Projects/2026", "Zeta")
    seed(imap_server, user, "INBOX", mime("one"))
    seed(imap_server, user, "INBOX", mime("two"), [r"\Seen"])
    mail = connections(test_account("a", user, imap_server))

    folders = mail.folders("a")
    assert [(f.path, f.role) for f in folders] == [
        ("INBOX", "inbox"),
        ("Sent", "sent"),
        ("Drafts", "drafts"),
        ("Junk", "junk"),
        ("Trash", "trash"),
        ("Projects", None),
        ("Projects/2026", None),
        ("Zeta", None),
    ]
    assert all(f.messages is None for f in folders)
    assert "\\HasChildren" in folders[5].flags
    assert folders[5].delimiter == "/"

    inbox = mail.folders("a", with_status=True)[0]
    assert (inbox.messages, inbox.unseen) == (2, 1)
    assert inbox.uid_validity is not None
    assert inbox.uid_next == 3

    assert mail.special_folder("a", "trash") == Folder(
        path="Trash", delimiter="/", role="trash", flags=folders[4].flags
    )
    with pytest.raises(MailError) as caught:
        mail.special_folder("a", "archive")
    assert (
        str(caught.value)
        == "The mail server does not support this operation. a has no Archive folder."
    )


@pytest.mark.integration
def test_folder_names_round_trip_in_utf7(imap_server, connections) -> None:
    user = imap_server.unique_user("utf7")
    create_folders(imap_server, user, "Città")
    seed(imap_server, user, "Città", mime("qui"))
    mail = connections(test_account("a", user, imap_server))
    assert "Città" in [f.path for f in mail.folders("a")]
    assert mail.with_mailbox("a", "Città", True, lambda ctx: ctx.exists) == 1


# Dovecot delays every failed login by about 2 s.
@pytest.mark.integration
def test_maps_failures_to_fixed_messages(imap_server, connections) -> None:
    mail = connections(
        test_account("a", imap_server.unique_user("map"), imap_server),
        test_account("wrong", imap_server.unique_user("wrong"), imap_server),
        test_account(
            "down", "x", imap_server, imap={"host": "127.0.0.1", "port": 9, "tls": "implicit"}
        ),
        passwords={"wrong": "not-the-password"},
    )
    with pytest.raises(MailError) as caught:
        mail.with_mailbox("a", "Nope", True, lambda _ctx: None)
    assert caught.value.code == "mailbox_missing"
    with pytest.raises(MailError) as caught:
        mail.with_mailbox("wrong", "INBOX", True, lambda _ctx: None)
    assert caught.value.code == "auth_failed"
    assert str(caught.value) == "The mail server rejected the login for this account."
    with pytest.raises(MailError) as caught:
        mail.folders("down")
    assert caught.value.code == "unreachable"
    with pytest.raises(MailError) as caught:
        mail.capabilities("nobody")
    assert str(caught.value) == 'Invalid argument. Unknown account "nobody".'
    assert mail.reachability("a") == Reachability(ok=True)
    assert mail.reachability("wrong") == Reachability(ok=False, code="auth_failed")
    assert mail.reachability("nobody") == Reachability(ok=False, code="invalid_argument")


@pytest.mark.integration
def test_logs_in_with_a_non_ascii_password_and_never_logs_it(
    imap_server, connections, caplog: pytest.LogCaptureFixture
) -> None:
    mail = connections(
        test_account("a", imap_server.unique_user("utf8"), imap_server),
        passwords={"a": "perché-2026"},
    )
    with (
        caplog.at_level(logging.DEBUG, logger="mailroom_core.mail"),
        pytest.raises(MailError) as caught,
    ):
        mail.folders("a")
    # Sent as UTF-8 by AUTHENTICATE PLAIN, and refused as a wrong password, not mis-encoded,
    # nor by the ASCII-only refusal that LOGIN would need, which says why in its detail.
    assert caught.value.code == "auth_failed"
    assert caught.value.detail is None
    assert "é" not in caplog.text
    assert all("error" not in record.__dict__ for record in caplog.records)


@pytest.mark.integration
def test_reachability_never_raises_and_a_busy_account_is_not_down(imap_server, opened) -> None:
    from mailroom_core.testing import unverified_context

    account = test_account("a", imap_server.unique_user("health"), imap_server)
    mail = MailConnections(
        {"a": account, "nopass": account}.get,
        {"a": imap_server.password}.get,
        ssl_context=unverified_context(),
        timeouts=Timeouts(lock=30),
    )
    opened.append(mail)
    assert mail.reachability("nopass") == Reachability(ok=False, code="server_error")
    holding, release = threading.Event(), threading.Event()

    def hold(_ctx: MailboxContext) -> None:
        holding.set()
        release.wait(10)

    holder = threading.Thread(target=mail.with_mailbox, args=("a", "INBOX", True, hold))
    holder.start()
    try:
        assert holding.wait(10)
        assert mail.reachability("a") == Reachability(ok=True)
    finally:
        release.set()
        holder.join()


def test_a_probe_gives_up_on_a_silent_server_quickly(opened) -> None:
    import time

    from mailroom_core.testing import unverified_context

    with socket.create_server(("127.0.0.1", 0)) as silent:
        server = ImapTestServer("127.0.0.1", silent.getsockname()[1])
        account = test_account("a", "someone", server)
        mail = MailConnections(
            {"a": account}.get,
            lambda _id: "pass",
            ssl_context=unverified_context(),
            timeouts=Timeouts(probe=0.5),
        )
        opened.append(mail)
        started = time.monotonic()
        assert mail.reachability("a") == Reachability(ok=False, code="timeout")
        # One attempt, bounded by the probe timeout rather than the 15 s connect one.
        assert time.monotonic() - started < 0.9


@pytest.mark.integration
def test_verifies_certificates_by_default(imap_server, opened) -> None:
    account = test_account("a", imap_server.unique_user("verify"), imap_server)
    mail = MailConnections({"a": account}.get, lambda _id: imap_server.password)
    opened.append(mail)
    assert mail.reachability("a") == Reachability(ok=False, code="unreachable")


@pytest.mark.integration
def test_caches_reachability(imap_server, opened) -> None:
    from mailroom_core.testing import unverified_context

    down = test_account(
        "a", "x", imap_server, imap={"host": "127.0.0.1", "port": 9, "tls": "implicit"}
    )
    accounts = {"a": down}
    mail = MailConnections(
        accounts.get, lambda _id: "pass", ssl_context=unverified_context(), health_cache=60
    )
    opened.append(mail)
    assert mail.reachability("a").code == "unreachable"
    accounts["a"] = test_account("a", imap_server.unique_user("cache"), imap_server)
    assert mail.reachability("a").code == "unreachable"
    mail.close("a")  # forgets the cached answer, and the account is looked up again
    assert mail.reachability("a") == Reachability(ok=True)


@contextlib.contextmanager
def _starttls_proxy(
    server: ImapTestServer,
    *,
    capability: bytes = b"IMAP4rev1 STARTTLS LOGINDISABLED",
    refuse: bool = False,
    received: list[bytes] | None = None,
) -> Iterator[int]:
    """A plain-text IMAP front that answers up to STARTTLS, then pipes the connection to the
    Dovecot implicit-TLS port, which so handles the TLS the client starts. `refuse` answers
    STARTTLS with NO instead; `received` collects the plain-text lines the client sent."""
    listener = socket.create_server(("127.0.0.1", 0))
    lines = received if received is not None else []

    def pipe(source: socket.socket, target: socket.socket) -> None:
        with contextlib.suppress(OSError):
            while data := source.recv(65536):
                target.sendall(data)
        with contextlib.suppress(OSError):
            target.shutdown(socket.SHUT_WR)

    def serve() -> None:
        with contextlib.suppress(OSError):
            client, _ = listener.accept()
            reader = client.makefile("rb")
            client.sendall(b"* OK [CAPABILITY " + capability + b"] proxy ready\r\n")
            while line := reader.readline():
                lines.append(line)
                tag, command = line.split()[:2]
                if command.upper() == b"CAPABILITY":
                    client.sendall(b"* CAPABILITY " + capability + b"\r\n" + tag + b" OK done\r\n")
                elif command.upper() == b"STARTTLS" and not refuse:
                    client.sendall(tag + b" OK Begin TLS negotiation now\r\n")
                    break
                elif command.upper() == b"STARTTLS":
                    client.sendall(tag + b" NO TLS unavailable\r\n")
                else:
                    client.sendall(tag + b" BAD STARTTLS first\r\n")
            else:
                return
            upstream = socket.create_connection((server.host, server.port))
            threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()
            pipe(client, upstream)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()


@pytest.mark.integration
def test_starttls(imap_server, connections) -> None:
    user = imap_server.unique_user("starttls")
    seed(imap_server, user, "INBOX", mime("over starttls"))
    with _starttls_proxy(imap_server) as port:
        mail = connections(
            test_account(
                "a", user, imap_server, imap={"host": "127.0.0.1", "port": port, "tls": "starttls"}
            )
        )
        assert mail.with_mailbox("a", "INBOX", True, lambda ctx: ctx.exists) == 1
        assert mail.capabilities("a").uidplus  # as announced after TLS and the login


@pytest.mark.integration
@pytest.mark.parametrize(
    ("capability", "refuse"),
    [(b"IMAP4rev1 AUTH=PLAIN", False), (b"IMAP4rev1 STARTTLS AUTH=PLAIN", True)],
    ids=["not-offered", "refused"],
)
def test_never_logs_in_in_clear_text_when_starttls_fails(
    imap_server, connections, capability, refuse
) -> None:
    received: list[bytes] = []
    with _starttls_proxy(
        imap_server, capability=capability, refuse=refuse, received=received
    ) as port:
        mail = connections(
            test_account(
                "a",
                "someone",
                imap_server,
                imap={"host": "127.0.0.1", "port": port, "tls": "starttls"},
            )
        )
        with pytest.raises(MailError):
            mail.folders("a")
    commands = [line.split()[1].upper() for line in received]
    assert (b"STARTTLS" in commands) is refuse
    assert not {b"LOGIN", b"AUTHENTICATE"} & set(commands)


@pytest.mark.integration
def test_seed_and_folder_contents(imap_server) -> None:
    user = imap_server.unique_user("seed")
    first = seed(imap_server, user, "INBOX", mime("=?utf-8?q?perch=C3=A9?="), [r"\Flagged"])
    second = seed(imap_server, user, "INBOX", mime("two"))
    contents = folder_contents(imap_server, user, "INBOX")
    assert [(m.uid, m.subject) for m in contents] == [(first, "perché"), (second, "two")]
    assert "\\Flagged" in contents[0].flags
    assert contents[1].source.startswith(b"From: Maria Rossi")
    assert folder_contents(imap_server, user, "Nope") == []
