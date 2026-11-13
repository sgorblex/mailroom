"""Creating, renaming and deleting folders against the test Dovecot: subscriptions follow, a
folder a `with_mailbox` left selected on the one connection is no obstacle, the refusals write
nothing, and a delete goes only with the count the caller confirmed."""

from typing import TYPE_CHECKING

import pytest

from mailroom_core.mail import (
    AuditItem,
    FolderChange,
    FolderChangedError,
    MailConnections,
    MailError,
    create_folder,
    delete_folder,
    rename_folder,
)
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
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.integration


@pytest.fixture
def mail(imap_server: ImapTestServer) -> Iterator[Callable[[str], MailConnections]]:
    made: list[MailConnections] = []

    def make(user: str) -> MailConnections:
        connections = connections_for(imap_server, [test_account("acc", user, imap_server)])
        made.append(connections)
        return connections

    yield make
    for connections in made:
        connections.close_all()


def listed(server: ImapTestServer, user: str) -> dict[str, set[str]]:
    """Every folder a raw client lists, with its flags."""
    with raw_client(server, user) as client:
        return {
            name: {flag.decode() for flag in flags} for flags, _d, name in client.list_folders()
        }


def subscribed(server: ImapTestServer, user: str) -> set[str]:
    with raw_client(server, user) as client:
        return {name for _flags, _d, name in client.list_sub_folders()}


def subjects(server: ImapTestServer, user: str, folder: str) -> list[str]:
    return [m.subject for m in folder_contents(server, user, folder)]


