"""Tests for the rule runner: run_rules, starred_only and monitor."""

import signal
import threading
import time
from collections.abc import Callable, Iterator
from types import FrameType
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from imap_thingy.accounts import Folder, Path
from imap_thingy.filters import Filter, FromIs, IsStarred, MarkAsRead, MoveTo, Unstar
from imap_thingy.idle_monitor import IdleHandler, IdleMonitor
from imap_thingy.runner import Rules, RunRulesError, monitor, run_rules, starred_only

if TYPE_CHECKING:
    from imap_thingy.idle_monitor import IdleResponse
    from imap_thingy.report import FolderReport
    from tests.conftest import MockEMailAccount

POLL = 0.05


@pytest.fixture(autouse=True)
def _fast_idle_poll() -> Iterator[None]:
    with patch("imap_thingy.idle_monitor.IDLE_POLL", POLL):
        yield


def _rules(account: MockEMailAccount) -> Rules:
    return [
        (account / "INBOX", [Filter(FromIs("a@example.com"), MarkAsRead())]),
        (account / "Lists", [Filter(FromIs("b@example.com"), MoveTo(Path("Archive")))]),
        (account / "Spam", [Filter(FromIs("c@example.com"), MarkAsRead())]),
    ]


class TestRunRules:
    """Test run_rules, sequential and parallel."""

    def test_sequential_runs_each_folder_in_order(self, mock_account: MockEMailAccount) -> None:
        rules = _rules(mock_account)
        results = [f.new_report(True) for f, _ in rules]
        with patch.object(Folder, "run", autospec=True, side_effect=results) as run:
            reports = run_rules(rules, dry_run=True)
        assert [c.args for c in run.call_args_list] == [(f, fs) for f, fs in rules]
        assert all(c.kwargs == {"dry_run": True} for c in run.call_args_list)
        assert all(r is e for r, e in zip(reports, results, strict=True))

    def test_sequential_stops_at_first_failure(self, mock_account: MockEMailAccount) -> None:
        rules = _rules(mock_account)
        with (
            patch.object(Folder, "run", autospec=True, side_effect=[None, OSError("boom"), None]),
            pytest.raises(OSError, match="boom"),
        ):
            run_rules(rules, dry_run=False)

    def test_parallel_runs_every_folder(self, mock_account: MockEMailAccount) -> None:
        rules = _rules(mock_account)
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

    def test_parallel_failure_does_not_stop_other_folders(
        self, mock_account: MockEMailAccount
    ) -> None:
        rules = _rules(mock_account)
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

    def test_wraps_criterion_and_action(self, mock_account: MockEMailAccount) -> None:
        rules = _rules(mock_account)
        wrapped = starred_only(rules)
        assert [f for f, _ in wrapped] == [f for f, _ in rules]
        for (_, original), (_, filters) in zip(rules, wrapped, strict=True):
            assert len(filters) == len(original)
            for orig, new in zip(original, filters, strict=True):
                assert new.criterion == IsStarred() & orig.criterion
                assert new.action == Unstar() + orig.action

    def test_does_not_modify_input(self, mock_account: MockEMailAccount) -> None:
        rules = _rules(mock_account)
        before = [(f, list(fs)) for f, fs in rules]
        starred_only(rules)
        assert rules == before


SignalHandler = Callable[[int, FrameType | None], Any]


