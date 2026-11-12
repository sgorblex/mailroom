"""Folder (path + account) and its run, on a stand-in for `MailConnections`: the search, fetch and
envelope calls are patched where the engine makes them. Runs on real mail: `test_engine_mail`."""

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from imapclient.exceptions import IMAPClientError
from imapclient.response_types import Address, Envelope

from mailroom_core.filters.account import Account, Folder
from mailroom_core.filters.actions.action import Action
from mailroom_core.filters.actions.flags import MarkAsRead
from mailroom_core.filters.actions.move import MoveTo
from mailroom_core.filters.criteria.address import FromIs
from mailroom_core.filters.criteria.criterion import Anything
from mailroom_core.filters.criteria.date import OlderThanDays
from mailroom_core.filters.criteria.flags import IsRead
from mailroom_core.filters.criteria.subject import SubjectIs, SubjectMatches
from mailroom_core.filters.filter import Filter
from mailroom_core.filters.path import Path
from mailroom_core.filters.pattern import MatchTimeoutError
from mailroom_core.filters.report import summaries
from mailroom_core.mail import FetchedMessage, MailError
from mailroom_core.mail import Folder as ListedFolder

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from mailroom_core.filters.selected import Selected

ENGINE = "mailroom_core.filters.account"


class TestPath:
    """Test Path (segments only)."""

    def test_path_creation(self) -> None:
        assert Path("Cool").segments == ["Cool"]

    def test_path_truediv(self) -> None:
        assert (Path("Cool") / "Sub").segments == ["Cool", "Sub"]

    def test_path_empty_is_root(self) -> None:
        for path in (Path([]), Path("")):
            assert path.segments == []
            assert path.as_string() == ""

    def test_path_inbox_is_inbox(self) -> None:
        assert Path("INBOX").as_string() == "INBOX"


class TestAccountFolder:
    """Test Folder (account + path) and the account's folders."""

    def test_folder_and_truediv(self, account: Account) -> None:
        folder = account / "Cool"
        assert isinstance(folder, Folder)
        assert folder.account is account
        assert folder.imap_name() == "Cool"
        assert account.inbox.path.segments == ["INBOX"]

    @pytest.mark.parametrize(("delimiter", "name"), [("/", "Cool/Sub"), (".", "Cool.Sub")])
    def test_imap_name_uses_the_servers_delimiter(
        self, account: Account, connections: Any, delimiter: str, name: str
    ) -> None:
        connections.hierarchy = delimiter
        assert (account / "Cool" / "Sub").imap_name() == name

    def test_str_names_the_account_and_path_without_a_connection(self, account: Account) -> None:
        assert str(account / "Lists" / "Nerds") == "test_account.Lists/Nerds"

    def test_folders_split_on_each_folders_delimiter(
        self, account: Account, connections: Any
    ) -> None:
        connections.listed = [
            ListedFolder("INBOX", "/", "inbox", ()),
            ListedFolder("Lists", "/", None, ("\\Noselect",)),
            ListedFolder("Lists/Nerds.2026", "/", None, ()),
            ListedFolder("Flat.Name", None, None, ()),
        ]
        assert [p.segments for p in account.folders()] == [
            ["INBOX"],
            ["Lists", "Nerds.2026"],
            ["Flat.Name"],
        ]


def fetched(uid: int, raw: str, *flags: str) -> FetchedMessage:
    return FetchedMessage(uid, raw.encode(), frozenset(flags))


def subjects(by_uid: dict[int, str]) -> dict[int, FetchedMessage]:
    return {
        uid: fetched(uid, f"From: a@b.example\r\nSubject: {s}\r\n\r\n") for uid, s in by_uid.items()
    }


