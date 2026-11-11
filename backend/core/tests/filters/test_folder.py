"""Tests for Folder (path + account) and run API."""

import json
import ssl
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from imapclient.exceptions import IMAPClientError
from imapclient.response_types import Address, Envelope

from imap_thingy.accounts import Account, Folder, Path, accounts_from_json
from imap_thingy.accounts.presets import GMailAccount
from imap_thingy.core import Message
from imap_thingy.filters import (
    Action,
    Anything,
    Filter,
    FromIs,
    MarkAsRead,
    MoveTo,
    OlderThanDays,
    SubjectMatches,
)
from imap_thingy.get_mail import summarize_mail

if TYPE_CHECKING:
    import pathlib

    from tests.conftest import MockEMailAccount


class TestFolder:
    """Test Folder (path-only) class."""

    def test_folder_creation(self) -> None:
        folder = Path("Cool")
        assert folder.segments == ["Cool"]

    def test_folder_truediv(self) -> None:
        folder = Path("Cool") / "Sub"
        assert folder.segments == ["Cool", "Sub"]

    def test_folder_empty_path_is_root(self) -> None:
        folder = Path([])
        assert folder.segments == []
        assert folder.as_string() == ""

    def test_folder_empty_string_is_root(self) -> None:
        folder = Path("")
        assert folder.segments == []
        assert folder.as_string() == ""

    def test_folder_inbox_is_inbox(self) -> None:
        folder = Path("INBOX")
        assert folder.segments == ["INBOX"]
        assert folder.as_string() == "INBOX"


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

    def test_account_folder_empty_path_is_server_root(self, mock_account: Account) -> None:
        folder = Folder(mock_account, Path([]))
        assert folder.imap_name() == ""

    def test_account_folder_inbox(self, mock_account: Account) -> None:
        folder = Folder(mock_account, Path("INBOX"))
        assert folder.imap_name() == "INBOX"
        assert mock_account.inbox.path.segments == ["INBOX"]


