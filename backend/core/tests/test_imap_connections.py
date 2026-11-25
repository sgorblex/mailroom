import contextlib
import imaplib
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
    Quota,
    QuotaUsage,
    Reachability,
    StructureCache,
    Timeouts,
    analyse_structure,
    retriable,
)
from mailroom_core.mail.imap import _announced, _capabilities, _keywords, _quota, folders_from
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


def test_a_structure_cache_keeps_the_most_recently_used() -> None:
    cache = StructureCache(2)
    one, two = analyse_structure(()), analyse_structure((b"text", b"plain"))
    cache.put((1, "INBOX", 7, 1), one)
    cache.put((1, "INBOX", 7, 2), two)
    assert cache.get((1, "INBOX", 7, 1)) is one  # now the most recently used
    cache.put((2, "INBOX", 7, 1), two)
    assert cache.get((1, "INBOX", 7, 2)) is None
    assert len(cache) == 2
    # Another UIDVALIDITY or folder is another message.
    assert cache.get((1, "INBOX", 8, 1)) is None
    assert cache.get((1, "Work", 7, 1)) is None
    cache.forget(1)
    assert cache.get((1, "INBOX", 7, 1)) is None
    assert cache.get((2, "INBOX", 7, 1)) is two
    with pytest.raises(ValueError, match="at least 1"):
        StructureCache(0)


def test_announcements_are_spent_once_and_others_left_alone() -> None:
    class Imaplib:
        def __init__(self, pending: dict[str, list[bytes]]) -> None:
            self.untagged_responses = pending

    class Client:
        def __init__(self, pending: dict[str, list[bytes]]) -> None:
            self._imap = Imaplib(pending)

    quiet = Client({"FLAGS": [b"(\\Seen)"], "READ-ONLY": [b""]})
    assert _announced(quiet) is False  # type: ignore[arg-type]
    for name in ("EXISTS", "EXPUNGE", "FETCH", "UIDVALIDITY", "BYE", "VANISHED"):
        pending = {name: [b"1"], "FLAGS": [b"()"]}
        client = Client(pending)
        assert _announced(client) is True  # type: ignore[arg-type]
        assert pending == {"FLAGS": [b"()"]}
        assert _announced(client) is False  # type: ignore[arg-type]


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
    return connections.with_mailbox(1, path, read_only, lambda ctx: ctx.client)


@pytest.mark.integration
def test_opens_lazily_and_reports_capabilities(imap_server, connections) -> None:
    mail = connections(test_account(1, imap_server.unique_user("caps"), imap_server))
    assert mail.capabilities(1) == Capabilities(
        gmail=False,
        thread=True,
        move=True,
        uidplus=True,
        condstore=True,
        special_use=True,
        quota=True,
    )


class _CapableClient:
    def __init__(self, *capabilities: str) -> None:
        self.offered = capabilities

    def has_capability(self, name: str) -> bool:
        return name in self.offered


@pytest.mark.parametrize(
    ("selected", "capabilities", "kept"),
    [
        ({b"PERMANENTFLAGS": (b"\\Seen", b"\\*")}, (), True),
        ({b"PERMANENTFLAGS": (b"\\Seen", b"\\Flagged")}, (), False),
        ({b"PERMANENTFLAGS": ()}, (), False),
        ({}, (), False),
        ({b"PERMANENTFLAGS": (b"\\*",)}, ("X-GM-EXT-1",), False),
    ],
)
def test_keywords_need_a_star_in_permanentflags_and_no_gmail(
    selected: dict[bytes, object], capabilities: tuple[str, ...], kept: bool
) -> None:
    assert _keywords(_CapableClient(*capabilities), selected) is kept  # type: ignore[arg-type]


@pytest.mark.integration
def test_reads_keyword_support_once_from_a_read_write_inbox(imap_server, connections) -> None:
    mail = connections(test_account(1, imap_server.unique_user("keywords"), imap_server))
    assert mail.with_mailbox(1, "INBOX", False, lambda context: context.keywords) is True
    assert mail.with_mailbox(1, "INBOX", True, lambda context: context.keywords) is False
    assert mail.keywords(1) is True
    selects: list[str] = []
    mail.with_mailbox = lambda *args: selects.append("again")  # type: ignore[method-assign]
    assert mail.keywords(1) is True
    assert selects == []