class TestMonitor:
    """Test monitor: dispatch on new mail and stop on SIGTERM/SIGINT."""

    @pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
    def test_runs_filters_on_new_mail_and_stops_on_signal(
        self, mock_account: MockEMailAccount, signum: signal.Signals
    ) -> None:
        folder = mock_account / "INBOX"
        filters = [Filter(FromIs("a@example.com"), MarkAsRead())]
        conn = mock_account.connect.return_value
        handlers: dict[int, SignalHandler] = {}
        installed = threading.Event()

        def fake_signal(sig: int, handler: SignalHandler) -> None:
            handlers[sig] = handler
            if len(handlers) == 2:
                installed.set()

        def send_signal(timeout: int) -> IdleResponse:
            assert installed.wait(5)
            handlers[signum](signum, None)
            return []

        # New mail, then a read-flag change (no dispatch), then the signal.
        responses: list[IdleResponse] = [
            [(1, b"EXISTS")],
            [(1, b"FETCH", (b"FLAGS", (b"\\Seen",)))],
        ]

        def idle_check(timeout: int) -> IdleResponse:
            return responses.pop(0) if responses else send_signal(timeout)

        conn.idle_check.side_effect = idle_check

        with (
            patch.object(signal, "signal", side_effect=fake_signal),
            patch.object(Folder, "run", autospec=True) as run,
        ):
            thread = threading.Thread(
                target=monitor, args=([(folder, filters)],), kwargs={"dry_run": True}
            )
            thread.start()
            thread.join(5)

        assert not thread.is_alive()
        assert set(handlers) == {signal.SIGTERM, signal.SIGINT}
        run.assert_called_once_with(folder, filters, dry_run=True)
        conn.logout.assert_called()

    def test_runs_in_a_thread_until_stop_is_set(self, mock_account: MockEMailAccount) -> None:
        folder = mock_account / "INBOX"
        filters = [Filter(FromIs("a@example.com"), MarkAsRead())]
        conn = mock_account.connect.return_value
        dispatched = threading.Event()
        responses: list[IdleResponse] = [[(1, b"EXISTS")]]

        def idle_check(timeout: float) -> IdleResponse:
            if responses:
                return responses.pop(0)
            time.sleep(timeout)
            return []

        conn.idle_check.side_effect = idle_check
        stop = threading.Event()

        with (
            patch.object(signal, "signal") as install,
            patch.object(
                Folder, "run", autospec=True, side_effect=lambda *a, **k: dispatched.set()
            ) as run,
        ):
            thread = threading.Thread(
                target=monitor,
                args=([(folder, filters)],),
                kwargs={"dry_run": False, "stop": stop, "install_signal_handlers": False},
            )
            thread.start()
            try:
                assert dispatched.wait(5)
            finally:
                stop.set()
                thread.join(5)

        assert not thread.is_alive()
        install.assert_not_called()
        run.assert_called_once_with(folder, filters, dry_run=False)
        conn.logout.assert_called_once()

    def test_restores_default_when_previous_handler_is_unknown(self) -> None:
        stop = threading.Event()
        stop.set()
        with patch.object(signal, "signal", return_value=None) as install:
            monitor([], dry_run=True, stop=stop)
        restored = [c.args for c in install.call_args_list[2:]]
        assert restored == [(signal.SIGTERM, signal.SIG_DFL), (signal.SIGINT, signal.SIG_DFL)]

    def test_already_stopped_connects_nothing(self, mock_account: MockEMailAccount) -> None:
        stop = threading.Event()
        stop.set()
        monitor(_rules(mock_account), dry_run=True, stop=stop, install_signal_handlers=False)
        mock_account.connect.assert_not_called()

    def test_restores_previous_signal_handlers(self, mock_account: MockEMailAccount) -> None:
        stop = threading.Event()
        stop.set()
        before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        monitor([], dry_run=True, stop=stop)
        assert {s: signal.getsignal(s) for s in before} == before


class TestIdleMonitorStop:
    """Test that IdleMonitor.stop() returns promptly and the watcher closes its connection."""

    def test_stop_is_prompt_and_closes_on_the_watcher_thread(
        self, mock_account: MockEMailAccount
    ) -> None:
        conn = mock_account.connect.return_value
        timeouts: list[float] = []
        idling = threading.Event()
        logout_threads: list[threading.Thread] = []

        def idle_check(timeout: float) -> IdleResponse:
            timeouts.append(timeout)
            idling.set()
            time.sleep(timeout)
            return []

        conn.idle_check.side_effect = idle_check
        conn.logout.side_effect = lambda: logout_threads.append(threading.current_thread())
        mon = IdleMonitor(mock_account / "INBOX", IdleHandler(lambda r: None)).start()
        assert mon._thread is not None
        try:
            assert idling.wait(5)
            started = time.monotonic()
        finally:
            mon.stop()
            mon._thread.join(5)

        assert not mon._thread.is_alive()
        assert time.monotonic() - started < POLL + 1
        assert max(timeouts) <= POLL
        conn.idle_done.assert_called()
        assert logout_threads == [mon._thread]

    def test_stop_before_start_is_harmless(self, mock_account: MockEMailAccount) -> None:
        mon = IdleMonitor(mock_account / "INBOX", IdleHandler(lambda r: None))
        mon.stop()
        mon.join()
        with pytest.raises(RuntimeError, match="one-shot"):
            mon.start()
        mock_account.connect.assert_not_called()
