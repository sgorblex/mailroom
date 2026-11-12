"""Tests for action classes."""

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from mailroom_core.filters.actions.action import Action
from mailroom_core.filters.actions.delete import Delete
from mailroom_core.filters.actions.flags import (
    AddFlag,
    MarkAsAnswered,
    MarkAsRead,
    MarkAsUnanswered,
    MarkAsUnread,
    RemoveFlag,
    Star,
    Unstar,
)
from mailroom_core.filters.actions.move import MoveTo, Trash
from mailroom_core.filters.actions.noop import NoOp
from mailroom_core.filters.flags import Flag
from mailroom_core.filters.path import Path
from mailroom_core.filters.selected import Selected
from mailroom_core.mail import Folder, MailboxContext, MailError

if TYPE_CHECKING:
    from collections.abc import Sequence


class TestActionCreation:
    """Test action class creation."""

    def test_move_to_creation(self) -> None:
        """Test MoveTo action creation."""
        action = MoveTo(Path("TestFolder"))
        assert isinstance(action, Action)
        assert action.name == "move to TestFolder"

    def test_trash_creation(self) -> None:
        """Test Trash action creation."""
        action = Trash()
        assert isinstance(action, Action)
        assert action.name == "trash"

    def test_mark_as_read_creation(self) -> None:
        """Test MarkAsRead action creation."""
        action = MarkAsRead()
        assert isinstance(action, Action)
        assert action.name == "mark as read"

    def test_mark_as_unread_creation(self) -> None:
        """Test MarkAsUnread action creation."""
        action = MarkAsUnread()
        assert isinstance(action, Action)
        assert action.name == "mark as unread"

    def test_mark_as_answered_creation(self) -> None:
        """Test MarkAsAnswered action creation."""
        action = MarkAsAnswered()
        assert isinstance(action, Action)
        assert action.name == "mark as answered"

    def test_mark_as_unanswered_creation(self) -> None:
        """Test MarkAsUnanswered action creation."""
        action = MarkAsUnanswered()
        assert isinstance(action, Action)
        assert action.name == "mark as unanswered"

    def test_star_creation(self) -> None:
        """Test Star action creation."""
        action = Star()
        assert isinstance(action, Action)
        assert action.name == "star"

    def test_unstar_creation(self) -> None:
        """Test Unstar action creation."""
        action = Unstar()
        assert isinstance(action, Action)
        assert action.name == "unstar"

    def test_delete_creation(self) -> None:
        """Test Delete action creation."""
        action = Delete()
        assert isinstance(action, Action)
        assert action.name == "delete"

    def test_flag_action_default_name(self) -> None:
        """A bare flag action is named after its flag."""
        assert AddFlag(Flag.SEEN).name == "add SEEN"
        assert RemoveFlag(Flag.FLAGGED).name == "remove FLAGGED"

    def test_noop_creation(self) -> None:
        """Test NoOp action creation."""
        action = NoOp()
        assert isinstance(action, Action)
        assert action.name == "do nothing"


class TestActionChaining:
    """Test action chaining with + operator."""

    def test_action_chaining(self) -> None:
        """Test chaining two actions together."""
        action1 = MarkAsRead()
        action2 = MoveTo(Path("Folder"))
        chained = action1 + action2
        assert isinstance(chained, Action)
        assert "mark as read" in chained.name
        assert "move to Folder" in chained.name

    def test_action_then_method(self) -> None:
        """Test a.then(other) returns Action."""
        a1 = MarkAsRead()
        a2 = MoveTo(Path("Folder"))
        chained = a1.then(a2)
        assert isinstance(chained, Action)
        assert "mark as read" in chained.name
        assert "move to Folder" in chained.name

    def test_action_chaining_execution(self) -> None:
        """Test that chained actions execute both functions, on the same selection."""
        call_order: list[tuple[str, Selected, list[int]]] = []

        def action1_func(selected: Selected, uids: Sequence[int]) -> None:
            call_order.append(("action1", selected, list(uids)))

        def action2_func(selected: Selected, uids: Sequence[int]) -> None:
            call_order.append(("action2", selected, list(uids)))

        chained = Action(action1_func, "action1") + Action(action2_func, "action2")
        on = selected()
        chained.execute(on, [1, 2, 3])
        assert call_order == [("action1", on, [1, 2, 3]), ("action2", on, [1, 2, 3])]


def selected(path: str = "INBOX", delimiter: str | None = ".", *listed: Folder) -> Selected:
    """A Selected on a MagicMock client whose LIST answers `listed`."""
    client = MagicMock()
    client.list_folders.return_value = [
        (
            tuple(flag.encode() for flag in folder.flags),
            folder.delimiter.encode() if folder.delimiter else None,
            folder.path,
        )
        for folder in listed
    ]
    context = MailboxContext(
        client=client,
        path=path,
        read_only=False,
        uid_validity=1,
        exists=3,
        uid_next=None,
        account="a",
    )
    return Selected(context, delimiter)


def listed(path: str, *flags: str) -> Folder:
    return Folder(path, ".", None, flags)


