"""Folder (path + account) and its run, on a stand-in for `MailConnections`: the search, fetch and
envelope calls are patched where the engine makes them. Runs on real mail: `test_engine_mail`."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest
from imapclient.exceptions import IMAPClientError
from imapclient.response_types import Address, Envelope

from mailroom_core.config import TimeZoneName
from mailroom_core.filters.account import Account, Folder
from mailroom_core.filters.actions.action import Action
from mailroom_core.filters.actions.flags import MarkAsRead
from mailroom_core.filters.actions.move import MoveTo
from mailroom_core.filters.actions.transfer import AccountTarget, MoveToAccount
from mailroom_core.filters.criteria.address import FromContains, FromIs
from mailroom_core.filters.criteria.criterion import Anything
from mailroom_core.filters.criteria.date import OlderThanDays
from mailroom_core.filters.criteria.flags import IsRead
from mailroom_core.filters.criteria.subject import SubjectIs, SubjectMatches
from mailroom_core.filters.criteria.thread import InThread, ThreadMembers
from mailroom_core.filters.filter import Filter
from mailroom_core.filters.path import Path
from mailroom_core.filters.pattern import MatchTimeoutError
from mailroom_core.filters.report import summaries
from mailroom_core.filters.spec import load_action
from mailroom_core.mail import (
    MAX_REFS,
    AuditItem,
    FetchedMessage,
    GroupResult,
    MailError,
    MessageRef,
    OrganizeResult,
    parse_ref,
)
from mailroom_core.mail import Folder as ListedFolder

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from mailroom_core.filters.selected import Selected
    from mailroom_core.mail import MailConnections

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
            Filter(FromIs("a@b.example") & ~FromContains("c@d.example"), MarkAsRead())
        )
        assert calls.searched == [
            ["UNDELETED", "FROM", "a@b.example", "NOT", "FROM", "c@d.example"]
        ]

    def test_skip_leaves_the_hosts_uids_to_every_filter(
        self, account: Account, calls: Calls
    ) -> None:
        calls.searches = [[1, 2, 3], [2, 3, 4]]
        action, done = recording()
        asked: list[tuple[str, int]] = []

        def skip(path: str, uid_validity: int) -> set[int]:
            asked.append((path, uid_validity))
            return {2, 3}

        report = (account / "INBOX").run(
            [Filter(Anything(), action), Filter(Anything(), action)], skip=skip
        )
        assert asked == [("INBOX", 1)] * 2  # before each filter
        assert done == [[1], [4]]
        assert [[m["uid"] for m in f["matched"]] for f in report["filters"]] == [[1], [4]]

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

    def test_older_than_days_counts_from_the_runs_time_zone(
        self, account: Account, calls: Calls
    ) -> None:
        """At 23:30 UTC on 31 March it is already 1 April in Auckland."""

        class Clock(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                return datetime(2026, 3, 31, 23, 30, tzinfo=UTC).astimezone(tz)

        f = Filter(OlderThanDays(0), MarkAsRead())
        with patch("mailroom_core.filters.clock.datetime", Clock):
            (account / "INBOX").run(f, time_zone=TimeZoneName("Pacific/Auckland"))
            (account / "INBOX").run(f, time_zone=TimeZoneName("UTC"))
        assert calls.searched == [
            ["UNDELETED", "SENTBEFORE", date(2026, 4, 1)],
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
        (account / "INBOX").run(Filter(FromContains("b.example"), action))
        assert calls.fetched == []
        assert done == [[1, 2]]

    def test_matches_exactly_on_the_fetched_address(self, account: Account, calls: Calls) -> None:
        """FromIs searches FROM (a substring) and checks the parsed sender."""
        calls.searches = [[1, 2]]
        calls.sources = {
            uid: fetched(uid, f"From: {sender}\r\nSubject: s\r\n\r\n")
            for uid, sender in {1: "ana@x.org", 2: "banana@x.org"}.items()
        }
        action, done = recording()
        (account / "INBOX").run(Filter(FromIs("ana@x.org"), action))
        assert calls.searched == [["UNDELETED", "FROM", "ana@x.org"]]
        assert calls.fetched == [[1, 2]]
        assert done == [[1]]

    def test_a_negated_prefilter_fetches_only_its_candidates(
        self, account: Account, calls: Calls
    ) -> None:
        """Outside FromIs's FROM search the negation matches unfetched; inside, the predicate
        decides."""
        calls.searches = [[3], [1, 2]]
        calls.sources = {
            uid: fetched(uid, f"From: {sender}\r\nSubject: s\r\n\r\n")
            for uid, sender in {1: "ana@x.org", 2: "banana@x.org"}.items()
        }
        action, done = recording()
        (account / "INBOX").run(Filter(~FromIs("ana@x.org"), action))
        assert calls.searched == [
            ["UNDELETED", "NOT", "FROM", "ana@x.org"],
            ["UNDELETED", "FROM", "ana@x.org"],
        ]
        assert calls.fetched == [[1, 2]]
        assert done == [[2, 3]]

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

    def test_a_thread_is_matched_by_its_ids_and_gmails_thread(
        self, account: Account, calls: Calls
    ) -> None:
        calls.searches = [[1, 2, 3, 4]]
        calls.sources = {
            1: fetched(1, "Message-ID: <other@x.org>\r\nReferences: <root@x.org>\r\n\r\n"),
            2: fetched(2, "Message-ID: <new@x.org>\r\n\r\n"),
            3: replace(fetched(3, "Subject: s\r\n\r\n"), gmail_thread=77),
            4: fetched(4, "In-Reply-To: <stranger@x.org>\r\n\r\n"),
        }
        members = ThreadMembers(frozenset({"<root@x.org>"}), frozenset({77}))
        action, done = recording()
        (account / "INBOX").run(Filter(InThread("t", threads=lambda _: members), action))
        assert calls.searched == [["UNDELETED", "ALL"]]
        assert done == [[1, 3]]


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
        f = Filter(FromContains("example.com"), MoveTo(Path("Dest")))
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


TRANSFER = "mailroom_core.filters.actions.transfer.move_to_account"


class Target:
    """An `AccountResolver` that records the accounts it is asked for and hands out a stand-in
    for the target's connections, whose delimiter is ``/``."""

    def __init__(self, refusal: MailError | None = None) -> None:
        self.asked: list[int] = []
        self.connections = MagicMock()
        self.connections.delimiter.return_value = "/"
        self.refusal = refusal

    def __call__(self, account: int) -> AccountTarget:
        self.asked.append(account)
        if self.refusal is not None:
            raise self.refusal
        return AccountTarget(self.connections, 1000)