@pytest.mark.parametrize(
    ("raw", "offered"),
    [((b"IMAP4rev1", b"QUOTA"), True), ((b"QUOTA=RES-STORAGE", b"quota"), True), ((), False)],
)
def test_reads_the_quota_capability(raw: tuple[bytes, ...], offered: bool) -> None:
    assert _capabilities(raw).quota is offered


class _Imaplib:
    """imaplib's side of a GETQUOTAROOT: the answer's type and its QUOTAROOT and QUOTA lines."""

    def __init__(self, typ: str, roots: list[bytes | None], quotas: list[bytes | None]) -> None:
        self.answer = (typ, [roots, quotas])
        self.asked: list[str] = []

    def getquotaroot(self, mailbox: str) -> tuple[str, list[list[bytes | None]]]:
        self.asked.append(mailbox)
        return self.answer


class _QuotaClient:
    def __init__(
        self, roots: list[bytes | None], quotas: list[bytes | None], typ: str = "OK"
    ) -> None:
        self._imap = _Imaplib(typ, roots, quotas)


def _refused(text: bytes) -> _QuotaClient:
    """A NO: imaplib gives the tagged answer's text in place of both lists."""
    return _QuotaClient([text], [text], typ="NO")


def test_reads_inboxs_quota_in_bytes_from_its_own_roots() -> None:
    client = _QuotaClient(
        [b'INBOX "User quota" Shared'],
        [
            b"Elsewhere (STORAGE 1 2)",
            b'"User quota" (storage 10 1024 MESSAGE 3 100)',
            b"Shared (STORAGE 99 99)",
        ],
    )
    assert _quota(client) == Quota(  # type: ignore[arg-type]
        storage=QuotaUsage(used=10 * 1024, limit=1024 * 1024),
        messages=QuotaUsage(used=3, limit=100),
    )
    assert client._imap.asked == ["INBOX"]  # noqa: SLF001


def test_an_answer_without_a_quotaroot_line_counts_every_quota_line() -> None:
    # Mailu's Dovecot answers GETQUOTAROOT with no QUOTAROOT line; imaplib then gives [None].
    assert _quota(_QuotaClient([None], [None])) is None  # type: ignore[arg-type]
    client = _QuotaClient([None], [b'"User quota" (STORAGE 10 1024)'])
    assert _quota(client) == Quota(  # type: ignore[arg-type]
        storage=QuotaUsage(used=10 * 1024, limit=1024 * 1024), messages=None
    )


def test_a_quota_root_without_storage_or_message_limits_is_none() -> None:
    assert _quota(_QuotaClient([b"INBOX"], [None])) is None  # type: ignore[arg-type]
    unlimited = _QuotaClient([b'INBOX "User quota"'], [b'"User quota" (MAILBOX 1 5)'])
    assert _quota(unlimited) is None  # type: ignore[arg-type]


def test_a_no_to_getquotaroot_is_no_quota() -> None:
    assert _quota(_refused(b"[NONEXISTENT] No quota root")) is None  # type: ignore[arg-type]