class Calls:
    """The engine's patched operations: `search` answers in turn, `fetch` records what it was
    asked and answers from `sources`."""

    def __init__(self, searches: Sequence[list[int]], sources: dict[int, FetchedMessage]) -> None:
        self.searches = list(searches)
        self.searched: list[list[object]] = []
        self.sources = sources
        self.fetched: list[list[int]] = []
        self.envelopes: dict[int, Envelope | None] = {}

    def search(self, _context: object, criteria: list[object]) -> list[int]:
        self.searched.append(criteria)
        return self.searches.pop(0) if self.searches else []

    def fetch(self, _context: object, uids: list[int]) -> dict[int, FetchedMessage]:
        self.fetched.append(list(uids))
        return {uid: self.sources[uid] for uid in uids if uid in self.sources}

    def envelope(self, _context: object, uids: list[int]) -> dict[int, Envelope | None]:
        return {uid: self.envelopes.get(uid) for uid in uids}


@pytest.fixture
def calls() -> Iterator[Calls]:
    found = Calls([], {})
    with (
        patch(f"{ENGINE}.search_uids", side_effect=found.search),
        patch(f"{ENGINE}.fetch_sources", side_effect=found.fetch),
        patch(f"{ENGINE}.fetch_envelopes", side_effect=found.envelope),
    ):
        yield found


def recording(name: str = "record") -> tuple[Action, list[list[int]]]:
    done: list[list[int]] = []

    def func(_selected: Selected, uids: Sequence[int]) -> None:
        done.append(list(uids))

    return Action(func, name), done