class TestFolderRun:
    """Test folder.run(filter) and folder.run(filters)."""

    def test_folder_run_single_filter(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        conn._raw_command_untagged = MagicMock(return_value=[b"1"])
        conn.move = MagicMock()

        f = Filter(Anything(), MoveTo(Path("Dest")))
        folder.run(f, dry_run=False)

        mock_account.connect.assert_called_once_with()
        conn.logout.assert_called_once()
        conn._raw_command_untagged.assert_called_once()
        conn.move.assert_called_once_with([1], "Dest")

    def test_folder_run_iterable_filters(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        conn._raw_command_untagged = MagicMock(side_effect=[[b"1"], [b"2"]])
        conn.move = MagicMock()

        f1 = Filter(Anything(), MoveTo(Path("D1")))
        f2 = Filter(Anything(), MoveTo(Path("D2")))
        folder.run([f1, f2], dry_run=False)

        assert mock_account.connect.call_count == 1
        conn.logout.assert_called_once()
        assert conn._raw_command_untagged.call_count == 2
        assert conn.move.call_count == 2

    def test_older_than_days_cutoff_is_taken_at_each_run(
        self, mock_account: MockEMailAccount
    ) -> None:
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        conn._raw_command_untagged = MagicMock(return_value=[b""])
        f = Filter(OlderThanDays(30), MarkAsRead())

        with patch("imap_thingy.filters.criteria.date._today") as today:
            today.return_value = date(2026, 3, 31)
            folder.run(f, dry_run=False)
            today.return_value = date(2026, 4, 30)
            folder.run(f, dry_run=False)

        first, second = (c.args[1] for c in conn._raw_command_untagged.call_args_list)
        assert b"01-Mar-2026" in first
        assert b"31-Mar-2026" in second

    def test_folder_run_fetch_cache_two_non_efficient_filters(
        self, mock_account: MockEMailAccount
    ) -> None:
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

    def test_partial_fetch_skips_missing_no_key_error(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        conn.move = MagicMock()

        def fetch_return_subset(client: object, msg_ids: list[int]) -> dict[int, Message]:
            result: dict[int, Message] = {}
            for mid in msg_ids:
                if mid == 2:
                    continue
                parsed = MagicMock()
                parsed.subject = "match"
                result[mid] = Message(id=mid, parsed=parsed, flags=[])
            return result

        with (
            patch("imap_thingy.accounts.account.search_mail", return_value=[1, 2, 3]),
            patch("imap_thingy.accounts.account.fetch_mail", side_effect=fetch_return_subset),
        ):
            f = Filter(SubjectMatches("match"), MoveTo(Path("Dest")))
            folder.run(f, dry_run=False)

        conn.move.assert_called_once_with([1, 3], "Dest")

    def test_eviction_after_efficient_filter_clears_cache_for_acted_on_ids(
        self, mock_account: MockEMailAccount
    ) -> None:
        folder = mock_account / "INBOX"
        conn = mock_account.connect.return_value
        conn.move = MagicMock()
        conn.add_flags = MagicMock()

        fetch_calls: list[list[int]] = []

        def record_fetch(client: object, msg_ids: list[int]) -> dict[int, Message]:
            fetch_calls.append(list(msg_ids))
            result: dict[int, Message] = {}
            for mid in msg_ids:
                parsed = MagicMock()
                parsed.subject = "one" if mid == 1 else ("other" if mid in (2, 3) else "four")
                result[mid] = Message(id=mid, parsed=parsed, flags=[])
            return result

        with (
            patch(
                "imap_thingy.accounts.account.search_mail",
                side_effect=[[1, 2, 3], [2, 3], [2, 3, 4]],
            ),
            patch("imap_thingy.accounts.account.fetch_mail", side_effect=record_fetch),
        ):
            f1 = Filter(SubjectMatches("one"), MoveTo(Path("D1")))
            f2 = Filter(Anything(), MarkAsRead())
            f3 = Filter(SubjectMatches("other"), MoveTo(Path("D2")))
            folder.run([f1, f2, f3], dry_run=False)

        assert len(fetch_calls) == 2
        assert fetch_calls[0] == [1, 2, 3]
        assert fetch_calls[1] == [2, 3, 4]


_ENVELOPE_DATE = datetime(2026, 5, 4, 12, 30)  # noqa: DTZ001 -- imapclient returns naive local times
_ENVELOPE_DATE_UTC = _ENVELOPE_DATE.astimezone(UTC).isoformat()


def _envelope(subject: bytes, name: bytes | None, mailbox: bytes, host: bytes) -> Envelope:
    return Envelope(
        date=_ENVELOPE_DATE,
        subject=subject,
        from_=(Address(name, None, mailbox, host),),
        sender=None,
        reply_to=None,
        to=None,
        cc=None,
        bcc=None,
        in_reply_to=b"",
        message_id=b"",
    )


class TestFolderReport:
    """Test the report returned by folder.run()."""

    def _conn(self, account: MockEMailAccount, uids: bytes) -> MagicMock:
        conn: MagicMock = account.connect.return_value
        conn._raw_command_untagged = MagicMock(return_value=[uids])
        conn.fetch = MagicMock(
            return_value={
                1: {b"ENVELOPE": _envelope(b"Hello", b"Ann", b"ann", b"example.com")},
                2: {b"ENVELOPE": _envelope(b"Again", None, b"bob", b"example.com")},
            }
        )
        return conn

    def test_real_run_reports_matches_and_execution(self, mock_account: MockEMailAccount) -> None:
        conn = self._conn(mock_account, b"1 2")
        f = Filter(FromIs("ann@example.com"), MoveTo(Path("Dest")))
        report = (mock_account / "INBOX").run(f, dry_run=False)

        conn.select_folder.assert_called_once_with("INBOX", readonly=False)
        conn.move.assert_called_once_with([1, 2], "Dest")
        assert report == {
            "account": "test_account",
            "folder": ["INBOX"],
            "dry_run": False,
            "filters": [
                {
                    "spec": f.to_spec(),
                    "action": "MoveTo(folder=['Dest'])",
                    "matched": [
                        {
                            "uid": 1,
                            "from": "Ann <ann@example.com>",
                            "subject": "Hello",
                            "date": _ENVELOPE_DATE_UTC,
                        },
                        {
                            "uid": 2,
                            "from": "bob@example.com",
                            "subject": "Again",
                            "date": _ENVELOPE_DATE_UTC,
                        },
                    ],
                    "executed": True,
                }
            ],
            "error": None,
        }
        assert json.loads(json.dumps(report)) == report

    def test_dry_run_selects_read_only_and_executes_nothing(
        self, mock_account: MockEMailAccount
    ) -> None:
        conn = self._conn(mock_account, b"1")
        report = (mock_account / "INBOX").run(Filter(Anything(), MoveTo(Path("D"))), dry_run=True)

        conn.select_folder.assert_called_once_with("INBOX", readonly=True)
        conn.move.assert_not_called()
        assert report["dry_run"] is True
        (filter_report,) = report["filters"]
        assert [m["uid"] for m in filter_report["matched"]] == [1]
        assert filter_report["executed"] is False

    def test_no_match_fetches_no_envelope(self, mock_account: MockEMailAccount) -> None:
        conn = self._conn(mock_account, b"")
        report = (mock_account / "INBOX").run(Filter(Anything(), MarkAsRead()), dry_run=False)

        conn.fetch.assert_not_called()
        assert report["filters"][0]["matched"] == []
        assert report["filters"][0]["executed"] is False

    def test_custom_filter_has_no_spec(self, mock_account: MockEMailAccount) -> None:
        self._conn(mock_account, b"1")
        action = Action(lambda conn, ids, *, delimiter: None, "custom")
        report = (mock_account / "INBOX").run(Filter(Anything(), action), dry_run=False)

        assert report["filters"][0]["spec"] is None
        assert report["filters"][0]["action"] == "custom"
        assert report["filters"][0]["executed"] is True

    def test_envelope_fetch_failure_does_not_block_action(
        self, mock_account: MockEMailAccount
    ) -> None:
        conn = self._conn(mock_account, b"1")
        conn.fetch.side_effect = IMAPClientError("FETCH failed")
        report = (mock_account / "INBOX").run(Filter(Anything(), MoveTo(Path("D"))), dry_run=False)

        conn.move.assert_called_once_with([1], "D")
        assert report["filters"][0]["matched"] == [
            {"uid": 1, "from": None, "subject": None, "date": None}
        ]
        assert report["filters"][0]["executed"] is True

    def test_run_into_keeps_finished_filters_on_failure(
        self, mock_account: MockEMailAccount
    ) -> None:
        conn = self._conn(mock_account, b"1")
        conn.move.side_effect = OSError("connection lost")
        folder = mock_account / "INBOX"
        report = folder.new_report(dry_run=False)
        first = Filter(Anything(), MarkAsRead())

        with pytest.raises(OSError, match="connection lost"):
            folder.run_into(report, [first, Filter(Anything(), MoveTo(Path("D")))], dry_run=False)

        assert [r["spec"] for r in report["filters"]] == [first.to_spec()]
        conn.logout.assert_called_once()

    def test_empty_filters_report(self, mock_account: MockEMailAccount) -> None:
        report = (mock_account / "INBOX").run([], dry_run=False)

        mock_account.connect.assert_not_called()
        assert report["filters"] == []


class TestSummarizeMail:
    """Test summarize_mail's header decoding."""

    def test_decodes_encoded_words_and_keeps_missing(self) -> None:
        conn = MagicMock()
        conn.fetch.return_value = {
            7: {
                b"ENVELOPE": _envelope(
                    b"=?utf-8?q?Caff=C3=A8?=", b"=?utf-8?q?Jos=C3=A9?=", b"jose", b"example.com"
                )
            }
        }
        summaries = summarize_mail(conn, [7, 8])

        conn.fetch.assert_called_once_with([7, 8], ["ENVELOPE"])
        assert summaries == [
            {
                "uid": 7,
                "from": "José <jose@example.com>",
                "subject": "Caffè",
                "date": _ENVELOPE_DATE_UTC,
            },
            {"uid": 8, "from": None, "subject": None, "date": None},
        ]


class TestAccountConnection:
    """Test Account.connect options and list_folders."""

    def test_connect_passes_ssl_context(self) -> None:
        context = ssl.create_default_context()
        account = Account("a", "imap.example.com", 993, "u", "p", ssl_context=context)
        with patch("imap_thingy.accounts.account.IMAPClient") as client:
            conn = account.connect()
        client.assert_called_once_with(
            "imap.example.com", 993, ssl=True, ssl_context=context, timeout=None
        )
        conn.login.assert_called_once_with("u", "p")

    def test_connect_defaults_to_no_ssl_context_or_timeout(self) -> None:
        account = Account("a", "imap.example.com", 993, "u", "p")
        with patch("imap_thingy.accounts.account.IMAPClient") as client:
            account.connect()
        assert client.call_args.kwargs["ssl_context"] is None
        assert client.call_args.kwargs["timeout"] is None

    def test_connect_passes_timeout(self) -> None:
        account = Account("a", "imap.example.com", 993, "u", "p", timeout=7.5)
        with patch("imap_thingy.accounts.account.IMAPClient") as client:
            account.connect()
        assert client.call_args.kwargs["timeout"] == 7.5

    def test_list_folders_skips_unselectable(self, mock_account: MockEMailAccount) -> None:
        conn = mock_account.connect.return_value
        conn.list_folders.return_value = [
            ((b"\\HasNoChildren",), b".", "INBOX"),
            ((b"\\HasChildren", b"\\Noselect"), b".", "Lists"),
            ((b"\\HasNoChildren",), b".", "Lists.Nerds"),
            ((b"\\NonExistent",), b".", "Gone"),
            ((), None, "Flat.Name"),
        ]
        paths = mock_account.list_folders()

        assert [p.segments for p in paths] == [["INBOX"], ["Lists", "Nerds"], ["Flat.Name"]]
        assert [(mock_account / p).imap_name() for p in paths] == [
            "INBOX",
            "Lists.Nerds",
            "Flat.Name",
        ]
        conn.logout.assert_called_once()

    def test_list_folders_splits_on_account_delimiter(
        self, mock_account: MockEMailAccount, caplog: pytest.LogCaptureFixture
    ) -> None:
        conn = mock_account.connect.return_value
        conn.list_folders.return_value = [((), b"/", "Lists/Nerds.2026")]
        paths = mock_account.list_folders()

        assert [p.segments for p in paths] == [["Lists/Nerds", "2026"]]
        assert "differs from the account's '.'" in caplog.text

    def test_list_folders_logs_out_when_listing_fails(self, mock_account: MockEMailAccount) -> None:
        conn = mock_account.connect.return_value
        conn.list_folders.side_effect = IMAPClientError("LIST failed")
        with pytest.raises(IMAPClientError):
            mock_account.list_folders()
        conn.logout.assert_called_once()

    def test_list_folders_ignores_logout_failure(self, mock_account: MockEMailAccount) -> None:
        conn = mock_account.connect.return_value
        conn.list_folders.return_value = [((), b".", "INBOX")]
        conn.logout.side_effect = OSError("gone")
        assert [p.segments for p in mock_account.list_folders()] == [["INBOX"]]

    def test_gmail_account_passes_ssl_context(self) -> None:
        context = ssl.create_default_context()
        assert GMailAccount("g", "u", "p", ssl_context=context).ssl_context is context

    def test_gmail_account_passes_timeout(self) -> None:
        assert GMailAccount("g", "u", "p", timeout=3).timeout == 3

    def test_accounts_from_json_reads_timeout(self, tmp_path: pathlib.Path) -> None:
        config = tmp_path / "accounts.json"
        config.write_text(
            json.dumps(
                [
                    {"name": "g", "type": "gmail", "username": "u", "password": "p", "timeout": 5},
                    {"name": "c", "host": "h", "port": 993, "username": "u", "password": "p"},
                ]
            )
        )
        accounts = accounts_from_json(str(config))
        assert accounts["g"].timeout == 5
        assert accounts["c"].timeout is None
