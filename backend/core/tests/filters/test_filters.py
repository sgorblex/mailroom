"""Tests for filter classes."""

from unittest.mock import MagicMock, patch

from imap_thingy.accounts import Account, Path
from imap_thingy.filters import Anything, Filter, FromIs, If, MarkAsRead, MoveTo, SubjectMatches, ToIs
from tests.conftest import MockEMailAccount


def test_readme_imports_and_usage() -> None:
    """README example imports and filter construction work."""
    from imap_thingy.accounts import Path, accounts_from_json
    from imap_thingy.filters import Filter, MoveTo

    _ = Path("INBOX")
    _ = accounts_from_json
    f = Filter(ToIs("members@boringassociation.org"), MarkAsRead() + MoveTo(Path("Boring Association")))
    assert f.criterion is not None
    assert f.action is not None
    f2 = If(FromIs("x@y.edu") & SubjectMatches(r"List Digest")).then(MarkAsRead() + MoveTo(Path("List")))
    assert f2.criterion is not None
    assert f2.action is not None


class TestFilter:
    """Test Filter (criterion + action, run on folder)."""

    def test_filter_creation(self, mock_account: Account) -> None:
        criterion = Anything()
        action = MoveTo(Path("TestFolder"))
        filter_obj = Filter(criterion, action)
        assert filter_obj.criterion == criterion
        assert filter_obj.action == action

    def test_filter_run_dry_run(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        mock_account.connect.return_value.search = MagicMock(return_value=[1, 2, 3])

        criterion = Anything()
        action = MoveTo(Path("TestFolder"))
        filter_obj = Filter(criterion, action)

        with patch.object(action, "execute", new_callable=MagicMock) as mock_execute:
            folder.run(filter_obj, dry_run=True)

            mock_account.connect.assert_called_once_with()
            mock_account.connect.return_value.search.assert_called_once()
            mock_execute.assert_not_called()

    def test_filter_run_execution(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        conn.search = MagicMock(return_value=[1, 2, 3])
        conn.fetch = MagicMock(
            return_value={
                1: {b"BODY[]": b"From: a@b.com\r\n\r\n", b"FLAGS": []},
                2: {b"BODY[]": b"From: c@d.com\r\n\r\n", b"FLAGS": []},
                3: {b"BODY[]": b"From: e@f.com\r\n\r\n", b"FLAGS": []},
            }
        )
        conn.move = MagicMock()

        criterion = Anything()
        action = MoveTo(Path("TestFolder"))
        filter_obj = Filter(criterion, action)

        folder.run(filter_obj, dry_run=False)

        mock_account.connect.assert_called_once_with()
        conn.search.assert_called_once()
        conn.move.assert_called_once_with([1, 2, 3], "TestFolder")

    def test_filter_run_empty_results(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        mock_account.connect.return_value.search = MagicMock(return_value=[])

        criterion = Anything()
        action = MoveTo(Path("TestFolder"))
        filter_obj = Filter(criterion, action)

        with patch.object(action, "execute", new_callable=MagicMock) as mock_execute:
            folder.run(filter_obj, dry_run=False)

            mock_account.connect.assert_called_once_with()
            mock_account.connect.return_value.search.assert_called_once()
            mock_execute.assert_not_called()

    def test_filter_with_chained_action(self, mock_account: Account) -> None:
        """Test Filter(criterion, action1 + action2) builds filter with chained action."""

        criterion = Anything()
        action1 = MoveTo(Path("D1"))
        action2 = MarkAsRead()
        f = Filter(criterion, action1 + action2)
        assert isinstance(f, Filter)
        assert f.criterion is criterion
        assert "MarkAsRead" in f.action.name
