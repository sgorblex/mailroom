"""Tests for the rule runner: run_rules, starred_only and monitor."""

from __future__ import annotations

import signal
import threading
import time
from collections.abc import Callable, Iterator
from types import FrameType
from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import call, patch

import pytest

from mailroom_core.filters.account import Folder
from mailroom_core.filters.actions.flags import MarkAsRead, Unstar
from mailroom_core.filters.actions.move import MoveTo
from mailroom_core.filters.criteria.address import FromIs
from mailroom_core.filters.criteria.flags import IsStarred
from mailroom_core.filters.filter import Filter
from mailroom_core.filters.path import Path
from mailroom_core.filters.runner import Rules, RunRulesError, monitor, run_rules, starred_only
from mailroom_core.mail import IdleChange, MailError

if TYPE_CHECKING:
    from mailroom_core.filters.account import Account
    from mailroom_core.filters.report import FolderReport


def _rules(account: Account) -> Rules:
    return [
        (account / "INBOX", [Filter(FromIs("a@example.com"), MarkAsRead())]),
        (account / "Lists", [Filter(FromIs("b@example.com"), MoveTo(Path("Archive")))]),
        (account / "Spam", [Filter(FromIs("c@example.com"), MarkAsRead())]),
    ]


class TestRunRules:
    """Test run_rules, sequential and parallel."""

    def test_sequential_runs_each_folder_in_order(self, account: Account) -> None:
        rules = _rules(account)
        results = [f.new_report(True) for f, _ in rules]
        with patch.object(Folder, "run", autospec=True, side_effect=results) as run:
            reports = run_rules(rules, dry_run=True)
        assert [c.args for c in run.call_args_list] == [(f, fs) for f, fs in rules]
        assert all(c.kwargs == {"dry_run": True} for c in run.call_args_list)
        assert all(r is e for r, e in zip(reports, results, strict=True))

    def test_sequential_stops_at_first_failure(self, account: Account) -> None:
        rules = _rules(account)
        with (
            patch.object(Folder, "run", autospec=True, side_effect=[None, OSError("boom"), None]),
            pytest.raises(OSError, match="boom"),
        ):
            run_rules(rules, dry_run=False)

    def test_parallel_runs_every_folder(self, account: Account) -> None:
        rules = _rules(account)
        with patch.object(Folder, "run_into", autospec=True) as run:
            reports = run_rules(rules, dry_run=False, parallel=True)
        assert sorted(c.args[0].imap_name() for c in run.call_args_list) == [
            "INBOX",
            "Lists",
            "Spam",
        ]
        assert all(c.kwargs == {"dry_run": False} for c in run.call_args_list)
        assert [r["folder"] for r in reports] == [["INBOX"], ["Lists"], ["Spam"]]
        assert all(r["error"] is None and r["dry_run"] is False for r in reports)

    def test_parallel_failure_does_not_stop_other_folders(self, account: Account) -> None:
        rules = _rules(account)
        ran: list[str] = []

        def fake_run(
            folder: Folder, report: FolderReport, filters: list[Filter], *, dry_run: bool
        ) -> None:
            ran.append(folder.imap_name())
            report["filters"].append(
                {"spec": None, "action": "a", "matched": [], "executed": False}
            )
            if folder.imap_name() == "Lists":
                raise OSError("connection lost")

        with (
            patch.object(Folder, "run_into", autospec=True, side_effect=fake_run),
            pytest.raises(RunRulesError, match=r"^1 folder\(s\) failed: ") as excinfo,
        ):
            run_rules(rules, dry_run=False, parallel=True)
        assert isinstance(excinfo.value, RuntimeError)
        assert sorted(ran) == ["INBOX", "Lists", "Spam"]
        assert "test_account.Lists: connection lost" in str(excinfo.value)
        reports = excinfo.value.reports
        assert [r["error"] for r in reports] == [None, "connection lost", None]
        assert all(len(r["filters"]) == 1 for r in reports)

    def test_parallel_empty_rules(self) -> None:
        assert run_rules([], dry_run=False, parallel=True) == []


class TestStarredOnly:
    """Test starred_only wrapping."""

    def test_wraps_criterion_and_action(self, account: Account) -> None:
        rules = _rules(account)
        wrapped = starred_only(rules)
        assert [f for f, _ in wrapped] == [f for f, _ in rules]
        for (_, original), (_, filters) in zip(rules, wrapped, strict=True):
            assert len(filters) == len(original)
            for orig, new in zip(original, filters, strict=True):
                assert new.criterion == IsStarred() & orig.criterion
                assert new.action == Unstar() + orig.action

    def test_does_not_modify_input(self, account: Account) -> None:
        rules = _rules(account)
        before = [(f, list(fs)) for f, fs in rules]
        starred_only(rules)
        assert rules == before


SignalHandler = Callable[[int, FrameType | None], Any]


