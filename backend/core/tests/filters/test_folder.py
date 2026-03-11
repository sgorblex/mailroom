"""Tests for Folder (path + account) and run API."""

from unittest.mock import MagicMock, patch

from imap_thingy.accounts import Account, Folder, Path
from imap_thingy.core import Message
from imap_thingy.filters import Anything, Filter, MoveTo, SubjectMatches
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
        conn.logout.assert_called_once()
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

        assert mock_account.connect.call_count == 1
        conn.logout.assert_called_once()
        assert conn.search.call_count == 2
        assert conn.move.call_count == 2

    def test_folder_run_fetch_cache_two_non_efficient_filters(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        conn.move = MagicMock()

        fetch_calls: list[list[int]] = []

        def record_fetch(client: object, msg_ids: list[int]) -> dict[int, Message]:
            fetch_calls.append(list(msg_ids))
            result: dict[int, Message] = {}
            for mid in msg_ids:
                parsed = MagicMock()
                parsed.subject = "one" if mid == 1 else ("four" if mid == 4 else "other")
                result[mid] = Message(id=mid, parsed=parsed, flags=[])
            return result

        with (
            patch("imap_thingy.accounts.account.search_mail", side_effect=[[1, 2, 3], [2, 3, 4]]),
            patch("imap_thingy.accounts.account.fetch_mail", side_effect=record_fetch),
        ):
            f1 = Filter(SubjectMatches("one"), MoveTo(Path("D1")))
            f2 = Filter(SubjectMatches("four"), MoveTo(Path("D2")))
            folder.run([f1, f2], dry_run=False)

        assert len(fetch_calls) == 2
        assert fetch_calls[0] == [1, 2, 3]
        assert fetch_calls[1] == [4]