def test_a_temporary_no_to_getquotaroot_is_raised() -> None:
    with pytest.raises(
        IMAPClientError, match=r"GETQUOTAROOT failed: \[UNAVAILABLE\] Quota backend down"
    ):
        _quota(_refused(b"[UNAVAILABLE] Quota backend down"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "error",
    [
        imaplib.IMAP4.error("GETQUOTAROOT command error: BAD [b'Unknown command']"),
        imaplib.IMAP4.abort("socket error: EOF"),
    ],
)
def test_other_getquotaroot_errors_are_raised(error: Exception) -> None:
    client = _QuotaClient([], [])

    def fail(_mailbox: str) -> None:
        raise error

    client._imap.getquotaroot = fail  # type: ignore[method-assign,assignment]  # noqa: SLF001
    with pytest.raises(type(error)):
        _quota(client)  # type: ignore[arg-type]


@pytest.mark.integration
def test_reports_the_quota_of_inbox(imap_server, connections) -> None:
    user = imap_server.unique_user("quota")
    seed(imap_server, user, "INBOX", mime("one"))
    mail = connections(test_account(1, user, imap_server))
    quota = mail.quota(1)
    assert quota is not None
    assert quota.storage is not None
    assert quota.storage.limit == 1024**3
    assert 0 < quota.storage.used < quota.storage.limit
    assert quota.messages == QuotaUsage(used=1, limit=100000)


@pytest.mark.integration
def test_examines_read_only_and_releases_the_lock_on_error(imap_server, connections) -> None:
    user = imap_server.unique_user("lock")
    uid = seed(imap_server, user, "INBOX", mime("one"))
    mail = connections(test_account(1, user, imap_server), timeouts=Timeouts(lock=1))

    def fail(_ctx: MailboxContext) -> None:
        raise MailError("not_found")

    with pytest.raises(MailError) as caught:
        mail.with_mailbox(1, "INBOX", True, fail)
    assert caught.value.code == "not_found"

    def mark_seen(ctx: MailboxContext) -> MailboxContext:
        ctx.client.add_flags([uid], [b"\\Seen"])
        return ctx

    seen = mail.with_mailbox(1, "INBOX", True, mark_seen)
    assert (seen.path, seen.read_only, seen.exists) == ("INBOX", True, 1)
    assert seen.uid_validity > 0
    assert seen.uid_next == uid + 1
    # STORE under EXAMINE answers OK and changes nothing.
    [message] = folder_contents(imap_server, user, "INBOX")
    assert "\\Seen" not in message.flags

    mail.with_mailbox(1, "INBOX", False, mark_seen)
    [message] = folder_contents(imap_server, user, "INBOX")
    assert "\\Seen" in message.flags


@pytest.mark.integration
@pytest.mark.parametrize("read_only", [True, False])
def test_reopens_a_connection_that_died(imap_server, connections, read_only) -> None:
    mail = connections(test_account(1, imap_server.unique_user("reopen"), imap_server))
    first = _client_of(mail, read_only=read_only)
    first.shutdown()
    second = _client_of(mail, read_only=read_only)
    assert second is not first
    assert mail.with_mailbox(1, "INBOX", read_only, lambda ctx: ctx.client.noop()) is not None


@pytest.mark.integration
def test_recovers_when_another_client_deletes_and_recreates_the_folder(
    imap_server, connections
) -> None:
    user = imap_server.unique_user("dropped")
    mail = connections(test_account(1, user, imap_server))
    create_folders(imap_server, user, "Projects")
    mail.with_mailbox(1, "Projects", True, lambda _ctx: None)
    with raw_client(imap_server, user) as setup:
        setup.delete_folder("Projects")
        setup.create_folder("Projects")
    seed(imap_server, user, "Projects", mime("after"))

    def subjects(ctx: MailboxContext) -> list[bytes]:
        fetched = ctx.client.fetch(ctx.client.search("ALL"), ["ENVELOPE"])
        return [data[b"ENVELOPE"].subject for data in fetched.values()]

    assert mail.with_mailbox(1, "Projects", True, subjects) == [b"after"]


@pytest.mark.integration
def test_retries_a_read_but_never_a_write(imap_server, connections) -> None:
    mail = connections(test_account(1, imap_server.unique_user("retry"), imap_server))
    calls = []

    def fail(_ctx: MailboxContext) -> None:
        calls.append(1)
        raise IMAPClientError("fetch failed: something")

    with pytest.raises(MailError) as caught:
        mail.with_mailbox(1, "INBOX", True, fail)
    assert (caught.value.code, len(calls)) == ("server_error", 2)
    calls.clear()
    with pytest.raises(MailError):
        mail.with_mailbox(1, "INBOX", False, fail)
    assert len(calls) == 1

    def bug(_ctx: MailboxContext) -> None:
        calls.append(1)
        raise KeyError(b"BODY[1]")

    calls.clear()
    with pytest.raises(MailError) as caught:
        mail.with_mailbox(1, "INBOX", True, bug)
    assert (caught.value.code, len(calls)) == ("server_error", 1)


@pytest.mark.integration
def test_retries_on_a_new_connection_when_every_idle_one_died(imap_server, connections) -> None:
    mail = connections(test_account(1, imap_server.unique_user("both"), imap_server), pool_size=2)
    dead, _ = _overlapping(mail)
    for client in dead:
        client.shutdown()  # type: ignore[attr-defined]
    assert _client_of(mail) not in dead
    clients, _ = _overlapping(mail)
    assert not set(map(id, clients)) & set(map(id, dead))


@pytest.mark.integration
def test_does_not_reuse_a_connection_idle_for_too_long(imap_server, connections) -> None:
    user = imap_server.unique_user("idle")
    fresh = connections(test_account(1, user, imap_server), timeouts=Timeouts(idle=0))
    assert _client_of(fresh) is not _client_of(fresh)
    kept = connections(test_account(1, user, imap_server))
    assert _client_of(kept) is _client_of(kept)


@pytest.mark.integration
def test_drops_a_connection_left_read_only(imap_server, connections) -> None:
    import imaplib

    mail = connections(test_account(1, imap_server.unique_user("ro"), imap_server))
    first = _client_of(mail, read_only=False)

    def refused(_ctx: MailboxContext) -> None:
        raise imaplib.IMAP4.readonly("INBOX is not writable")

    with pytest.raises(MailError):
        mail.with_mailbox(1, "INBOX", False, refused)
    assert _client_of(mail) is not first


@pytest.mark.integration
def test_interrupt_cuts_a_blocked_call_short(imap_server, connections) -> None:
    import time

    mail = connections(test_account(1, imap_server.unique_user("cut"), imap_server))
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
            mail.with_mailbox(1, "INBOX", True, wait_for_mail)
        except MailError as error:
            outcome.append(error)

    assert mail.interrupt(1) == 0
    started = time.monotonic()
    thread = threading.Thread(target=run)
    thread.start()
    assert waiting.wait(10)
    assert mail.interrupt(1) == 1
    thread.join(10)
    assert time.monotonic() - started < 10
    [error] = outcome
    assert isinstance(error, MailError)
    assert (error.code, len(calls)) == ("unreachable", 1)
    assert mail.with_mailbox(1, "INBOX", True, lambda ctx: ctx.exists) == 0
    assert mail.interrupt("unknown") == 0


@pytest.mark.integration
def test_interrupt_spares_the_accounts_idle_connections(imap_server, connections) -> None:
    mail = connections(test_account(1, imap_server.unique_user("spare"), imap_server), pool_size=2)
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
            mail.with_mailbox(1, "INBOX", True, wait_for_mail)

    thread = threading.Thread(target=run)
    thread.start()
    assert waiting.wait(10)
    assert mail.interrupt(1) == 1
    thread.join(10)
    [cut] = held
    [other] = [client for client in pooled if client is not cut]
    assert _client_of(mail) is other


@pytest.mark.integration
def test_interrupt_while_the_connection_opens(imap_server, opened) -> None:
    from mailroom_core.testing import unverified_context

    account = test_account(1, imap_server.unique_user("opening"), imap_server)
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
            mail.with_mailbox(1, "INBOX", True, lambda ctx: ran.append(ctx.client))
        except MailError as error:
            outcome.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert opening.wait(10)
    assert mail.interrupt(1) == 0  # no connection to cut yet
    proceed.set()
    thread.join(10)
    [error] = outcome
    assert (error.code, ran) == ("unreachable", [])
    assert mail.with_mailbox(1, "INBOX", True, lambda ctx: ctx.exists) == 0
    assert len(lookups) == 3  # the connection opened under the interrupt was not kept


@pytest.mark.integration
def test_a_close_during_a_probe_keeps_nothing_learnt_before_it(imap_server, opened) -> None:
    from mailroom_core.testing import unverified_context

    account = test_account(1, imap_server.unique_user("stale"), imap_server)
    lookups: list[str] = []
    close_at: set[int] = set()
    mail: list[MailConnections] = []

    def lookup(account_id: str):
        lookups.append(account_id)
        if len(lookups) in close_at:
            mail[0].close(1)
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
    assert mail[0].capabilities(1).uidplus
    assert mail[0].capabilities(1).uidplus
    assert len(lookups) == 3
    assert mail[0].capabilities(1).uidplus
    assert len(lookups) == 3

    mail[0].close(1)
    close_at.add(4)
    assert mail[0].reachability(1) == Reachability(ok=True)
    assert mail[0].reachability(1) == Reachability(ok=True)
    assert len(lookups) == 5
    assert mail[0].reachability(1) == Reachability(ok=True)
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

        mail.with_mailbox(1, "INBOX", True, fn)

    threads = [threading.Thread(target=job) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return clients, len(overlapped) == 2


@pytest.mark.integration
def test_pool_of_one_serialises_callers_on_one_connection(imap_server, connections) -> None:
    mail = connections(test_account(1, imap_server.unique_user("race"), imap_server))
    clients, overlapped = _overlapping(mail)
    assert clients[0] is clients[1]
    assert not overlapped


@pytest.mark.integration
def test_larger_pool_runs_callers_side_by_side(imap_server, connections) -> None:
    user = imap_server.unique_user("pool")
    mail = connections(test_account(1, user, imap_server), pool_size=2)
    clients, overlapped = _overlapping(mail)
    assert clients[0] is not clients[1]
    assert overlapped


@pytest.mark.integration
def test_busy_when_no_connection_frees_up(imap_server, connections) -> None:
    mail = connections(
        test_account(1, imap_server.unique_user("busy"), imap_server),
        timeouts=Timeouts(lock=0.2),
    )
    holding, release = threading.Event(), threading.Event()

    def hold(_ctx: MailboxContext) -> None:
        holding.set()
        release.wait(5)

    holder = threading.Thread(target=mail.with_mailbox, args=(1, "INBOX", True, hold))
    holder.start()
    try:
        assert holding.wait(5)
        with pytest.raises(MailError) as caught:
            mail.with_mailbox(1, "INBOX", True, lambda _ctx: None)
        assert caught.value.code == "busy"
    finally:
        release.set()
        holder.join()
    mail.with_mailbox(1, "INBOX", True, lambda _ctx: None)


@pytest.mark.integration
def test_close_drops_idle_connections_and_busy_ones_once_released(imap_server, connections) -> None:
    mail = connections(test_account(1, imap_server.unique_user("close"), imap_server))
    first = _client_of(mail)
    mail.close(1)
    second = _client_of(mail)
    assert second is not first

    def close_meanwhile(ctx: MailboxContext) -> object:
        mail.close(1)
        ctx.client.noop()  # still usable by the caller that holds it
        return ctx.client

    third = mail.with_mailbox(1, "INBOX", True, close_meanwhile)
    assert third is second
    assert _client_of(mail) is not third
    mail.close("unknown")


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The SELECT, EXAMINE and NOOP commands every connection sends, in order."""
    from imapclient import IMAPClient

    sent: list[str] = []
    select, noop = IMAPClient.select_folder, IMAPClient.noop

    def selecting(self: IMAPClient, folder: str, readonly: bool = False) -> object:
        sent.append(f"{'EXAMINE' if readonly else 'SELECT'} {folder}")
        return select(self, folder, readonly)

    def nooping(self: IMAPClient) -> object:
        sent.append("NOOP")
        return noop(self)

    monkeypatch.setattr(IMAPClient, "select_folder", selecting)
    monkeypatch.setattr(IMAPClient, "noop", nooping)
    return sent


@pytest.mark.integration
def test_a_read_reuses_the_folder_the_last_read_examined(
    imap_server, connections, commands
) -> None:
    user = imap_server.unique_user("reuse")
    create_folders(imap_server, user, "Projects")
    seed(imap_server, user, "INBOX", mime("one"))
    mail = connections(test_account(1, user, imap_server))
    commands.clear()

    first = mail.with_mailbox(1, "INBOX", True, lambda ctx: ctx)
    second = mail.with_mailbox(1, "INBOX", True, lambda ctx: ctx)
    assert commands == ["EXAMINE INBOX", "NOOP"]
    assert second == first
    assert (second.exists, second.uid_next) == (1, first.uid_next)

    commands.clear()
    mail.with_mailbox(1, "Projects", True, lambda _ctx: None)
    mail.with_mailbox(1, "INBOX", True, lambda _ctx: None)
    # A write selects; the read after it examines, never reusing a read-write selection.
    mail.with_mailbox(1, "INBOX", False, lambda _ctx: None)
    mail.with_mailbox(1, "INBOX", False, lambda _ctx: None)
    mail.with_mailbox(1, "INBOX", True, lambda _ctx: None)
    mail.with_mailbox(1, "INBOX", True, lambda _ctx: None, reselect=True)
    assert commands == [
        "EXAMINE Projects",
        "EXAMINE INBOX",
        "SELECT INBOX",
        "SELECT INBOX",
        "EXAMINE INBOX",
        "EXAMINE INBOX",
    ]

    # A read that failed leaves nothing to reuse; nor does a folder command's UNSELECT.
    commands.clear()
    with pytest.raises(MailError):
        mail.with_mailbox(1, "INBOX", True, _not_found)
    mail.with_mailbox(1, "INBOX", True, lambda _ctx: None)
    mail.with_connection(1, lambda _client: None)
    mail.with_mailbox(1, "INBOX", True, lambda _ctx: None)
    assert commands == ["NOOP", "EXAMINE INBOX", "EXAMINE INBOX"]


def _not_found(_ctx: MailboxContext) -> None:
    raise MailError("not_found")


@pytest.mark.integration
def test_the_changed_flags_and_index_reads_always_examine_afresh(
    imap_server, connections, commands
) -> None:
    user = imap_server.unique_user("afresh")
    seed(imap_server, user, "INBOX", mime("one"))
    mail = connections(test_account(1, user, imap_server))
    commands.clear()
    mail.with_mailbox(1, "INBOX", True, lambda _ctx: None)
    mail.folder_uids(1, "INBOX")
    mail.folder_flags(1, "INBOX")
    mail.changed_flags(1, "INBOX", 1)
    assert commands == ["EXAMINE INBOX"] * 4


@pytest.mark.integration
def test_examines_again_once_the_server_announces_a_change(
    imap_server, connections, commands
) -> None:
    user = imap_server.unique_user("announced")
    uid = seed(imap_server, user, "INBOX", mime("one"))
    mail = connections(test_account(1, user, imap_server))
    before = mail.with_mailbox(1, "INBOX", True, lambda ctx: ctx)

    # New mail, announced on the NOOP.
    added = seed(imap_server, user, "INBOX", mime("two"))
    commands.clear()
    after = mail.with_mailbox(1, "INBOX", True, lambda ctx: ctx)
    assert commands == ["NOOP", "EXAMINE INBOX"]
    assert (after.exists, after.uid_next) == (2, added + 1)

    # A flag set elsewhere, announced as a FETCH: HIGHESTMODSEQ moves on.
    with raw_client(imap_server, user) as other:
        other.select_folder("INBOX")
        other.add_flags([uid], [b"\\Flagged"])
    commands.clear()
    flagged = mail.with_mailbox(1, "INBOX", True, lambda ctx: ctx)
    assert commands == ["NOOP", "EXAMINE INBOX"]
    if before.highest_modseq is not None:
        assert flagged.highest_modseq is not None
        assert flagged.highest_modseq > before.highest_modseq

    # Mail that arrives while a call runs is announced inside its FETCH: kept for the next one.

    def arrive(ctx: MailboxContext) -> None:
        seed(imap_server, user, "INBOX", mime("three"))
        ctx.client.fetch([uid], ["FLAGS"])

    mail.with_mailbox(1, "INBOX", True, arrive)
    commands.clear()
    third = mail.with_mailbox(1, "INBOX", True, lambda ctx: ctx)
    assert commands == ["EXAMINE INBOX"]  # no NOOP needed to know
    assert third.exists == 3

    # An expunge elsewhere.
    with raw_client(imap_server, user) as other:
        other.select_folder("INBOX")
        other.delete_messages([uid])
        other.expunge()
    commands.clear()
    assert mail.with_mailbox(1, "INBOX", True, lambda ctx: ctx.exists) == 2
    assert commands == ["NOOP", "EXAMINE INBOX"]


@pytest.mark.integration
def test_prefers_the_connection_that_examined_the_folder(imap_server, connections) -> None:
    user = imap_server.unique_user("prefer")
    create_folders(imap_server, user, "Projects")
    mail = connections(test_account(1, user, imap_server), pool_size=2)
    both = threading.Barrier(2, timeout=5)
    clients: dict[str, object] = {}

    def job(path: str) -> None:
        def fn(ctx: MailboxContext) -> None:
            clients[path] = ctx.client
            both.wait()

        mail.with_mailbox(1, path, True, fn)

    threads = [threading.Thread(target=job, args=(path,)) for path in ("INBOX", "Projects")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert clients["INBOX"] is not clients["Projects"]
    for path in ("Projects", "INBOX", "Projects"):
        assert _client_of(mail, path) is clients[path]


@pytest.mark.integration
def test_lists_folders_with_roles_and_counts(imap_server, connections) -> None:
    user = imap_server.unique_user("folders")
    create_folders(imap_server, user, "Projects", "Projects/2026", "Zeta")
    seed(imap_server, user, "INBOX", mime("one"))
    seed(imap_server, user, "INBOX", mime("two"), [r"\Seen"])
    mail = connections(test_account(1, user, imap_server))

    folders = mail.folders(1)
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

    inbox = mail.folders(1, with_status=True)[0]
    assert (inbox.messages, inbox.unseen) == (2, 1)
    assert inbox.uid_validity is not None
    assert inbox.uid_next == 3

    assert mail.special_folder(1, "trash") == Folder(
        path="Trash", delimiter="/", role="trash", flags=folders[4].flags
    )
    with pytest.raises(MailError) as caught:
        mail.special_folder(1, "archive")
    assert (
        str(caught.value)
        == "The mail server does not support this operation. Account 1 has no Archive folder."
    )


@pytest.mark.integration
def test_folder_names_round_trip_in_utf7(imap_server, connections) -> None:
    user = imap_server.unique_user("utf7")
    create_folders(imap_server, user, "Città")
    seed(imap_server, user, "Città", mime("qui"))
    mail = connections(test_account(1, user, imap_server))
    assert "Città" in [f.path for f in mail.folders(1)]
    assert mail.with_mailbox(1, "Città", True, lambda ctx: ctx.exists) == 1


# Dovecot delays every failed login by about 2 s.
@pytest.mark.integration
def test_maps_failures_to_fixed_messages(imap_server, connections) -> None:
    mail = connections(
        test_account(1, imap_server.unique_user("map"), imap_server),
        test_account(2, imap_server.unique_user("wrong"), imap_server),
        test_account(3, "x", imap_server, imap={"host": "127.0.0.1", "port": 9, "tls": "implicit"}),
        passwords={2: "not-the-password"},
    )
    with pytest.raises(MailError) as caught:
        mail.with_mailbox(1, "Nope", True, lambda _ctx: None)
    assert caught.value.code == "mailbox_missing"
    with pytest.raises(MailError) as caught:
        mail.with_mailbox(2, "INBOX", True, lambda _ctx: None)
    assert caught.value.code == "auth_failed"
    assert str(caught.value) == "The mail server rejected the login for this account."
    with pytest.raises(MailError) as caught:
        mail.folders(3)
    assert caught.value.code == "unreachable"
    with pytest.raises(MailError) as caught:
        mail.capabilities(99)
    assert str(caught.value) == "Invalid argument. Unknown account 99."
    assert mail.reachability(1) == Reachability(ok=True)
    assert mail.reachability(2) == Reachability(ok=False, code="auth_failed")
    assert mail.reachability(99) == Reachability(ok=False, code="invalid_argument")


@pytest.mark.integration
def test_logs_in_with_a_non_ascii_password_and_never_logs_it(
    imap_server, connections, caplog: pytest.LogCaptureFixture
) -> None:
    mail = connections(
        test_account(1, imap_server.unique_user("utf8"), imap_server),
        passwords={1: "perché-2026"},
    )
    with (
        caplog.at_level(logging.DEBUG, logger="mailroom_core.mail"),
        pytest.raises(MailError) as caught,
    ):
        mail.folders(1)
    # Sent as UTF-8 by AUTHENTICATE PLAIN, and refused as a wrong password, not mis-encoded,
    # nor by the ASCII-only refusal that LOGIN would need, which says why in its detail.
    assert caught.value.code == "auth_failed"
    assert caught.value.detail is None
    assert "é" not in caplog.text
    assert all("error" not in record.__dict__ for record in caplog.records)


@pytest.mark.integration
def test_reachability_never_raises_and_a_busy_account_is_not_down(imap_server, opened) -> None:
    from mailroom_core.testing import unverified_context

    account = test_account(1, imap_server.unique_user("health"), imap_server)
    mail = MailConnections(
        {1: account, 2: account}.get,
        {1: imap_server.password}.get,
        ssl_context=unverified_context(),
        timeouts=Timeouts(lock=30),
    )
    opened.append(mail)
    assert mail.reachability(2) == Reachability(ok=False, code="server_error")
    holding, release = threading.Event(), threading.Event()

    def hold(_ctx: MailboxContext) -> None:
        holding.set()
        release.wait(10)

    holder = threading.Thread(target=mail.with_mailbox, args=(1, "INBOX", True, hold))
    holder.start()
    try:
        assert holding.wait(10)
        assert mail.reachability(1) == Reachability(ok=True)
    finally:
        release.set()
        holder.join()


def test_a_probe_gives_up_on_a_silent_server_quickly(opened) -> None:
    import time

    from mailroom_core.testing import unverified_context

    with socket.create_server(("127.0.0.1", 0)) as silent:
        server = ImapTestServer("127.0.0.1", silent.getsockname()[1])
        account = test_account(1, "someone", server)
        mail = MailConnections(
            {1: account}.get,
            lambda _id: "pass",
            ssl_context=unverified_context(),
            timeouts=Timeouts(probe=0.5),
        )
        opened.append(mail)
        started = time.monotonic()
        assert mail.reachability(1) == Reachability(ok=False, code="timeout")
        # One attempt, bounded by the probe timeout rather than the 15 s connect one.
        assert time.monotonic() - started < 0.9


@pytest.mark.integration
def test_verifies_certificates_by_default(imap_server, opened) -> None:
    account = test_account(1, imap_server.unique_user("verify"), imap_server)
    mail = MailConnections({1: account}.get, lambda _id: imap_server.password)
    opened.append(mail)
    assert mail.reachability(1) == Reachability(ok=False, code="unreachable")


@pytest.mark.integration
def test_caches_reachability(imap_server, opened) -> None:
    from mailroom_core.testing import unverified_context

    down = test_account(
        1, "x", imap_server, imap={"host": "127.0.0.1", "port": 9, "tls": "implicit"}
    )
    accounts = {1: down}
    mail = MailConnections(
        accounts.get, lambda _id: "pass", ssl_context=unverified_context(), health_cache=60
    )
    opened.append(mail)
    assert mail.reachability(1).code == "unreachable"
    accounts[1] = test_account(1, imap_server.unique_user("cache"), imap_server)
    assert mail.reachability(1).code == "unreachable"
    mail.close(1)  # forgets the cached answer, and the account is looked up again
    assert mail.reachability(1) == Reachability(ok=True)


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
                1, user, imap_server, imap={"host": "127.0.0.1", "port": port, "tls": "starttls"}
            )
        )
        assert mail.with_mailbox(1, "INBOX", True, lambda ctx: ctx.exists) == 1
        assert mail.capabilities(1).uidplus  # as announced after TLS and the login


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
                1,
                "someone",
                imap_server,
                imap={"host": "127.0.0.1", "port": port, "tls": "starttls"},
            )
        )
        with pytest.raises(MailError):
            mail.folders(1)
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
