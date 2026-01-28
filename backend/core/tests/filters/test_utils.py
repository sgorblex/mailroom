"""Tests for utility functions."""

from unittest.mock import MagicMock, patch

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters import CriterionFilter, SelectAll, apply_filters
from imap_thingy.filters.actions import MoveTo
from imap_thingy.filters.utils import all_unique_accounts


class TestAllUniqueAccounts:
    """Test all_unique_accounts utility function."""

    def test_single_account(self, mock_account: EMailAccount) -> None:
        """Test with a single account."""
        filter_obj = CriterionFilter(mock_account, SelectAll(), MoveTo("Folder"))
        accounts = all_unique_accounts([filter_obj])
        assert accounts == {mock_account}

    def test_multiple_filters_same_account(self, mock_account: EMailAccount) -> None:
        """Test with multiple filters using the same account."""
        filter1 = CriterionFilter(mock_account, SelectAll(), MoveTo("Folder1"))
        filter2 = CriterionFilter(mock_account, SelectAll(), MoveTo("Folder2"))
        accounts = all_unique_accounts([filter1, filter2])
        assert accounts == {mock_account}
        assert len(accounts) == 1

    def test_multiple_accounts(self) -> None:
        """Test with multiple different accounts."""
        account1 = EMailAccount("acc1", "host1", 993, "user1", "pass1")
        account2 = EMailAccount("acc2", "host2", 993, "user2", "pass2")
        filter1 = CriterionFilter(account1, SelectAll(), MoveTo("Folder1"))
        filter2 = CriterionFilter(account2, SelectAll(), MoveTo("Folder2"))
        accounts = all_unique_accounts([filter1, filter2])
        assert account1 in accounts
        assert account2 in accounts
        assert len(accounts) == 2


class TestApplyFilters:
    """Test apply_filters utility function."""

    def test_apply_filters_calls_apply(self, mock_account: EMailAccount) -> None:
        """Test that apply_filters calls apply on each filter."""
        filter1 = CriterionFilter(mock_account, SelectAll(), MoveTo("Folder1"))
        filter2 = CriterionFilter(mock_account, SelectAll(), MoveTo("Folder2"))

        with patch.object(filter1, "apply", new_callable=MagicMock) as mock1, patch.object(filter2, "apply", new_callable=MagicMock) as mock2:
            apply_filters([filter1, filter2], dry_run=True)

            mock1.assert_called_once_with(dry_run=True)
            mock2.assert_called_once_with(dry_run=True)

    def test_apply_filters_with_dry_run(self, mock_account: EMailAccount) -> None:
        """Test apply_filters with dry_run parameter."""
        filter_obj = CriterionFilter(mock_account, SelectAll(), MoveTo("Folder"))

        with patch.object(filter_obj, "apply", new_callable=MagicMock) as mock_apply:
            apply_filters([filter_obj], dry_run=True)
            mock_apply.assert_called_once_with(dry_run=True)

            apply_filters([filter_obj], dry_run=False)
            assert mock_apply.call_count == 2
            mock_apply.assert_called_with(dry_run=False)
