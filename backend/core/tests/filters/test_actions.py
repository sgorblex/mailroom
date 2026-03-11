"""Tests for action classes."""

from collections.abc import Iterable
from unittest.mock import MagicMock

from imap_thingy.accounts import Path
from imap_thingy.filters import (
    Action,
    Delete,
    MarkAsAnswered,
    MarkAsRead,
    MarkAsUnanswered,
    MarkAsUnread,
    MoveTo,
    NoOp,
    Star,
    Trash,
    Unstar,
)
from tests.conftest import MockEMailAccount


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
        assert action.name == "MarkAsRead"

    def test_mark_as_unread_creation(self) -> None:
        """Test MarkAsUnread action creation."""
        action = MarkAsUnread()
        assert isinstance(action, Action)
        assert action.name == "MarkAsUnread"

    def test_mark_as_answered_creation(self) -> None:
        """Test MarkAsAnswered action creation."""
        action = MarkAsAnswered()
        assert isinstance(action, Action)
        assert action.name == "MarkAsAnswered"

    def test_mark_as_unanswered_creation(self) -> None:
        """Test MarkAsUnanswered action creation."""
        action = MarkAsUnanswered()
        assert isinstance(action, Action)
        assert action.name == "MarkAsUnanswered"

    def test_star_creation(self) -> None:
        """Test Star action creation."""
        action = Star()
        assert isinstance(action, Action)
        assert action.name == "Star"

    def test_unstar_creation(self) -> None:
        """Test Unstar action creation."""
        action = Unstar()
        assert isinstance(action, Action)
        assert action.name == "Unstar"

    def test_delete_creation(self) -> None:
        """Test Delete action creation."""
        action = Delete()
        assert isinstance(action, Action)
        assert action.name == "Delete"

    def test_noop_creation(self) -> None:
        """Test NoOp action creation."""
        action = NoOp()
        assert isinstance(action, Action)
        assert action.name == "NoOp"


class TestActionChaining:
    """Test action chaining with + operator."""

    def test_action_chaining(self) -> None:
        """Test chaining two actions together."""
        action1 = MarkAsRead()
        action2 = MoveTo(Path("Folder"))
        chained = action1 + action2
        assert isinstance(chained, Action)
        assert "MarkAsRead" in chained.name
        assert "move to Folder" in chained.name

    def test_action_then_method(self) -> None:
        """Test a.then(other) returns Action."""
        a1 = MarkAsRead()
        a2 = MoveTo(Path("Folder"))
        chained = a1.then(a2)
        assert isinstance(chained, Action)
        assert "MarkAsRead" in chained.name
        assert "move to Folder" in chained.name

    def test_action_chaining_execution(self, mock_account: MockEMailAccount) -> None:
        """Test that chained actions execute both functions."""
        call_order: list[str] = []

        def action1_func(conn: object, msgids: Iterable[int], *, delimiter: str = "/") -> None:
            call_order.append("action1")

        def action2_func(conn: object, msgids: Iterable[int], *, delimiter: str = "/") -> None:
            call_order.append("action2")

        action1 = Action(action1_func, "action1")
        action2 = Action(action2_func, "action2")
        chained = action1 + action2

        chained.execute(mock_account.connect.return_value, [1, 2, 3])
        assert call_order == ["action1", "action2"]


class TestActionExecution:
    """Test action execution."""

    def test_move_to_execution(self, mock_account: MockEMailAccount) -> None:
        """Test MoveTo action execution."""
        mock_account.connect.return_value.move = MagicMock()
        action = MoveTo(Path("TestFolder"))
        action.execute(mock_account.connect.return_value, [1, 2, 3])
        mock_account.connect.return_value.move.assert_called_once_with([1, 2, 3], "TestFolder")

    def test_mark_as_read_execution(self, mock_account: MockEMailAccount) -> None:
        """Test MarkAsRead action execution."""
        mock_account.connect.return_value.add_flags = MagicMock()
        action = MarkAsRead()
        action.execute(mock_account.connect.return_value, [1, 2, 3])
        mock_account.connect.return_value.add_flags.assert_called_once_with([1, 2, 3], [b"\\Seen"])

    def test_mark_as_unread_execution(self, mock_account: MockEMailAccount) -> None:
        """Test MarkAsUnread action execution."""
        mock_account.connect.return_value.remove_flags = MagicMock()
        action = MarkAsUnread()
        action.execute(mock_account.connect.return_value, [1, 2, 3])
        mock_account.connect.return_value.remove_flags.assert_called_once_with([1, 2, 3], [b"\\Seen"])

    def test_trash_execution(self, mock_account: MockEMailAccount) -> None:
        """Test Trash action execution."""
        from unittest.mock import patch

        mock_account.connect.return_value.find_special_folder = MagicMock(return_value="Trash")
        mock_account.connect.return_value.move = MagicMock()
        action = Trash()

        with patch("imap_thingy.filters.actions.move.TRASH", "TRASH"):
            action.execute(mock_account.connect.return_value, [1, 2, 3])

        mock_account.connect.return_value.find_special_folder.assert_called_once()
        mock_account.connect.return_value.move.assert_called_once_with([1, 2, 3], "Trash")

    def test_mark_as_answered_execution(self, mock_account: MockEMailAccount) -> None:
        """Test MarkAsAnswered action execution."""
        mock_account.connect.return_value.add_flags = MagicMock()
        action = MarkAsAnswered()
        action.execute(mock_account.connect.return_value, [1, 2, 3])
        mock_account.connect.return_value.add_flags.assert_called_once_with([1, 2, 3], [b"\\Answered"])

    def test_mark_as_unanswered_execution(self, mock_account: MockEMailAccount) -> None:
        """Test MarkAsUnanswered action execution."""
        mock_account.connect.return_value.remove_flags = MagicMock()
        action = MarkAsUnanswered()
        action.execute(mock_account.connect.return_value, [1, 2, 3])
        mock_account.connect.return_value.remove_flags.assert_called_once_with([1, 2, 3], [b"\\Answered"])

    def test_star_execution(self, mock_account: MockEMailAccount) -> None:
        """Test Star action execution."""
        mock_account.connect.return_value.add_flags = MagicMock()
        action = Star()
        action.execute(mock_account.connect.return_value, [1, 2, 3])
        mock_account.connect.return_value.add_flags.assert_called_once_with([1, 2, 3], [b"\\Flagged"])

    def test_unstar_execution(self, mock_account: MockEMailAccount) -> None:
        """Test Unstar action execution."""
        mock_account.connect.return_value.remove_flags = MagicMock()
        action = Unstar()
        action.execute(mock_account.connect.return_value, [1, 2, 3])
        mock_account.connect.return_value.remove_flags.assert_called_once_with([1, 2, 3], [b"\\Flagged"])

    def test_delete_execution(self, mock_account: MockEMailAccount) -> None:
        """Test Delete action execution."""
        mock_account.connect.return_value.add_flags = MagicMock()
        mock_account.connect.return_value.expunge = MagicMock()
        action = Delete()
        action.execute(mock_account.connect.return_value, [1, 2, 3])
        mock_account.connect.return_value.add_flags.assert_called_once_with([1, 2, 3], [b"\\Deleted"])
        mock_account.connect.return_value.expunge.assert_called_once_with([1, 2, 3])

    def test_noop_execution(self, mock_account: MockEMailAccount) -> None:
        """Test NoOp action does nothing."""
        action = NoOp()
        action.execute(mock_account.connect.return_value, [1, 2, 3])