def refused(call: Callable[[], object], code: str = "invalid_argument") -> MailError:
    with pytest.raises(MailError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def leave_selected(connections: MailConnections, folder: str) -> None:
    """A read-write session on `folder`, which leaves it selected on the account's one slot."""
    connections.with_mailbox("acc", folder, False, lambda _context: None)


class TestCreate:
    def test_top_level_and_nested_subscribed(self, imap_server, mail) -> None:
        user = imap_server.unique_user("create")
        connections = mail(user)
        notes: list[AuditItem] = []
        top = create_folder(connections, "acc", None, "Projects", notes.append)
        inner = create_folder(connections, "acc", "Projects", "2026 Città", notes.append)
        assert top == FolderChange(account="acc", path="Projects", delimiter="/")
        assert inner.path == "Projects/2026 Città"
        assert {"Projects", "Projects/2026 Città"} <= set(listed(imap_server, user))
        assert {"Projects", "Projects/2026 Città"} <= subscribed(imap_server, user)
        assert notes == [
            AuditItem(account="acc", folder="Projects", result="ok"),
            AuditItem(account="acc", folder="Projects/2026 Città", result="ok"),
        ]

    def test_refused_when_it_exists_or_the_parent_is_missing(self, imap_server, mail) -> None:
        user = imap_server.unique_user("create-refused")
        create_folders(imap_server, user, "Projects")
        connections = mail(user)
        notes: list[AuditItem] = []
        refused(
            lambda: create_folder(connections, "acc", None, "Projects", notes.append),
            "folder_exists",
        )
        refused(
            lambda: create_folder(connections, "acc", "Nope", "Inner", notes.append),
            "mailbox_missing",
        )
        refused(lambda: create_folder(connections, "acc", None, "a*b", notes.append))
        assert "Nope" not in listed(imap_server, user)
        assert "Nope/Inner" not in listed(imap_server, user)
        assert [(n.folder, n.result) for n in notes] == [
            ("Projects", "folder_exists"),
            ("Nope/Inner", "mailbox_missing"),
            ("a*b", "invalid_argument"),
        ]

    def test_right_after_a_session_left_a_folder_selected(self, imap_server, mail) -> None:
        user = imap_server.unique_user("create-selected")
        # Marked \Deleted by another client: a CLOSE would expunge it, UNSELECT must not.
        seed(imap_server, user, "INBOX", mime("kept"), flags=("\\Deleted",))
        connections = mail(user)
        leave_selected(connections, "INBOX")
        create_folder(connections, "acc", None, "New", lambda _item: None)
        assert "New" in listed(imap_server, user)
        assert subjects(imap_server, user, "INBOX") == ["kept"]


class TestRename:
    def test_a_leaf(self, imap_server, mail) -> None:
        user = imap_server.unique_user("rename-leaf")
        create_folders(imap_server, user, "Projects", "Archive")
        seed(imap_server, user, "Projects", mime("plan"))
        connections = mail(user)
        create_folder(connections, "acc", "Projects", "Leaf", lambda _item: None)
        seed(imap_server, user, "Projects/Leaf", mime("leaf mail"))
        notes: list[AuditItem] = []
        change = rename_folder(
            connections, "acc", "Projects/Leaf", "Archive", "Moved", notes.append
        )
        assert change == FolderChange(
            account="acc", path="Projects/Leaf", delimiter="/", destination="Archive/Moved"
        )
        folders = listed(imap_server, user)
        assert "Projects/Leaf" not in folders
        assert "Archive/Moved" in folders
        assert subjects(imap_server, user, "Archive/Moved") == ["leaf mail"]
        assert subjects(imap_server, user, "Projects") == ["plan"]
        subs = subscribed(imap_server, user)
        assert "Archive/Moved" in subs
        assert "Projects/Leaf" not in subs
        assert notes == [
            AuditItem(
                account="acc", folder="Projects/Leaf", destination="Archive/Moved", result="ok"
            )
        ]

    def test_a_parent_with_its_children_right_after_a_session_selected_it(
        self, imap_server, mail
    ) -> None:
        user = imap_server.unique_user("rename-parent")
        connections = mail(user)
        for parent, name in [(None, "Projects"), ("Projects", "2026"), ("Projects/2026", "Q1")]:
            create_folder(connections, "acc", parent, name, lambda _item: None)
        seed(imap_server, user, "Projects", mime("top"))
        seed(imap_server, user, "Projects/2026/Q1", mime("deep"))
        leave_selected(connections, "Projects")
        rename_folder(connections, "acc", "Projects", None, "Work", lambda _item: None)
        folders = listed(imap_server, user)
        assert not any(name.startswith("Projects") for name in folders)
        assert {"Work", "Work/2026", "Work/2026/Q1"} <= set(folders)
        assert subjects(imap_server, user, "Work") == ["top"]
        assert subjects(imap_server, user, "Work/2026/Q1") == ["deep"]
        subs = subscribed(imap_server, user)
        assert {"Work", "Work/2026", "Work/2026/Q1"} <= subs
        assert not any(name.startswith("Projects") for name in subs)
        # The session's connection is still good for the next one.
        assert connections.with_mailbox("acc", "Work", True, lambda context: context.exists) == 1

    def test_refusals_write_nothing(self, imap_server, mail) -> None:
        user = imap_server.unique_user("rename-refused")
        create_folders(imap_server, user, "Projects", "Projects/2026")
        connections = mail(user)
        notes: list[AuditItem] = []
        for folder in ("INBOX", "Sent", "Trash"):
            error = refused(
                lambda folder=folder: rename_folder(
                    connections, "acc", folder, None, "Other", notes.append
                )
            )
            assert "cannot be renamed" in error.message
        error = refused(
            lambda: rename_folder(
                connections, "acc", "Projects", "Projects/2026", "Inside", notes.append
            )
        )
        assert "into itself" in error.message
        deep = "/".join(f"D{level}" for level in range(1, 32))
        create_folders(imap_server, user, deep)
        before = listed(imap_server, user)
        error = refused(
            lambda: rename_folder(connections, "acc", "Projects", deep, "P", notes.append)
        )
        assert "32 levels" in error.message
        error = refused(
            lambda: rename_folder(connections, "acc", "Projects", f"{deep}/D32", "P", notes.append)
        )
        assert listed(imap_server, user) == before
        assert [(n.folder, n.result) for n in notes] == [
            ("INBOX", "invalid_argument"),
            ("Sent", "invalid_argument"),
            ("Trash", "invalid_argument"),
            ("Projects", "invalid_argument"),
            ("Projects", "invalid_argument"),
            ("Projects", "invalid_argument"),
        ]


class TestDelete:
    def test_an_empty_folder_and_one_holding_mail(self, imap_server, mail) -> None:
        user = imap_server.unique_user("delete")
        connections = mail(user)
        create_folder(connections, "acc", None, "Empty", lambda _item: None)
        create_folder(connections, "acc", None, "Full", lambda _item: None)
        for subject in ("one", "two", "three"):
            seed(imap_server, user, "Full", mime(subject))
        notes: list[AuditItem] = []
        assert delete_folder(connections, "acc", "Empty", messages=0, note=notes.append) == (
            FolderChange(account="acc", path="Empty", delimiter="/", messages=0)
        )
        delete_folder(connections, "acc", "Full", messages=3, note=notes.append)
        folders = listed(imap_server, user)
        assert "Empty" not in folders
        assert "Full" not in folders
        assert not {"Empty", "Full"} & subscribed(imap_server, user)
        assert notes == [
            AuditItem(account="acc", folder="Empty", messages=0, result="ok"),
            AuditItem(account="acc", folder="Full", messages=3, result="ok"),
        ]

    def test_mail_arriving_after_the_count_refuses_it(self, imap_server, mail) -> None:
        user = imap_server.unique_user("delete-changed")
        create_folders(imap_server, user, "Old")
        seed(imap_server, user, "Old", mime("first"))
        connections = mail(user)
        counted = next(f for f in connections.folders("acc", with_status=True) if f.path == "Old")
        assert counted.messages == 1
        seed(imap_server, user, "Old", mime("late"))
        notes: list[AuditItem] = []
        with pytest.raises(FolderChangedError) as caught:
            delete_folder(connections, "acc", "Old", messages=1, note=notes.append)
        assert (caught.value.code, caught.value.messages) == ("folder_changed", 2)
        assert subjects(imap_server, user, "Old") == ["first", "late"]
        assert notes == [
            AuditItem(account="acc", folder="Old", messages=1, result="folder_changed")
        ]

    def test_a_noselect_parent_is_refused_for_its_subfolders(self, imap_server, mail) -> None:
        user = imap_server.unique_user("delete-noselect")
        create_folders(imap_server, user, "A/B")
        connections = mail(user)
        parent = next(f for f in connections.folders("acc") if f.path == "A")
        assert not parent.selectable
        notes: list[AuditItem] = []
        error = refused(
            lambda: delete_folder(connections, "acc", "A", messages=0, note=notes.append)
        )
        assert "subfolders" in error.message
        assert {"A", "A/B"} <= set(listed(imap_server, user))
        for folder in ("INBOX", "Trash"):
            refused(
                lambda folder=folder: delete_folder(
                    connections, "acc", folder, messages=0, note=notes.append
                )
            )
        refused(
            lambda: delete_folder(connections, "acc", "Gone", messages=0, note=notes.append),
            "mailbox_missing",
        )
        assert [(n.folder, n.result) for n in notes] == [
            ("A", "invalid_argument"),
            ("INBOX", "invalid_argument"),
            ("Trash", "invalid_argument"),
            ("Gone", "mailbox_missing"),
        ]

    def test_the_folder_selected_on_the_same_connection(self, imap_server, mail) -> None:
        user = imap_server.unique_user("delete-selected")
        create_folders(imap_server, user, "Old")
        seed(imap_server, user, "Old", mime("gone"))
        seed(imap_server, user, "INBOX", mime("kept"))
        connections = mail(user)
        leave_selected(connections, "Old")
        delete_folder(connections, "acc", "Old", messages=1, note=lambda _item: None)
        assert "Old" not in listed(imap_server, user)
        assert subjects(imap_server, user, "INBOX") == ["kept"]
        # Selecting it again is a missing folder, not a stale connection.
        refused(
            lambda: connections.with_mailbox("acc", "Old", True, lambda _context: None),
            "mailbox_missing",
        )
