"""Tests for action classes."""

from unittest.mock import MagicMock

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.actions import (
    Action,
    Delete,
    MarkAsAnswered,
    MarkAsRead,
    MarkAsUnanswered,
    MarkAsUnread,
    MoveTo,
    Star,
    Trash,
    Unstar,
)


class TestActionCreation:
    """Test action class creation."""

    def test_move_to_creation(self) -> None:
        """Test MoveTo action creation."""
        action = MoveTo("TestFolder")
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


class TestActionChaining:
    """Test action chaining with & operator."""

    def test_action_chaining(self) -> None:
        """Test chaining two actions together."""
        action1 = MarkAsRead()
        action2 = MoveTo("Folder")
        chained = action1 & action2
        assert isinstance(chained, Action)
        assert "mark as read" in chained.name
        assert "move to Folder" in chained.name

    def test_action_chaining_execution(self, mock_account: EMailAccount) -> None:
        """Test that chained actions execute both functions."""
        call_order: list[str] = []

        def action1_func(account: EMailAccount, msgids: list[int]) -> None:
            call_order.append("action1")

        def action2_func(account: EMailAccount, msgids: list[int]) -> None:
            call_order.append("action2")

        action1 = Action(action1_func, "action1")
        action2 = Action(action2_func, "action2")
        chained = action1 & action2

        chained.execute(mock_account, [1, 2, 3])
        assert call_order == ["action1", "action2"]


class TestActionExecution:
    """Test action execution."""

    def test_move_to_execution(self, mock_account: EMailAccount) -> None:
        """Test MoveTo action execution."""
        mock_account.connection.move = MagicMock()
        action = MoveTo("TestFolder")
        action.execute(mock_account, [1, 2, 3])
        mock_account.connection.move.assert_called_once_with([1, 2, 3], "TestFolder")

    def test_mark_as_read_execution(self, mock_account: EMailAccount) -> None:
        """Test MarkAsRead action execution."""
        mock_account.connection.add_flags = MagicMock()
        action = MarkAsRead()
        action.execute(mock_account, [1, 2, 3])
        mock_account.connection.add_flags.assert_called_once_with([1, 2, 3], [b"\\Seen"])

    def test_mark_as_unread_execution(self, mock_account: EMailAccount) -> None:
        """Test MarkAsUnread action execution."""
        mock_account.connection.remove_flags = MagicMock()
        action = MarkAsUnread()
        action.execute(mock_account, [1, 2, 3])
        mock_account.connection.remove_flags.assert_called_once_with([1, 2, 3], [b"\\Seen"])

    def test_trash_execution(self, mock_account: EMailAccount) -> None:
        """Test Trash action execution."""
        from unittest.mock import patch

        mock_account.connection.find_special_folder = MagicMock(return_value="Trash")
        mock_account.connection.move = MagicMock()
        action = Trash()

        with patch("imap_thingy.filters.actions.imap.imapclient") as mock_imapclient:
            mock_imapclient.TRASH = "TRASH"
            action.execute(mock_account, [1, 2, 3])

        mock_account.connection.find_special_folder.assert_called_once()
        mock_account.connection.move.assert_called_once_with([1, 2, 3], "Trash")

    def test_mark_as_answered_execution(self, mock_account: EMailAccount) -> None:
        """Test MarkAsAnswered action execution."""
        mock_account.connection.add_flags = MagicMock()
        action = MarkAsAnswered()
        action.execute(mock_account, [1, 2, 3])
        mock_account.connection.add_flags.assert_called_once_with([1, 2, 3], [b"\\Answered"])

    def test_mark_as_unanswered_execution(self, mock_account: EMailAccount) -> None:
        """Test MarkAsUnanswered action execution."""
        mock_account.connection.remove_flags = MagicMock()
        action = MarkAsUnanswered()
        action.execute(mock_account, [1, 2, 3])
        mock_account.connection.remove_flags.assert_called_once_with([1, 2, 3], [b"\\Answered"])

    def test_star_execution(self, mock_account: EMailAccount) -> None:
        """Test Star action execution."""
        mock_account.connection.add_flags = MagicMock()
        action = Star()
        action.execute(mock_account, [1, 2, 3])
        mock_account.connection.add_flags.assert_called_once_with([1, 2, 3], [b"\\Flagged"])

    def test_unstar_execution(self, mock_account: EMailAccount) -> None:
        """Test Unstar action execution."""
        mock_account.connection.remove_flags = MagicMock()
        action = Unstar()
        action.execute(mock_account, [1, 2, 3])
        mock_account.connection.remove_flags.assert_called_once_with([1, 2, 3], [b"\\Flagged"])

    def test_delete_execution(self, mock_account: EMailAccount) -> None:
        """Test Delete action execution."""
        mock_account.connection.add_flags = MagicMock()
        mock_account.connection.expunge = MagicMock()
        action = Delete()
        action.execute(mock_account, [1, 2, 3])
        mock_account.connection.add_flags.assert_called_once_with([1, 2, 3], [b"\\Deleted"])
        mock_account.connection.expunge.assert_called_once_with([1, 2, 3])
