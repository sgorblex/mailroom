"""Folder writes over fake clients: the names and depths refused before any connection, the
server's answers mapped here, the write whose answer was lost looked for again, subscriptions as
best effort, and `with_connection` readying a connection: UNSELECT or a new connection, never
CLOSE, one retry while readying and none of `fn`."""

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError

from mailroom_core.mail import (
    MAX_FOLDER_DEPTH,
    MAX_FOLDER_NAME,
    AuditItem,
    Folder,
    FolderChange,
    FolderChangedError,
    MailConnections,
    MailError,
    check_folder_name,
    create_folder,
    delete_folder,
    mail_error,
    rename_folder,
    subfolders_of,
)
from mailroom_core.mail.imap import folders_from

if TYPE_CHECKING:
    from collections.abc import Callable

type Listing = list[tuple[tuple[bytes, ...], bytes | None, str]]


def refused(call: Callable[[], object], code: str = "invalid_argument") -> MailError:
    with pytest.raises(MailError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


# Names and paths


@pytest.mark.parametrize(
    ("name", "detail"),
    [
        ("", "needs a name"),
        ("x" * (MAX_FOLDER_NAME + 1), f"at most {MAX_FOLDER_NAME} characters"),
        ("a\x01b", "control characters"),
        ("tab\there", "control characters"),
        ("a\x7fb", "control characters"),
        ("a/b", '"/"'),
        ("a*", '"*" or "%"'),
        ("50%", '"*" or "%"'),
        (" lead", "start or end with a space"),
        ("trail ", "start or end with a space"),
    ],
)
def test_refuses_a_name(name: str, detail: str) -> None:
    error = refused(lambda: check_folder_name(name, "/"))
    assert detail in error.message


@pytest.mark.parametrize(
    ("name", "delimiter"),
    [
        ("Città", "/"),
        ("x" * MAX_FOLDER_NAME, "/"),
        ("a.b", "/"),
        ("a/b", None),
        ("a/b", "."),
        ("In the middle", "/"),
        ("INBOX", "/"),  # under a parent it is a name like any other
    ],
)
def test_accepts_a_name(name: str, delimiter: str | None) -> None:
    check_folder_name(name, delimiter)


def test_subfolders_of() -> None:
    folders = folders_from(
        [
            ((), b"/", "A"),
            ((b"\\Noselect",), b"/", "A/B"),
            ((), b"/", "A/B/C"),
            ((), b"/", "AB"),
            ((), b"/", "B/A"),
            ((), None, "A/flat"),
        ]
    )
    assert [f.path for f in subfolders_of(folders, "A")] == ["A/B", "A/B/C"]
    assert [f.path for f in subfolders_of(folders, "A/B")] == ["A/B/C"]
    assert subfolders_of(folders, "A/B/C") == []
    assert subfolders_of(folders, "AB") == []


# A fake account


def entry(path: str, *flags: bytes, delimiter: bytes | None = b"/") -> tuple[Any, ...]:
    return (flags, delimiter, path)


class FolderClient:
    """An account's connection with no mailbox selected, recording every write."""

    def __init__(
        self,
        listing: Listing,
        *,
        counts: dict[str, int] | None = None,
        subscribed: tuple[str, ...] = (),
    ) -> None:
        self.listing = listing
        self.counts = counts or {}
        self.subscribed = subscribed
        self.calls: list[tuple[object, ...]] = []
        self.fail: dict[str, BaseException] = {}

    def _record(self, name: str, *args: object) -> None:
        self.calls.append((name, *args))
        if name in self.fail:
            raise self.fail[name]

    def list_folders(self) -> Listing:
        self._record("list")
        return self.listing

    def list_sub_folders(self) -> Listing:
        self._record("lsub")
        return [((), b"/", path) for path in self.subscribed]

    def folder_status(self, path: str, what: list[str]) -> dict[bytes, int]:
        self._record("status", path, tuple(what))
        return {b"MESSAGES": self.counts.get(path, 0)}

    def create_folder(self, path: str) -> None:
        self._record("create", path)

    def rename_folder(self, old: str, new: str) -> None:
        self._record("rename", old, new)

    def delete_folder(self, path: str) -> None:
        self._record("delete", path)

    def subscribe_folder(self, path: str) -> None:
        self._record("subscribe", path)

    def unsubscribe_folder(self, path: str) -> None:
        self._record("unsubscribe", path)

    @property
    def writes(self) -> list[tuple[object, ...]]:
        return [call for call in self.calls if call[0] not in ("list", "lsub", "status")]


class FakeConnections:
    """One session per call, errors mapped as `with_connection` maps them; `after` is what a
    listing outside the session finds."""

    def __init__(
        self, client: FolderClient, delimiter: str | None = "/", after: Listing | None = None
    ) -> None:
        self.client = client
        self._delimiter = delimiter
        self.after = after
        self.sessions = 0
        self.listings = 0

    def delimiter(self, account: str) -> str | None:
        return self._delimiter

    def with_connection(self, account: str, fn: Callable[[Any], object]) -> object:
        self.sessions += 1
        try:
            return fn(self.client)
        except Exception as error:
            raise mail_error(error, account) from None

    def folders(self, account: str) -> list[Folder]:
        self.listings += 1
        if self.after is None:
            raise MailError("unreachable")
        return folders_from(self.after)


BASE: Listing = [
    entry("INBOX"),
    entry("Sent", b"\\Sent"),
    entry("Trash", b"\\Trash"),
    entry("Projects", b"\\HasChildren"),
    entry("Projects/2026"),
    entry("Old"),
]


def account(
    listing: Listing = BASE, **options: Any
) -> tuple[FolderClient, FakeConnections, list[AuditItem]]:
    after = options.pop("after", None)
    delimiter = options.pop("delimiter", "/")
    client = FolderClient(listing, **options)
    return client, FakeConnections(client, delimiter, after), []


def run[T](call: Callable[..., T], connections: FakeConnections, *args: Any, **kwargs: Any) -> T:
    return call(cast("MailConnections", connections), "acc", *args, **kwargs)


def nested(depth: int) -> str:
    return "/".join(f"L{level}" for level in range(1, depth + 1))


class TestRefusedBeforeAnySession:
    def test_inbox_at_the_top_level(self) -> None:
        _client, connections, notes = account()
        error = refused(lambda: run(create_folder, connections, None, "inbox", notes.append))
        assert "INBOX" in error.message
        assert connections.sessions == 0
        assert [n.result for n in notes] == ["invalid_argument"]

    def test_a_parent_on_a_server_without_hierarchy(self) -> None:
        _client, connections, notes = account(delimiter=None)
        error = refused(lambda: run(create_folder, connections, "Projects", "x", notes.append))
        assert "no folder hierarchy" in error.message
        assert connections.sessions == 0

    def test_too_deep(self) -> None:
        _client, connections, notes = account()
        assert MAX_FOLDER_DEPTH == 32
        error = refused(lambda: run(create_folder, connections, nested(32), "x", notes.append))
        assert "32 levels" in error.message
        assert connections.sessions == 0

    def test_too_long(self) -> None:
        _client, connections, notes = account()
        parent = "/".join(["p" * 200] * 5)
        error = refused(lambda: run(create_folder, connections, parent, "x", notes.append))
        assert "1000 characters" in error.message

    def test_a_negative_count(self) -> None:
        _client, connections, notes = account()
        refused(lambda: run(delete_folder, connections, "Old", messages=-1, note=notes.append))
        refused(lambda: run(delete_folder, connections, "Old", messages=True, note=notes.append))
        assert connections.sessions == 0
        assert [n.result for n in notes] == ["invalid_argument", "invalid_argument"]


class TestCreate:
    def test_creates_and_subscribes(self) -> None:
        client, connections, notes = account()
        change = run(create_folder, connections, "Projects", "2027", notes.append)
        assert change == FolderChange(account="acc", path="Projects/2027", delimiter="/")
        assert client.writes == [("create", "Projects/2027"), ("subscribe", "Projects/2027")]
        assert notes == [AuditItem(account="acc", folder="Projects/2027", result="ok")]

    def test_an_inbox_parent_in_any_case_is_spelled_as_listed(self) -> None:
        client, connections, notes = account()
        change = run(create_folder, connections, "inbox", "Sub", notes.append)
        assert change.path == "INBOX/Sub"
        assert client.writes[0] == ("create", "INBOX/Sub")

    def test_parent_may_be_noselect(self) -> None:
        client, connections, notes = account([*BASE, entry("Box", b"\\Noselect")])
        run(create_folder, connections, "Box", "In", notes.append)
        assert client.writes[0] == ("create", "Box/In")

    def test_refusals(self) -> None:
        client, connections, notes = account()
        refused(
            lambda: run(create_folder, connections, "Nope", "x", notes.append), "mailbox_missing"
        )
        refused(
            lambda: run(create_folder, connections, "Projects", "2026", notes.append),
            "folder_exists",
        )
        assert client.writes == []
        assert [(n.folder, n.result) for n in notes] == [
            ("Nope/x", "mailbox_missing"),
            ("Projects/2026", "folder_exists"),
        ]


class TestServerAnswers:
    @pytest.mark.parametrize(
        ("text", "code", "detail"),
        [
            ("create failed: [ALREADYEXISTS] Mailbox already exists", "folder_exists", None),
            (
                "create failed: [CANNOT] Invalid mailbox name",
                "invalid_argument",
                "The mail server does not accept that folder name.",
            ),
            ("create failed: [INUSE] Mailbox is locked", "busy", None),
            ("create failed: Something else", "server_error", None),
            ("create failed: [NONEXISTENT] No parent", "mailbox_missing", None),
        ],
    )
    def test_create(self, text: str, code: str, detail: str | None) -> None:
        client, connections, notes = account()
        client.fail["create"] = IMAPClientError(text)
        error = refused(lambda: run(create_folder, connections, None, "New", notes.append), code)
        assert error.detail == detail
        assert "Mailbox" not in error.message
        assert [n.result for n in notes] == [code]
        assert connections.listings == 0  # an answer is no uncertain outcome
        assert ("subscribe", "New") not in client.calls

    def test_delete_in_use(self) -> None:
        client, connections, notes = account()
        client.fail["delete"] = IMAPClientError("delete failed: [INUSE] Mailbox is in use")
        refused(
            lambda: run(delete_folder, connections, "Old", messages=0, note=notes.append), "busy"
        )
        assert [n.result for n in notes] == ["busy"]


class TestUncertainOutcome:
    def test_a_lost_answer_whose_change_shows_is_a_success(self, caplog) -> None:
        caplog.set_level(logging.INFO, "mailroom_core.mail")
        client, connections, notes = account(after=[*BASE, entry("New")])
        client.fail["create"] = TimeoutError("timed out")
        change = run(create_folder, connections, None, "New", notes.append)
        assert change.path == "New"
        assert connections.listings == 1
        assert [n.result for n in notes] == ["ok"]
        assert any(r.message == "imap_folder_write_confirmed" for r in caplog.records)

    def test_a_lost_answer_whose_change_does_not_show_is_raised(self) -> None:
        client, connections, notes = account(after=BASE)
        client.fail["rename"] = IMAPClientAbortError("socket error: EOF")
        refused(
            lambda: run(rename_folder, connections, "Old", None, "Older", notes.append),
            "unreachable",
        )
        assert connections.listings == 1
        assert [n.result for n in notes] == ["unreachable"]

    def test_a_listing_that_fails_raises_the_original(self) -> None:
        client, connections, notes = account()  # no `after`: the listing fails
        client.fail["delete"] = TimeoutError("timed out")
        refused(
            lambda: run(delete_folder, connections, "Old", messages=0, note=notes.append), "timeout"
        )
        assert connections.listings == 1

    def test_the_rename_shows_only_once_the_source_is_gone(self) -> None:
        client, connections, notes = account(after=[*BASE, entry("Older")])
        client.fail["rename"] = TimeoutError("timed out")
        refused(
            lambda: run(rename_folder, connections, "Old", None, "Older", notes.append), "timeout"
        )

    def test_a_delete_shows_once_the_folder_is_gone(self) -> None:
        after = [e for e in BASE if e[2] != "Old"]
        client, connections, notes = account(after=after)
        client.fail["delete"] = OSError("connection reset")
        change = run(delete_folder, connections, "Old", messages=0, note=notes.append)
        assert change.messages == 0

    def test_a_failure_before_the_write_is_never_looked_for(self) -> None:
        client, connections, notes = account(after=[*BASE, entry("New")])
        client.fail["list"] = OSError("connection reset")
        refused(lambda: run(create_folder, connections, None, "New", notes.append), "unreachable")
        assert connections.listings == 0
        assert client.writes == []


class TestSubscriptions:
    @pytest.mark.parametrize(
        "failure", [IMAPClientError("subscribe failed: NO"), OSError("connection reset")]
    )
    def test_best_effort(self, failure: Exception) -> None:
        client, connections, notes = account()
        client.fail["subscribe"] = failure
        assert run(create_folder, connections, None, "New", notes.append).path == "New"
        assert [n.result for n in notes] == ["ok"]

    def test_a_rename_moves_them(self) -> None:
        client, connections, notes = account(
            subscribed=("INBOX", "Projects", "Projects/2026", "ProjectsX")
        )
        change = run(rename_folder, connections, "Projects", "Old", "P", notes.append)
        assert change == FolderChange(
            account="acc", path="Projects", delimiter="/", destination="Old/P"
        )
        assert client.writes == [
            ("rename", "Projects", "Old/P"),
            ("subscribe", "Old/P"),
            ("subscribe", "Old/P/2026"),
            ("unsubscribe", "Projects"),
            ("unsubscribe", "Projects/2026"),
        ]
        assert notes == [
            AuditItem(account="acc", folder="Projects", destination="Old/P", result="ok")
        ]

    def test_a_rename_stands_when_lsub_is_refused(self) -> None:
        client, connections, notes = account(subscribed=("Projects",))
        client.fail["lsub"] = IMAPClientError("LSUB unknown command")
        run(rename_folder, connections, "Projects", None, "P", notes.append)
        assert client.writes == [("rename", "Projects", "P")]
        assert [n.result for n in notes] == ["ok"]

    def test_a_delete_unsubscribes(self) -> None:
        client, connections, notes = account(counts={"Old": 4})
        run(delete_folder, connections, "Old", messages=4, note=notes.append)
        assert client.writes == [("delete", "Old"), ("unsubscribe", "Old")]
        assert notes == [AuditItem(account="acc", folder="Old", messages=4, result="ok")]


class TestRenameRefusals:
    @pytest.mark.parametrize("folder", ["INBOX", "inbox", "Sent", "Trash"])
    def test_inbox_and_roles(self, folder: str) -> None:
        client, connections, notes = account()
        error = refused(lambda: run(rename_folder, connections, folder, None, "X", notes.append))
        assert "cannot be renamed" in error.message
        assert client.writes == []

    def test_a_parent_of_a_role_folder(self) -> None:
        _client, connections, notes = account([*BASE, entry("Mail"), entry("Mail/Junk", b"\\Junk")])
        error = refused(lambda: run(rename_folder, connections, "Mail", None, "M", notes.append))
        assert "role" in error.message

    def test_into_itself_and_onto_itself(self) -> None:
        client, connections, notes = account()
        error = refused(
            lambda: run(rename_folder, connections, "Projects", "Projects/2026", "P", notes.append)
        )
        assert "into itself" in error.message
        error = refused(lambda: run(rename_folder, connections, "Old", None, "Old", notes.append))
        assert "already" in error.message
        assert client.writes == []

    def test_existing_and_missing(self) -> None:
        client, connections, notes = account()
        refused(
            lambda: run(rename_folder, connections, "Old", None, "Projects", notes.append),
            "folder_exists",
        )
        refused(
            lambda: run(rename_folder, connections, "Gone", None, "X", notes.append),
            "mailbox_missing",
        )
        refused(
            lambda: run(rename_folder, connections, "Old", "Gone", "X", notes.append),
            "mailbox_missing",
        )
        assert client.writes == []
        assert [(n.folder, n.destination, n.result) for n in notes] == [
            ("Old", "Projects", "folder_exists"),
            ("Gone", "X", "mailbox_missing"),
            ("Old", "Gone/X", "mailbox_missing"),
        ]

    def test_a_subfolder_too_deep(self) -> None:
        deep = nested(31)
        client, connections, notes = account([*BASE, entry(deep)])
        error = refused(
            lambda: run(rename_folder, connections, "Projects", deep, "P", notes.append)
        )
        assert "32 levels" in error.message
        assert client.writes == []
        # Without the subfolder, the folder itself fits at depth 32.
        run(rename_folder, connections, "Old", deep, "P", notes.append)
        assert client.writes[0] == ("rename", "Old", f"{deep}/P")


class TestDelete:
    def test_refuses_a_changed_count_with_the_new_one(self) -> None:
        client, connections, notes = account(counts={"Old": 3})
        with pytest.raises(FolderChangedError) as caught:
            run(delete_folder, connections, "Old", messages=2, note=notes.append)
        assert caught.value.code == "folder_changed"
        assert caught.value.messages == 3
        assert "3 messages" in caught.value.message
        assert ("status", "Old", ("MESSAGES",)) in client.calls
        assert client.writes == []
        assert notes == [
            AuditItem(account="acc", folder="Old", messages=2, result="folder_changed")
        ]

    def test_a_noselect_folder_holds_nothing(self) -> None:
        client, connections, notes = account([*BASE, entry("Box", b"\\Noselect")])
        run(delete_folder, connections, "Box", messages=0, note=notes.append)
        assert not any(call[0] == "status" for call in client.calls)
        assert client.writes[0] == ("delete", "Box")

    @pytest.mark.parametrize("folder", ["Projects", "INBOX", "Trash"])
    def test_refuses_subfolders_inbox_and_roles(self, folder: str) -> None:
        client, connections, notes = account()
        refused(lambda: run(delete_folder, connections, folder, messages=0, note=notes.append))
        assert client.writes == []


# with_connection


class SessionClient:
    """A connection as `MailConnections` holds it: imaplib's state and the commands sent."""

    def __init__(self, capabilities: tuple[bytes, ...], log: list[tuple[object, ...]]) -> None:
        self.caps = capabilities
        self.log = log
        self.fail: dict[str, BaseException] = {}
        self._imap = SimpleNamespace(state="AUTH", _simple_command=self._simple_command)

    def _record(self, name: str) -> None:
        self.log.append((name, id(self)))
        if name in self.fail:
            raise self.fail.pop(name)

    @property
    def state(self) -> str:
        return cast("str", self._imap.state)

    def capabilities(self) -> tuple[bytes, ...]:
        return self.caps

    def has_capability(self, name: str) -> bool:
        return name.upper().encode() in self.caps

    def select_folder(self, path: str, readonly: bool = False) -> dict[bytes, int]:
        self._record("select")
        self._imap.state = "SELECTED"
        return {b"UIDVALIDITY": 1, b"EXISTS": 0}

    def unselect_folder(self) -> None:
        self._record("unselect")
        self._imap.state = "AUTH"

    def _simple_command(self, name: str) -> tuple[str, list[bytes]]:
        self._record(f"raw {name}")
        if name == "UNSELECT":
            self._imap.state = "AUTH"
        return "OK", [b""]

    def close_folder(self) -> None:
        self._record("close")

    def noop(self) -> None:
        self._record("noop")

    def logout(self) -> None:
        self._record("logout")

    def shutdown(self) -> None:
        self._record("shutdown")

    def socket(self) -> SimpleNamespace:
        return SimpleNamespace(settimeout=lambda _seconds: None)


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch):
    """MailConnections over SessionClients, each opened with the capabilities asked for."""

    def make(*capabilities: bytes) -> tuple[MailConnections, list[SessionClient], list[Any]]:
        connections = MailConnections(lambda _id: cast("Any", SimpleNamespace()), lambda _id: "pw")
        opened: list[SessionClient] = []
        log: list[tuple[object, ...]] = []

        def open_unpooled(account_id: str, *, read_timeout: float | None = None) -> SessionClient:
            client = SessionClient(capabilities, log)
            opened.append(client)
            return client

        monkeypatch.setattr(connections, "_open_unpooled", open_unpooled)
        return connections, opened, log

    return make