FOLDERS = (
    listed("INBOX"),
    listed("Trash", "\\Trash"),
    listed("Sent", "\\Sent"),
    listed("Drafts", "\\Drafts"),
    listed("Lists"),
    listed("Lists.Nerds"),
)


class TestActionExecution:
    """Each action on the selected folder, through mailroom-core's operations."""

    def test_selected_joins_paths_with_the_delimiter(self) -> None:
        assert selected(delimiter="/").path(Path("Lists") / "Nerds") == "Lists/Nerds"
        assert selected(delimiter=None).path(Path("Lists")) == "Lists"

    def test_selected_lists_the_folders_once(self) -> None:
        on = selected("INBOX", ".", *FOLDERS)
        assert [f.path for f in on.folders()] == [f.path for f in on.folders()]
        on.context.client.list_folders.assert_called_once_with()

    def test_move_to_moves_with_the_clients_checks(self) -> None:
        on = selected("INBOX", ".", *FOLDERS)
        with patch("mailroom_core.filters.actions.move.move_uids") as move:
            MoveTo(Path("Lists") / "Nerds").execute(on, [1, 2, 3])
        move.assert_called_once_with(on.context, [1, 2, 3], "Lists.Nerds")

    def test_move_to_an_unknown_folder_is_mailbox_missing(self) -> None:
        on = selected("INBOX", ".", *FOLDERS)
        with (
            patch("mailroom_core.filters.actions.move.move_uids") as move,
            pytest.raises(MailError) as raised,
        ):
            MoveTo(Path("Nowhere")).execute(on, [1])
        assert raised.value.code == "mailbox_missing"
        move.assert_not_called()

    @pytest.mark.parametrize("target", ["Drafts", "Sent"])
    def test_move_to_drafts_or_sent_is_refused(self, target: str) -> None:
        on = selected("INBOX", ".", *FOLDERS)
        with (
            patch("mailroom_core.filters.actions.move.move_uids") as move,
            pytest.raises(MailError) as raised,
        ):
            MoveTo(Path(target)).execute(on, [1])
        assert raised.value.code == "invalid_argument"
        move.assert_not_called()

    @pytest.mark.parametrize(
        ("source", "action"), [("Lists", MoveTo(Path("Lists"))), ("Trash", Trash())]
    )
    def test_a_move_into_the_filtered_folder_is_skipped(self, source: str, action: Action) -> None:
        on = selected(source, ".", *FOLDERS)
        with patch("mailroom_core.filters.actions.move.move_uids") as move:
            action.execute(on, [1])
        move.assert_not_called()

    def test_trash_finds_the_special_use_trash(self) -> None:
        on = selected("INBOX", ".", *FOLDERS)
        with patch("mailroom_core.filters.actions.move.move_uids") as move:
            Trash().execute(on, [1, 2])
        move.assert_called_once_with(on.context, [1, 2], "Trash")

    def test_trash_to_a_named_folder(self) -> None:
        on = selected("INBOX", ".", *FOLDERS, listed("Bin"))
        with patch("mailroom_core.filters.actions.move.move_uids") as move:
            Trash(Path("Bin")).execute(on, [1])
        move.assert_called_once_with(on.context, [1], "Bin")

    def test_trash_without_a_trash_folder_is_unsupported(self) -> None:
        on = selected("INBOX", ".", listed("INBOX"))
        with pytest.raises(MailError) as raised:
            Trash().execute(on, [1])
        assert raised.value.code == "unsupported"

    @pytest.mark.parametrize(
        ("action", "method", "flag"),
        [
            (MarkAsRead(), "add_flags", b"\\Seen"),
            (MarkAsUnread(), "remove_flags", b"\\Seen"),
            (Star(), "add_flags", b"\\Flagged"),
            (Unstar(), "remove_flags", b"\\Flagged"),
            (MarkAsAnswered(), "add_flags", b"\\Answered"),
            (MarkAsUnanswered(), "remove_flags", b"\\Answered"),
        ],
    )
    def test_flag_actions_store_flags(self, action: Action, method: str, flag: bytes) -> None:
        on = selected()
        action.execute(on, [1, 2, 3])
        getattr(on.context.client, method).assert_called_once_with([1, 2, 3], [flag], silent=True)

    def test_delete_expunges_exactly_those_uids(self) -> None:
        on = selected()
        Delete().execute(on, [1, 2, 3])
        client = on.context.client
        client.add_flags.assert_called_once_with([1, 2, 3], [b"\\Deleted"], silent=True)
        client.uid_expunge.assert_called_once_with([1, 2, 3])
        client.expunge.assert_not_called()

    def test_delete_without_uidplus_is_refused_before_marking(self) -> None:
        on = selected()
        on.context.client.has_capability.return_value = False
        with pytest.raises(MailError) as raised:
            Delete().execute(on, [1])
        assert raised.value.code == "unsupported"
        on.context.client.add_flags.assert_not_called()

    def test_noop_execution(self) -> None:
        on = selected()
        NoOp().execute(on, [1, 2, 3])
        assert on.context.client.method_calls == []