def moved(refs: Sequence[str], *, error: MailError | None = None) -> OrganizeResult:
    """What `move_to_account` answers for `refs` of account 1's INBOX: each moved to Trash, or
    each failed with `error`."""
    uids = [parse_ref(ref).uid for ref in refs]
    items = tuple(
        AuditItem(
            account=1,
            folder="INBOX",
            uid=uid,
            destination="Lists/Nerds",
            destination_account=2,
            removed=None if error else "trashed",
            result=error.code if error else "ok",
        )
        for uid in uids
    ) + tuple(
        AuditItem(account=2, folder="Lists/Nerds", uid=100 + uid, source_account=1, result="ok")
        for uid in ([] if error else uids)
    )
    group = GroupResult(
        1,
        "INBOX",
        count=0 if error else len(uids),
        new_refs=None if error else tuple(f"copy-{uid}" for uid in uids),
        error=error,
        destination="Lists/Nerds",
        destination_account=2,
        trash="Trash",
    )
    return OrganizeResult((group,), items)


class TestMoveToAccount:
    """A move to another account: recorded in the session, made after it, reported per message."""

    @pytest.fixture
    def mine(self, connections: Any) -> Account:
        return Account(cast("MailConnections", connections), 1)

    def test_recorded_in_the_run_and_moved_once_the_session_closed(
        self, mine: Account, connections: Any, calls: Calls
    ) -> None:
        calls.searches = [[1, 2], [1, 2, 3]]
        later, done = recording()
        target = Target()
        action = load_action(
            {"type": "move_to_account", "args": {"account": 2, "folder": ["Lists", "Nerds"]}},
            accounts=target,
        )
        sessions_at_move: list[int] = []

        def move(conns: object, refs: Sequence[str], *args: object, **kwargs: object) -> Any:
            sessions_at_move.append(len(connections.sessions))
            assert conns is target.connections
            assert args == (2, "Lists/Nerds")
            assert kwargs == {
                "removal": "trash",
                "max_bytes": 1000,
                "refuse_each": True,
                "copied": None,
            }
            assert [parse_ref(ref) for ref in refs] == [
                MessageRef(1, "INBOX", 1, 1),
                MessageRef(1, "INBOX", 1, 2),
            ]
            return moved(refs)

        with patch(TRANSFER, side_effect=move):
            report = (mine / "INBOX").run([Filter(Anything(), action), Filter(Anything(), later)])
        # The later filter no longer sees what the move took, as after a move.
        assert done == [[3]]
        assert sessions_at_move == [1]
        assert target.asked == [2]
        first = report["filters"][0]
        assert first["executed"] is True
        assert first["action"] == "MoveToAccount(account=2, folder=['Lists', 'Nerds'])"
        assert first["transfers"] == [
            {
                "account": 2,
                "folder": ["Lists", "Nerds"],
                "messages": [
                    {"uid": 1, "result": "ok", "removed": "trashed"},
                    {"uid": 2, "result": "ok", "removed": "trashed"},
                ],
                "new_refs": ["copy-1", "copy-2"],
                "error": None,
            }
        ]
        assert "transfers" not in report["filters"][1]
        assert json.loads(json.dumps(report)) == report

    def test_a_message_refused_on_its_own_is_reported_with_why(
        self, mine: Account, calls: Calls
    ) -> None:
        calls.searches = [[1, 2]]
        target = Target()
        action = MoveToAccount(2, Path("X"), accounts=target)
        refusal = MailError("too_large", "It is over the 1 KB a moved message may have.")

        def move(_conns: object, refs: Sequence[str], *_args: object, **_kwargs: object) -> Any:
            ok = moved(refs[:1])
            [group] = ok.groups
            refused = AuditItem(
                account=1,
                folder="INBOX",
                uid=parse_ref(refs[1]).uid,
                destination="X",
                destination_account=2,
                result="too_large",
            )
            return OrganizeResult(
                (replace(group, refused=((parse_ref(refs[1]).uid, refusal),)),),
                (*ok.items, refused),
            )

        with patch(TRANSFER, side_effect=move):
            report = (mine / "INBOX").run(Filter(Anything(), action))
        [entry] = report["filters"]
        assert entry["executed"] is False
        [transfer] = entry["transfers"]
        assert transfer["messages"] == [
            {"uid": 1, "result": "ok", "removed": "trashed"},
            {
                "uid": 2,
                "result": "too_large",
                "reason": "It is over the 1 KB a moved message may have.",
            },
        ]
        assert transfer["new_refs"] == ["copy-1"]
        assert transfer["error"] == f"too_large: {refusal.message}"

    @pytest.mark.parametrize(
        ("group", "changed"),
        [
            ({}, {(1, "INBOX"), (1, "Trash"), (2, "Lists/Nerds")}),
            # It may have copied some, or removed some, before it failed.
            (
                {"count": 0, "new_refs": None, "error": MailError("server_error")},
                {(1, "INBOX"), (1, "Trash"), (2, "Lists/Nerds")},
            ),
            # Every message refused on its own, or the group before it began.
            ({"count": 0, "new_refs": None}, set()),
            (
                {
                    "count": 0,
                    "new_refs": None,
                    "error": MailError("stale_reference"),
                    "destination": None,
                    "destination_account": None,
                },
                set(),
            ),
        ],
    )
    def test_the_folders_it_changed_are_noted_in_both_accounts(
        self,
        mine: Account,
        calls: Calls,
        group: dict[str, Any],
        changed: set[tuple[int, str]],
    ) -> None:
        calls.searches = [[1]]
        inbox = mine / "INBOX"
        noted: set[tuple[int, str]] = set()

        def move(_conns: object, refs: Sequence[str], *_args: object, **_kwargs: object) -> Any:
            done = moved(refs)
            return replace(done, groups=(replace(done.groups[0], **group),))

        with patch(TRANSFER, side_effect=move):
            inbox.run_into(
                inbox.new_report(dry_run=False),
                Filter(Anything(), MoveToAccount(2, Path("X"), accounts=Target())),
                dry_run=False,
                changed=noted,
            )
        assert noted == changed

    def test_a_dry_run_changes_no_folder(self, mine: Account, calls: Calls) -> None:
        calls.searches = [[1]]
        inbox = mine / "INBOX"
        noted: set[tuple[int, str]] = set()
        star = Filter(Anything(), load_action({"type": "star", "args": {}}))
        inbox.run_into(inbox.new_report(dry_run=True), star, dry_run=True, changed=noted)
        assert noted == set()

    def test_without_a_resolver_every_message_fails_unsupported(
        self, mine: Account, calls: Calls
    ) -> None:
        calls.searches = [[4, 5]]
        with patch(TRANSFER) as move:
            report = (mine / "INBOX").run(Filter(Anything(), MoveToAccount(2, Path("X"))))
        move.assert_not_called()
        [entry] = report["filters"]
        assert entry["executed"] is False
        [transfer] = entry["transfers"]
        assert transfer["messages"] == [
            {"uid": 4, "result": "unsupported"},
            {"uid": 5, "result": "unsupported"},
        ]
        assert transfer["error"] is not None
        assert transfer["error"].startswith("unsupported: ")

    def test_a_refused_account_fails_each_message_with_its_code(
        self, mine: Account, calls: Calls
    ) -> None:
        calls.searches = [[4]]
        refusal = MailError("invalid_argument", "Unknown account 2.")
        target = Target(refusal)
        action = MoveToAccount(2, Path("X"), accounts=target)
        with patch(TRANSFER) as move:
            report = (mine / "INBOX").run(Filter(Anything(), action))
        move.assert_not_called()
        [transfer] = report["filters"][0]["transfers"]
        assert transfer["messages"] == [{"uid": 4, "result": "invalid_argument"}]
        assert transfer["error"] == f"invalid_argument: {refusal.message}"

    def test_a_resolver_that_fails_otherwise_fails_each_message_and_the_next_transfer_runs(
        self, mine: Account, calls: Calls
    ) -> None:
        calls.searches = [[4], [5]]

        def broken(account: int) -> AccountTarget:
            raise KeyError(account)

        first = MoveToAccount(2, Path("X"), accounts=broken)
        second = MoveToAccount(3, Path("X"), accounts=Target())
        with patch(TRANSFER, side_effect=lambda _c, refs, *_a, **_k: moved(refs)):
            report = (mine / "INBOX").run([Filter(Anything(), first), Filter(Anything(), second)])
        [failed] = report["filters"][0]["transfers"]
        assert failed["messages"] == [{"uid": 4, "result": "server_error"}]
        assert failed["error"] is not None
        assert failed["error"].startswith("server_error: ")
        assert "KeyError" not in failed["error"]
        [done] = report["filters"][1]["transfers"]
        assert [m["result"] for m in done["messages"]] == ["ok"]

    def test_a_folder_that_failed_is_reported_per_message(
        self, mine: Account, calls: Calls
    ) -> None:
        calls.searches = [[7]]
        failure = MailError("too_large", "1 of these are over the cap.")
        action = MoveToAccount(2, Path("X"), accounts=Target())
        with patch(TRANSFER, side_effect=lambda _c, refs, *_a, **_k: moved(refs, error=failure)):
            report = (mine / "INBOX").run(Filter(Anything(), action))
        [entry] = report["filters"]
        assert entry["executed"] is False
        [transfer] = entry["transfers"]
        assert transfer["messages"] == [{"uid": 7, "result": "too_large"}]
        assert transfer["new_refs"] == []
        assert transfer["error"] == f"too_large: {failure.message}"

    def test_moves_in_batches_of_max_refs(self, mine: Account, calls: Calls) -> None:
        uids = list(range(1, MAX_REFS + 3))
        calls.searches = [uids]
        action = MoveToAccount(2, Path("X"), accounts=Target())
        with patch(TRANSFER, side_effect=lambda _c, refs, *_a, **_k: moved(refs)) as move:
            report = (mine / "INBOX").run(Filter(Anything(), action))
        assert [len(c.args[1]) for c in move.call_args_list] == [MAX_REFS, 2]
        [transfer] = report["filters"][0]["transfers"]
        assert [m["uid"] for m in transfer["messages"]] == uids

    def test_a_refusal_in_a_later_batch_leaves_the_earlier_one_moved(
        self, mine: Account, calls: Calls
    ) -> None:
        calls.searches = [list(range(1, MAX_REFS + 2))]
        answers = iter([None, MailError("unreachable")])

        def move(_c: object, refs: Sequence[str], *_a: object, **_k: object) -> OrganizeResult:
            refusal = next(answers)
            if refusal is not None:
                raise refusal
            return moved(refs)

        action = MoveToAccount(2, Path("X"), accounts=Target())
        with patch(TRANSFER, side_effect=move):
            report = (mine / "INBOX").run(Filter(Anything(), action))
        [transfer] = report["filters"][0]["transfers"]
        results = [m["result"] for m in transfer["messages"]]
        assert results == ["ok"] * MAX_REFS + ["unreachable"]
        assert transfer["error"] is not None
        assert transfer["error"].startswith("unreachable: ")

    def test_a_dry_run_moves_nothing(self, mine: Account, calls: Calls) -> None:
        calls.searches = [[1]]
        action = MoveToAccount(2, Path("X"), accounts=Target())
        with patch(TRANSFER) as move:
            report = (mine / "INBOX").run(Filter(Anything(), action), dry_run=True)
        move.assert_not_called()
        assert "transfers" not in report["filters"][0]

    def test_a_run_that_failed_later_still_moves_what_it_took(
        self, mine: Account, calls: Calls
    ) -> None:
        calls.searches = [[1], [2]]

        def fail(_selected: Selected, _uids: Sequence[int]) -> None:
            raise MailError("server_error")

        action = MoveToAccount(2, Path("X"), accounts=Target())
        folder = mine / "INBOX"
        report = folder.new_report(dry_run=False)
        with (
            patch(TRANSFER, side_effect=lambda _c, refs, *_a, **_k: moved(refs)) as move,
            pytest.raises(MailError),
        ):
            folder.run_into(
                report,
                [Filter(Anything(), action), Filter(Anything(), Action(fail))],
                dry_run=False,
            )
        move.assert_called_once()
        assert report["filters"][0]["transfers"][0]["messages"][0]["result"] == "ok"

    def test_copied_is_handed_to_each_batchs_move(self, mine: Account, calls: Calls) -> None:
        calls.searches = [list(range(1, MAX_REFS + 2))]
        heard: list[list[str]] = []

        def move(_c: object, refs: Sequence[str], *_a: object, **kwargs: Any) -> OrganizeResult:
            result = moved(refs)
            kwargs["copied"](result.groups[0].new_refs)
            return result

        def resolve(_account: int) -> AccountTarget:
            return AccountTarget(MagicMock(), 1000, copied=lambda refs: heard.append(list(refs)))

        action = MoveToAccount(2, Path("X"), accounts=resolve)
        with patch(TRANSFER, side_effect=move):
            (mine / "INBOX").run(Filter(Anything(), action))
        assert [len(refs) for refs in heard] == [MAX_REFS, 1]
        assert heard[1] == [f"copy-{MAX_REFS + 1}"]

    def test_a_refusal_from_copied_stops_the_batches_left(
        self, mine: Account, calls: Calls
    ) -> None:
        calls.searches = [list(range(1, MAX_REFS + 2))]
        refusal = MailError("unsupported", "Not recorded.")

        def refuse(_refs: Sequence[str]) -> None:
            raise refusal

        def move(_c: object, refs: Sequence[str], *_a: object, **kwargs: Any) -> OrganizeResult:
            # As mailroom-core's: a refusal from `copied` leaves the originals, the group failed.
            try:
                kwargs["copied"]([f"copy-{parse_ref(ref).uid}" for ref in refs])
            except MailError as error:
                return moved(refs, error=error)
            return moved(refs)

        action = MoveToAccount(
            2, Path("X"), accounts=lambda _a: AccountTarget(MagicMock(), 1000, copied=refuse)
        )
        with patch(TRANSFER, side_effect=move) as move_mock:
            report = (mine / "INBOX").run(Filter(Anything(), action))
        move_mock.assert_called_once()
        [transfer] = report["filters"][0]["transfers"]
        results = [m["result"] for m in transfer["messages"]]
        assert results == ["unsupported"] * (MAX_REFS + 1)
        assert transfer["error"] == f"unsupported: {refusal.message}"

    def test_a_record_made_meanwhile_is_skipped_by_the_filters_after_it(
        self, mine: Account, calls: Calls
    ) -> None:
        calls.searches = [[1, 2], [1, 2]]
        recorded: set[int] = set()
        first_done: list[list[int]] = []
        second, second_done = recording()

        def record_then(_selected: Selected, uids: Sequence[int]) -> None:
            first_done.append(list(uids))
            recorded.add(2)  # another run's copy, recorded while this one runs

        (mine / "INBOX").run(
            [Filter(Anything(), Action(record_then)), Filter(Anything(), second)],
            skip=lambda _path, _validity: set(recorded),
        )
        assert first_done == [[1, 2]]
        assert second_done == [[1]]