class TestFolderRun:
    """Test folder.run(filter) and folder.run(filters)."""

    def test_one_session_for_all_filters(
        self, account: Account, connections: Any, calls: Calls
    ) -> None:
        calls.searches = [[1], [2]]
        action, done = recording()
        (account / "INBOX").run([Filter(Anything(), action), Filter(Anything(), action)])
        assert connections.sessions == [("test_account", "INBOX", False)]
        assert done == [[1], [2]]

    def test_a_dry_run_is_read_only_and_acts_on_nothing(
        self, account: Account, connections: Any, calls: Calls
    ) -> None:
        calls.searches = [[1]]
        action, done = recording()
        report = (account / "Lists" / "Nerds").run(Filter(Anything(), action), dry_run=True)
        assert connections.sessions == [("test_account", "Lists.Nerds", True)]
        assert done == []
        assert report["dry_run"] is True
        assert [m["uid"] for m in report["filters"][0]["matched"]] == [1]
        assert report["filters"][0]["executed"] is False

    def test_searches_send_the_criteria(self, account: Account, calls: Calls) -> None:
        (account / "INBOX").run(
            Filter(FromIs("a@b.example") & ~FromIs("c@d.example"), MarkAsRead())
        )
        assert calls.searched == [
            ["UNDELETED", "FROM", "a@b.example", "NOT", "FROM", "c@d.example"]
        ]

    def test_every_search_skips_deleted_messages(self, account: Account, calls: Calls) -> None:
        """An original a move copied but could not expunge is marked \\Deleted: never again."""
        filters = [
            Filter(Anything(), MarkAsRead()),
            Filter(FromIs("a@b.example") | ~FromIs("c@d.example"), MarkAsRead()),
        ]
        (account / "INBOX").run(filters)
        (account / "INBOX").run(filters, dry_run=True)
        assert len(calls.searched) == 4
        assert all(criteria[0] == "UNDELETED" for criteria in calls.searched)

    def test_newest_bounds_every_search_when_the_folder_holds_more(
        self, account: Account, connections: Any, calls: Calls
    ) -> None:
        """The window is the newest messages' UIDs, looked up once: sequence numbers shift when
        a filter expunges, and later mail stays out."""
        connections.exists = 1200
        calls.searches = [list(range(1701, 2201))]
        report = (account / "INBOX").run(
            [Filter(Anything(), MarkAsRead()), Filter(FromIs("a@b.example"), MarkAsRead())],
            dry_run=True,
            newest=500,
        )
        assert calls.searched == [
            ["701:1200"],
            ["UID", "1701:2200", "UNDELETED", "ALL"],
            ["UID", "1701:2200", "UNDELETED", "FROM", "a@b.example"],
        ]
        assert report["limited_to_newest"] == 500

    @pytest.mark.parametrize("newest", [0, -5])
    def test_newest_is_at_least_one(self, account: Account, newest: int) -> None:
        with pytest.raises(ValueError, match="newest must be at least 1"):
            (account / "INBOX").run(Filter(Anything(), MarkAsRead()), newest=newest)

    def test_newest_leaves_a_smaller_folder_whole(
        self, account: Account, connections: Any, calls: Calls
    ) -> None:
        connections.exists = 500
        report = (account / "INBOX").run(Filter(Anything(), MarkAsRead()), newest=500)
        assert calls.searched == [["UNDELETED", "ALL"]]
        assert "limited_to_newest" not in report

    def test_older_than_days_cutoff_is_taken_at_each_run(
        self, account: Account, calls: Calls
    ) -> None:
        f = Filter(OlderThanDays(30), MarkAsRead())
        with patch("mailroom_core.filters.criteria.date._today") as today:
            today.return_value = date(2026, 3, 31)
            (account / "INBOX").run(f)
            today.return_value = date(2026, 4, 30)
            (account / "INBOX").run(f)
        assert calls.searched == [
            ["UNDELETED", "SENTBEFORE", date(2026, 3, 1)],
            ["UNDELETED", "SENTBEFORE", date(2026, 3, 31)],
        ]

    def test_matches_exactly_on_the_fetched_subject(self, account: Account, calls: Calls) -> None:
        """SubjectIs searches SUBJECT (a substring) and checks the parsed subject."""
        subject = "Important - Claims settlement Information"
        calls.searches = [[1, 2]]
        calls.sources = subjects({1: subject, 2: f"Re: {subject}"})
        action, done = recording()
        (account / "INBOX").run(Filter(FromIs("a@b.example") & SubjectIs(subject), action))
        assert calls.fetched == [[1, 2]]
        assert done == [[1]]

    def test_an_efficient_criterion_fetches_nothing(self, account: Account, calls: Calls) -> None:
        calls.searches = [[1, 2]]
        action, done = recording()
        (account / "INBOX").run(Filter(FromIs("a@b.example"), action))
        assert calls.fetched == []
        assert done == [[1, 2]]

    def test_messages_are_fetched_once_per_run(self, account: Account, calls: Calls) -> None:
        calls.searches = [[1, 2, 3], [2, 3, 4]]
        calls.sources = subjects({1: "one", 2: "other", 3: "other", 4: "four"})
        first, _ = recording()
        second, _ = recording()
        (account / "INBOX").run(
            [Filter(SubjectMatches("one"), first), Filter(SubjectMatches("four"), second)]
        )
        assert calls.fetched == [[1, 2, 3], [4]]

    def test_messages_acted_on_are_fetched_again(self, account: Account, calls: Calls) -> None:
        calls.searches = [[1, 2, 3], [2, 3], [2, 3, 4]]
        calls.sources = subjects({1: "one", 2: "other", 3: "other", 4: "four"})
        action, done = recording()
        (account / "INBOX").run(
            [
                Filter(SubjectMatches("one"), action),
                Filter(Anything(), action),
                Filter(SubjectMatches("other"), action),
            ]
        )
        assert calls.fetched == [[1, 2, 3], [2, 3, 4]]
        assert done == [[1], [2, 3], [2, 3]]

    def test_a_message_that_does_not_come_back_is_skipped(
        self, account: Account, calls: Calls
    ) -> None:
        calls.searches = [[1, 2, 3]]
        calls.sources = subjects({1: "match", 3: "match"})
        action, done = recording()
        (account / "INBOX").run(Filter(SubjectMatches("match"), action))
        assert done == [[1, 3]]

    def test_flags_reach_the_criteria_as_bytes(self, account: Account, calls: Calls) -> None:
        calls.searches = [[1, 2]]
        calls.sources = {
            1: fetched(1, "Subject: a\r\n\r\n", "\\Seen"),
            2: fetched(2, "Subject: a\r\n\r\n"),
        }
        action, done = recording()
        (account / "INBOX").run(Filter(IsRead() & SubjectMatches("a"), action))
        assert done == [[1]]