def names(log: list[tuple[object, ...]], opened: list[SessionClient]) -> list[str]:
    """The log with each command tagged by which connection it went to."""
    index = {id(client): number for number, client in enumerate(opened)}
    return [f"{name}@{index[client]}" for name, client in log]


def leave_selected(connections: MailConnections) -> None:
    connections.with_mailbox("acc", "Projects", False, lambda _context: None)


class TestWithConnection:
    def test_unselects_a_selected_mailbox(self, pool) -> None:
        connections, opened, log = pool(b"IMAP4REV1", b"UNSELECT")
        leave_selected(connections)
        seen = connections.with_connection("acc", lambda client: client.state)
        assert seen == "AUTH"
        assert names(log, opened) == ["select@0", "unselect@0"]

    def test_imap4rev2_without_the_name_unselects_too(self, pool) -> None:
        connections, opened, log = pool(b"IMAP4REV2")
        leave_selected(connections)
        connections.with_connection("acc", lambda _client: None)
        assert names(log, opened) == ["select@0", "raw UNSELECT@0"]

    def test_without_unselect_opens_a_new_connection_never_close(self, pool) -> None:
        connections, opened, log = pool(b"IMAP4REV1")
        leave_selected(connections)
        used = connections.with_connection("acc", lambda client: client)
        assert used is opened[1]
        assert opened[1].state == "AUTH"
        assert names(log, opened) == ["select@0", "shutdown@0"]
        assert not any(name == "close" for name, _client in log)

    def test_a_reused_idle_connection_answers_a_noop_first(self, pool) -> None:
        connections, opened, log = pool(b"IMAP4REV1", b"UNSELECT")
        connections.with_connection("acc", lambda _client: None)
        connections.with_connection("acc", lambda _client: None)
        assert names(log, opened) == ["noop@0"]

    def test_retries_once_while_readying(self, pool) -> None:
        connections, opened, log = pool(b"IMAP4REV1", b"UNSELECT")
        connections.with_connection("acc", lambda _client: None)
        opened[0].fail["noop"] = OSError("connection reset")
        calls: list[object] = []
        connections.with_connection("acc", calls.append)
        assert calls == [opened[1]]
        assert names(log, opened) == ["noop@0", "shutdown@0"]

    def test_a_dead_selected_connection_is_retried_too(self, pool) -> None:
        connections, opened, _log = pool(b"IMAP4REV1", b"UNSELECT")
        leave_selected(connections)
        opened[0].fail["unselect"] = IMAPClientAbortError("socket error: EOF")
        calls: list[object] = []
        connections.with_connection("acc", calls.append)
        assert calls == [opened[1]]

    def test_never_retries_fn(self, pool) -> None:
        connections, opened, _log = pool(b"IMAP4REV1", b"UNSELECT")
        calls = 0

        def fn(_client: object) -> None:
            nonlocal calls
            calls += 1
            raise OSError("connection reset")

        refused(lambda: connections.with_connection("acc", fn), "unreachable")
        assert calls == 1
        assert len(opened) == 1

    def test_a_mail_error_from_fn_comes_out_as_it_is(self, pool) -> None:
        connections, _opened, _log = pool(b"IMAP4REV1", b"UNSELECT")
        error = FolderChangedError(4)

        def fn(_client: object) -> None:
            raise error

        with pytest.raises(FolderChangedError) as caught:
            connections.with_connection("acc", fn)
        assert caught.value is error
