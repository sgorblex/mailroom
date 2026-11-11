"""Tests for filter classes."""

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from mailroom_core.filters.actions.flags import MarkAsRead
from mailroom_core.filters.actions.move import MoveTo
from mailroom_core.filters.criteria.address import FromIs, ToIs
from mailroom_core.filters.criteria.criterion import Anything
from mailroom_core.filters.criteria.subject import SubjectIs, SubjectMatches
from mailroom_core.filters.filter import Filter, If
from mailroom_core.filters.path import Path

if TYPE_CHECKING:
    from conftest import MockEMailAccount
    from mailroom_core.filters.account import Account


def _body_fetches(conn: MagicMock) -> int:
    """Count the message-body fetches (the report's ENVELOPE fetches are not counted)."""
    return sum(1 for c in conn.fetch.call_args_list if c.args[1] == ["BODY.PEEK[]", "FLAGS"])


def test_filter_construction() -> None:
    """Both ways of building a filter give one with a criterion and an action."""
    f = Filter(ToIs("members@club.example.org"), MarkAsRead() + MoveTo(Path("Club")))
    assert f.criterion is not None
    assert f.action is not None
    f2 = If(FromIs("x@y.edu") & SubjectMatches(r"List Digest")).then(
        MarkAsRead() + MoveTo(Path("List"))
    )
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
        mock_account.connect.return_value._raw_command_untagged = MagicMock(return_value=[b"1 2 3"])

        criterion = Anything()
        action = MoveTo(Path("TestFolder"))
        filter_obj = Filter(criterion, action)

        with patch.object(action, "execute", new_callable=MagicMock) as mock_execute:
            folder.run(filter_obj, dry_run=True)

            mock_account.connect.assert_called_once_with()
            mock_account.connect.return_value._raw_command_untagged.assert_called_once()
            mock_execute.assert_not_called()

    def test_filter_run_execution(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        conn._raw_command_untagged = MagicMock(return_value=[b"1 2 3"])
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
        conn._raw_command_untagged.assert_called_once()
        conn.move.assert_called_once_with([1, 2, 3], "TestFolder")

    def test_filter_run_empty_results(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        mock_account.connect.return_value._raw_command_untagged = MagicMock(return_value=[b""])

        criterion = Anything()
        action = MoveTo(Path("TestFolder"))
        filter_obj = Filter(criterion, action)

        with patch.object(action, "execute", new_callable=MagicMock) as mock_execute:
            folder.run(filter_obj, dry_run=False)

            mock_account.connect.assert_called_once_with()
            mock_account.connect.return_value._raw_command_untagged.assert_called_once()
            mock_execute.assert_not_called()

    def test_filter_with_chained_action(self, mock_account: Account) -> None:
        """Test Filter(criterion, action1 + action2) builds filter with chained action."""

        criterion = Anything()
        action1 = MoveTo(Path("D1"))
        action2 = MarkAsRead()
        f = Filter(criterion, action1 + action2)
        assert isinstance(f, Filter)
        assert f.criterion is criterion
        assert "mark as read" in f.action.name

    def test_from_and_subject_is_fetches_and_exact_match(
        self, mock_account: MockEMailAccount
    ) -> None:
        """FromIs & SubjectIs fetches candidates and applies client-side exact subject match."""
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        sender = "team@insurer.example.com"
        exact_subject = "Important - Claims settlement Information"
        conn._raw_command_untagged = MagicMock(return_value=[b"1 2"])
        conn.fetch = MagicMock(
            return_value={
                1: {
                    b"BODY[]": f"From: {sender}\r\nSubject: {exact_subject}\r\n\r\n".encode(),
                    b"FLAGS": [],
                },
                2: {
                    b"BODY[]": f"From: {sender}\r\nSubject: Re: {exact_subject}\r\n\r\n".encode(),
                    b"FLAGS": [],
                },
            }
        )
        conn.move = MagicMock()

        filter_obj = Filter(
            FromIs(sender) & SubjectIs(exact_subject), MoveTo(Path("Personal") / "Health")
        )
        folder.run(filter_obj, dry_run=False)

        assert _body_fetches(conn) == 1
        conn.move.assert_called_once_with([1], "Personal.Health")

    def test_from_and_subject_is_not_imap_only_efficient_path(
        self, mock_account: MockEMailAccount
    ) -> None:
        """SubjectIs must not skip fetch when IMAP returns IDs (unlike old efficient path)."""
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        conn._raw_command_untagged = MagicMock(return_value=[b"1"])
        conn.fetch = MagicMock(
            return_value={
                1: {
                    b"BODY[]": b"From: a@b.com\r\nSubject: Exact Subject\r\n\r\n",
                    b"FLAGS": [],
                },
            }
        )

        filter_obj = Filter(SubjectIs("Exact Subject"), MarkAsRead())
        with patch.object(filter_obj.action, "execute", new_callable=MagicMock) as mock_execute:
            folder.run(filter_obj, dry_run=False)

        assert _body_fetches(conn) == 1
        mock_execute.assert_called_once_with(conn, [1], delimiter=mock_account.delimiter)
