"""Tests for Folder (path + account) and run API."""

from unittest.mock import MagicMock

from imap_thingy.accounts import Account, Folder, Path
from imap_thingy.filters import Anything, Filter, MoveTo
from tests.conftest import MockEMailAccount


class TestFolder:
    """Test Folder (path-only) class."""

    def test_folder_creation(self) -> None:
        folder = Path("Cool")
        assert folder.segments == ["Cool"]

    def test_folder_truediv(self) -> None:
        folder = Path("Cool") / "Sub"
        assert folder.segments == ["Cool", "Sub"]

    def test_folder_empty_inbox(self) -> None:
        folder = Path([])
        assert folder.segments == []


class TestAccountFolder:
    """Test Folder (account + path) and run API."""

    def test_account_folder_creation(self, mock_account: Account) -> None:
        folder = Folder(mock_account, Path("Cool"))
        assert folder.account == mock_account
        assert folder.imap_name() == "Cool"

    def test_account_folder_truediv(self, mock_account: Account) -> None:
        folder = mock_account / "Cool"
        assert isinstance(folder, Folder)
        assert folder.account == mock_account
        assert folder.imap_name() == "Cool"

    def test_account_folder_truediv_deeper(self, mock_account: Account) -> None:
        mock_account.delimiter = "/"
        folder = mock_account / "Cool" / "Sub"
        assert folder.imap_name() == "Cool/Sub"

    def test_account_folder_imap_name_delimiter(self, mock_account: Account) -> None:
        mock_account.delimiter = "."
        folder = mock_account / "Cool" / "Sub"
        assert folder.imap_name() == "Cool.Sub"

    def test_account_folder_empty_segments_inbox(self, mock_account: Account) -> None:
        folder = Folder(mock_account, Path([]))
        assert folder.imap_name() == "INBOX"


class TestFolderRun:
    """Test folder.run(filter) and folder.run(filters)."""

    def test_folder_run_single_filter(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        conn.search = MagicMock(return_value=[1])
        conn.fetch = MagicMock(return_value={1: {b"BODY[]": b"From: x@y.com\r\n\r\nbody", b"FLAGS": []}})
        conn.move = MagicMock()

        f = Filter(Anything(), MoveTo(Path("Dest")))
        folder.run(f, dry_run=False)

        mock_account.connect.assert_called_once_with()
        conn.search.assert_called_once()
        conn.move.assert_called_once_with([1], "Dest")

    def test_folder_run_iterable_filters(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        conn.search = MagicMock(side_effect=[[1], [2]])
        conn.fetch = MagicMock(
            side_effect=[
                {1: {b"BODY[]": b"From: a@b.com\r\n\r\n", b"FLAGS": []}},
                {2: {b"BODY[]": b"From: c@d.com\r\n\r\n", b"FLAGS": []}},
            ]
        )
        conn.move = MagicMock()

        f1 = Filter(Anything(), MoveTo(Path("D1")))
        f2 = Filter(Anything(), MoveTo(Path("D2")))
        folder.run([f1, f2], dry_run=False)

        assert mock_account.connect.call_count == 2
        assert conn.search.call_count == 2
        assert conn.move.call_count == 2