_ENVELOPE_DATE = datetime(2026, 5, 4, 12, 30, tzinfo=UTC)


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

    def test_real_run_reports_matches_and_execution(self, account: Account, calls: Calls) -> None:
        calls.searches = [[1, 2]]
        calls.envelopes = {
            1: _envelope(b"Hello", b"Ann", b"ann", b"example.com"),
            2: _envelope(b"Again", None, b"bob", b"example.com"),
        }
        f = Filter(FromIs("ann@example.com"), MoveTo(Path("Dest")))
        with patch("mailroom_core.filters.actions.move._move") as move:
            report = (account / "INBOX").run(f, dry_run=False)
        assert move.call_args.args[1:] == ([1, 2], "Dest")
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
                            "date": _ENVELOPE_DATE.isoformat(),
                        },
                        {
                            "uid": 2,
                            "from": "bob@example.com",
                            "subject": "Again",
                            "date": _ENVELOPE_DATE.isoformat(),
                        },
                    ],
                    "executed": True,
                }
            ],
            "error": None,
        }
        assert json.loads(json.dumps(report)) == report

    def test_no_match_fetches_no_envelope(self, account: Account, calls: Calls) -> None:
        with patch(f"{ENGINE}.fetch_envelopes") as envelopes:
            report = (account / "INBOX").run(Filter(Anything(), MarkAsRead()))
        envelopes.assert_not_called()
        assert report["filters"][0]["matched"] == []
        assert report["filters"][0]["executed"] is False

    def test_custom_filter_has_no_spec(self, account: Account, calls: Calls) -> None:
        calls.searches = [[1]]
        action, _ = recording("custom")
        report = (account / "INBOX").run(Filter(Anything(), action))
        assert report["filters"][0]["spec"] is None
        assert report["filters"][0]["action"] == "custom"
        assert report["filters"][0]["executed"] is True

    def test_envelope_fetch_failure_does_not_block_action(
        self, account: Account, calls: Calls
    ) -> None:
        calls.searches = [[1]]
        action, done = recording()
        with patch(f"{ENGINE}.fetch_envelopes", side_effect=IMAPClientError("FETCH failed")):
            report = (account / "INBOX").run(Filter(Anything(), action))
        assert done == [[1]]
        assert report["filters"][0]["matched"] == [
            {"uid": 1, "from": None, "subject": None, "date": None}
        ]

    def test_run_into_keeps_finished_filters_on_failure(
        self, account: Account, calls: Calls
    ) -> None:
        calls.searches = [[1], [1]]
        folder = account / "INBOX"
        report = folder.new_report(dry_run=False)
        report["filters"].append(
            {"spec": None, "action": "earlier", "matched": [], "executed": True}
        )
        first = Filter(Anything(), recording()[0])

        def fail(_selected: Selected, _uids: Sequence[int]) -> None:
            raise MailError("server_error")

        with pytest.raises(MailError):
            folder.run_into(report, [first, Filter(Anything(), Action(fail))], dry_run=False)
        assert [r["action"] for r in report["filters"]] == ["earlier", "record"]

    def test_a_non_mail_error_comes_out_as_itself(self, account: Account, calls: Calls) -> None:
        calls.searches = [[1]]

        def slow(_selected: Selected, _uids: Sequence[int]) -> None:
            raise MatchTimeoutError("timed out")

        with pytest.raises(MatchTimeoutError):
            (account / "INBOX").run(Filter(Anything(), Action(slow)))

    def test_empty_filters_open_nothing(self, account: Account, connections: Any) -> None:
        connections.delimiter = MagicMock(side_effect=AssertionError("asked"))
        report = (account / "INBOX").run([])
        assert connections.sessions == []
        assert report["filters"] == []


def test_summaries_decode_encoded_words_and_keep_missing() -> None:
    envelope = _envelope(
        b"=?utf-8?q?Caff=C3=A8?=", b"=?utf-8?q?Jos=C3=A9?=", b"jose", b"example.com"
    )
    assert summaries([7, 8], {7: envelope, 8: None}) == [
        {
            "uid": 7,
            "from": "José <jose@example.com>",
            "subject": "Caffè",
            "date": _ENVELOPE_DATE.isoformat(),
        },
        {"uid": 8, "from": None, "subject": None, "date": None},
    ]