class FakeWatch:
    """Stands in for IdleWatch: records how it was made, started and stopped."""

    made: ClassVar[list[FakeWatch]] = []

    def __init__(
        self,
        connections: object,
        account_id: str,
        path: str,
        *,
        on_change: Callable[[IdleChange], None],
        on_connect: Callable[[], None] | None = None,
        name: str | None = None,
    ) -> None:
        self.account_id = account_id
        self.path = path
        self.on_change = on_change
        self.on_connect = on_connect
        self.started = threading.Event()
        self.stopped = False
        self.joined = False
        self.failure = None
        FakeWatch.made.append(self)

    def start(self) -> FakeWatch:
        self.started.set()
        return self

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        self.joined = True


@pytest.fixture
def watches() -> Iterator[list[FakeWatch]]:
    FakeWatch.made = []
    with patch("mailroom_core.filters.runner.IdleWatch", FakeWatch):
        yield FakeWatch.made


class TestMonitor:
    """Test monitor: a watch per folder, filters run on connect and new mail, stop on signals."""

    def test_runs_filters_on_connect_and_on_new_mail_only(
        self, account: Account, watches: list[FakeWatch]
    ) -> None:
        folder = account / "Lists" / "Nerds"
        filters = [Filter(FromIs("a@example.com"), MarkAsRead())]
        stop = threading.Event()
        with patch.object(Folder, "run", autospec=True) as run:
            thread = threading.Thread(
                target=monitor,
                args=([(folder, filters)],),
                kwargs={"dry_run": True, "stop": stop, "install_signal_handlers": False},
            )
            thread.start()
            try:
                assert _started(watches, 1)
                [watch] = watches
                assert (watch.account_id, watch.path) == ("test_account", "Lists.Nerds")
                assert watch.on_connect is not None
                watch.on_connect()
                watch.on_change(IdleChange(exists=3))
                watch.on_change(IdleChange(flags={3: frozenset({"\\Seen"})}))
            finally:
                stop.set()
                thread.join(5)
        assert not thread.is_alive()
        assert run.call_args_list == [call(folder, filters, dry_run=True)] * 2
        assert watch.stopped
        assert watch.joined

    @pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
    def test_stops_on_signal(
        self, account: Account, watches: list[FakeWatch], signum: signal.Signals
    ) -> None:
        handlers: dict[int, SignalHandler] = {}

        def fake_signal(sig: int, handler: SignalHandler) -> None:
            handlers[sig] = handler

        with patch.object(signal, "signal", side_effect=fake_signal):
            thread = threading.Thread(
                target=monitor, args=(_rules(account),), kwargs={"dry_run": True}
            )
            thread.start()
            assert _started(watches, 3)
            handlers[signum](signum, None)
            thread.join(5)
        assert not thread.is_alive()
        assert set(handlers) == {signal.SIGTERM, signal.SIGINT}
        assert all(w.stopped and w.joined for w in watches)

    def test_a_folder_whose_name_cannot_be_looked_up_is_skipped(
        self, account: Account, watches: list[FakeWatch], caplog: pytest.LogCaptureFixture
    ) -> None:
        rules = _rules(account)
        unreachable = rules[1][0]
        real = Folder.imap_name

        def imap_name(folder: Folder) -> str:
            if folder is unreachable:
                raise MailError("unreachable")
            return real(folder)

        stop = threading.Event()
        with patch.object(Folder, "imap_name", autospec=True, side_effect=imap_name):
            thread = threading.Thread(
                target=monitor,
                args=(rules,),
                kwargs={"dry_run": True, "stop": stop, "install_signal_handlers": False},
            )
            thread.start()
            try:
                assert _started(watches, 2)
                assert [w.path for w in watches] == ["INBOX", "Spam"]
                assert thread.is_alive()
            finally:
                stop.set()
                thread.join(5)
        assert not thread.is_alive()
        assert all(w.stopped and w.joined for w in watches)
        assert "Not watching test_account.Lists: unreachable." in caplog.text

    def test_restores_default_when_previous_handler_is_unknown(self) -> None:
        stop = threading.Event()
        stop.set()
        with patch.object(signal, "signal", return_value=None) as install:
            monitor([], dry_run=True, stop=stop)
        restored = [c.args for c in install.call_args_list[2:]]
        assert restored == [(signal.SIGTERM, signal.SIG_DFL), (signal.SIGINT, signal.SIG_DFL)]

    def test_already_stopped_watches_nothing(
        self, account: Account, watches: list[FakeWatch]
    ) -> None:
        stop = threading.Event()
        stop.set()
        monitor(_rules(account), dry_run=True, stop=stop, install_signal_handlers=False)
        assert watches == []

    def test_restores_previous_signal_handlers(self) -> None:
        stop = threading.Event()
        stop.set()
        before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        monitor([], dry_run=True, stop=stop)
        assert {s: signal.getsignal(s) for s in before} == before


def _started(watches: list[FakeWatch], count: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if len(watches) >= count and all(w.started.is_set() for w in watches):
            return True
        time.sleep(0.01)
    return False
