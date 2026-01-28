"""Tests for filter classes."""

from unittest.mock import MagicMock, patch

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters import CriterionFilter, DuplicateFilter, MoveTo
from imap_thingy.filters.basic_filters import MoveIfFromFilter, MoveIfToFilter, ProcessHandledFilter
from imap_thingy.filters.criteria import SelectAll


class TestCriterionFilter:
    """Test CriterionFilter class."""

    def test_criterion_filter_creation(self, mock_account: EMailAccount) -> None:
        """Test CriterionFilter creation."""
        criterion = SelectAll()
        action = MoveTo("TestFolder")
        filter_obj = CriterionFilter(mock_account, criterion, action)
        assert filter_obj.account == mock_account
        assert filter_obj.criterion == criterion
        assert filter_obj.action == action
        assert filter_obj.base_folder == "INBOX"

    def test_criterion_filter_with_custom_folder(self, mock_account: EMailAccount) -> None:
        """Test CriterionFilter with custom base folder."""
        criterion = SelectAll()
        action = MoveTo("TestFolder")
        filter_obj = CriterionFilter(mock_account, criterion, action, base_folder="Archive")
        assert filter_obj.base_folder == "Archive"

    def test_criterion_filter_dry_run(self, mock_account: EMailAccount) -> None:
        """Test CriterionFilter in dry-run mode."""
        mock_account.connection.select_folder = MagicMock()
        mock_account.connection.search = MagicMock(return_value=[1, 2, 3])

        criterion = SelectAll()
        action = MoveTo("TestFolder")
        filter_obj = CriterionFilter(mock_account, criterion, action)

        with patch.object(action, "execute", new_callable=MagicMock) as mock_execute:
            filter_obj.apply(dry_run=True)

            mock_account.connection.select_folder.assert_called_once_with("INBOX", readonly=False)
            mock_account.connection.search.assert_called_once()
            mock_execute.assert_not_called()

    def test_criterion_filter_execution(self, mock_account: EMailAccount) -> None:
        """Test CriterionFilter execution (non-dry-run)."""
        mock_account.connection.select_folder = MagicMock()
        mock_account.connection.search = MagicMock(return_value=[1, 2, 3])
        mock_account.connection.move = MagicMock()

        criterion = SelectAll()
        action = MoveTo("TestFolder")
        filter_obj = CriterionFilter(mock_account, criterion, action)

        filter_obj.apply(dry_run=False)

        mock_account.connection.select_folder.assert_called_once_with("INBOX", readonly=False)
        mock_account.connection.search.assert_called_once()
        mock_account.connection.move.assert_called_once_with([1, 2, 3], "TestFolder")


class TestBasicFilters:
    """Test basic filter classes."""

    def test_move_if_from_filter_creation(self, mock_account: EMailAccount) -> None:
        """Test MoveIfFromFilter creation."""
        filter_obj = MoveIfFromFilter(mock_account, "sender@example.com", "Folder")
        assert isinstance(filter_obj, CriterionFilter)
        assert filter_obj.base_folder == "INBOX"

    def test_move_if_from_filter_with_mark_read(self, mock_account: EMailAccount) -> None:
        """Test MoveIfFromFilter with mark_read option."""
        filter_obj = MoveIfFromFilter(mock_account, "sender@example.com", "Folder", mark_read=True)
        assert "mark as read" in filter_obj.action.name

    def test_move_if_from_filter_without_mark_read(self, mock_account: EMailAccount) -> None:
        """Test MoveIfFromFilter without mark_read."""
        filter_obj = MoveIfFromFilter(mock_account, "sender@example.com", "Folder", mark_read=False)
        assert "mark as read" not in filter_obj.action.name

    def test_move_if_to_filter_creation(self, mock_account: EMailAccount) -> None:
        """Test MoveIfToFilter creation."""
        filter_obj = MoveIfToFilter(mock_account, "recipient@example.com", "Folder")
        assert isinstance(filter_obj, CriterionFilter)

    def test_move_if_to_filter_with_cc_bcc(self, mock_account: EMailAccount) -> None:
        """Test MoveIfToFilter with CC and BCC options."""
        filter_obj = MoveIfToFilter(mock_account, "recipient@example.com", "Folder", include_cc=True, include_bcc=True)
        assert isinstance(filter_obj, CriterionFilter)

    def test_move_if_to_filter_with_mark_read(self, mock_account: EMailAccount) -> None:
        """Test MoveIfToFilter with mark_read option."""
        filter_obj = MoveIfToFilter(mock_account, "recipient@example.com", "Folder", mark_read=True)
        assert "mark as read" in filter_obj.action.name

    def test_move_if_to_filter_without_mark_read(self, mock_account: EMailAccount) -> None:
        """Test MoveIfToFilter without mark_read."""
        filter_obj = MoveIfToFilter(mock_account, "recipient@example.com", "Folder", mark_read=False)
        assert "mark as read" not in filter_obj.action.name

    def test_process_handled_filter_creation(self, mock_account: EMailAccount) -> None:
        """Test ProcessHandledFilter creation."""

        criterion = SelectAll()
        action = MoveTo("Processed")
        filter_obj = ProcessHandledFilter(mock_account, criterion, action)
        assert isinstance(filter_obj, CriterionFilter)
        assert filter_obj.criterion.imap_query is not None
        assert "FLAGGED" in filter_obj.criterion.imap_query

    def test_process_handled_filter_unstars(self, mock_account: EMailAccount) -> None:
        """Test that ProcessHandledFilter unstars messages."""

        criterion = SelectAll()
        action = MoveTo("Processed")
        filter_obj = ProcessHandledFilter(mock_account, criterion, action)
        assert "unstar" in filter_obj.action.name

    def test_duplicate_filter_creation(self, mock_account: EMailAccount) -> None:
        """Test DuplicateFilter creation."""
        filter_obj = DuplicateFilter(mock_account)
        assert isinstance(filter_obj, CriterionFilter)
        assert filter_obj.base_folder == "INBOX"

    def test_duplicate_filter_with_custom_folder(self, mock_account: EMailAccount) -> None:
        """Test DuplicateFilter with custom base folder."""
        filter_obj = DuplicateFilter(mock_account, base_folder="Archive")
        assert filter_obj.base_folder == "Archive"

    def test_criterion_filter_empty_results(self, mock_account: EMailAccount) -> None:
        """Test CriterionFilter with no matching messages."""
        mock_account.connection.select_folder = MagicMock()
        mock_account.connection.search = MagicMock(return_value=[])

        criterion = SelectAll()
        action = MoveTo("TestFolder")
        filter_obj = CriterionFilter(mock_account, criterion, action)

        with patch.object(action, "execute", new_callable=MagicMock) as mock_execute:
            filter_obj.apply(dry_run=False)

            mock_account.connection.select_folder.assert_called_once_with("INBOX", readonly=False)
            mock_account.connection.search.assert_called_once()
            mock_execute.assert_not_called()
