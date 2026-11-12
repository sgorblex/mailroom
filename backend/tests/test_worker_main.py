"""``python -m mailroom.worker``: it reads ``MAILROOM_FEATURES`` before the database, and exits 1
when its job loop stops without being asked."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from mailroom.worker import __main__ as worker_main

if TYPE_CHECKING:
    import threading
    from collections.abc import AsyncIterator, Awaitable, Callable


class DatabaseReachedError(Exception):
    pass


@pytest.fixture(autouse=True)
def _no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Logging and the root ``.env`` left alone; the database step fails if reached."""

    def reached() -> str:
        raise DatabaseReachedError

    monkeypatch.setattr(worker_main, "configure_logging", lambda: None)
    monkeypatch.setattr(worker_main, "load_local_environment", lambda: None)
    monkeypatch.setattr(worker_main, "get_sync_database_url", reached)


def test_without_filters_it_exits_before_the_database(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MAILROOM_FEATURES", "mail,mcp")
    caplog.set_level(logging.INFO, logger="mailroom.worker")
    worker_main.main()
    messages = [record.getMessage() for record in caplog.records]
    assert "MAILROOM_FEATURES: mail, mcp" in messages
    assert "No enabled feature needs the worker (filters is off); exiting." in messages


def test_an_unknown_feature_exits_1(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MAILROOM_FEATURES", "filters,bogus")
    with pytest.raises(SystemExit) as exited:
        worker_main.main()
    assert exited.value.code == 1
    [record] = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert "bogus" in record.getMessage()


def test_unset_it_goes_on_to_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAILROOM_FEATURES", raising=False)
    with pytest.raises(DatabaseReachedError):
        worker_main.main()


class _FakeApp:
    """procrastinate's app around a job loop that ``run`` stands for."""

    def __init__(self, run: Callable[[], Awaitable[None]]) -> None:
        self._run = run
        self.kwargs: dict[str, object] = {}

    @contextlib.asynccontextmanager
    async def open_async(self) -> AsyncIterator[None]:
        yield

    async def run_worker_async(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        await self._run()


def test_a_stop_signal_stops_the_jobs_as_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGTERM cancels procrastinate's loop (its graceful stop): an asked stop."""

    async def until_cancelled() -> None:
        asyncio.get_running_loop().call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)
        await asyncio.Event().wait()

    app = _FakeApp(until_cancelled)
    monkeypatch.setattr(worker_main, "app", app)
    assert asyncio.run(worker_main.run_jobs()) is True
    assert app.kwargs["install_signal_handlers"] is False
    # The handlers are gone again: SIGTERM is back to its default.
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL


def test_jobs_that_stop_on_their_own_are_no_asked_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """procrastinate stops itself when a side task fails (its database went away)."""

    async def stops_itself() -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(worker_main, "app", _FakeApp(stops_itself))
    assert asyncio.run(worker_main.run_jobs()) is False


def test_a_worker_that_stops_on_its_own_exits_1(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """So ``Restart=on-failure`` starts it again."""
    monkeypatch.delenv("MAILROOM_FEATURES", raising=False)
    monkeypatch.setattr(worker_main, "get_sync_database_url", lambda: "postgresql://x/y")
    monkeypatch.setattr(worker_main, "wait_for_sync_database", lambda _url: _Disposable())
    monkeypatch.setattr(worker_main, "wait_for_head", lambda _engine: None)
    monkeypatch.setattr(worker_main, "worker_lock", lambda _url: contextlib.nullcontext())
    monkeypatch.setattr(
        worker_main, "app", SimpleNamespace(replace_connector=lambda _c: contextlib.nullcontext())
    )
    monkeypatch.setattr(worker_main, "database_connector", lambda _url: None)
    monkeypatch.setattr(worker_main, "_settle", _nothing)
    context = SimpleNamespace(
        sessions=contextlib.nullcontext,
        mailboxes=SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(worker_main, "worker_context", lambda: context)
    monkeypatch.setattr(worker_main, "close_interrupted_runs", lambda _db: [])
    monkeypatch.setattr(worker_main, "IdleSupervisor", _IdleSupervisor)

    async def stopped_on_its_own() -> bool:
        return False

    monkeypatch.setattr(worker_main, "run_jobs", stopped_on_its_own)
    with pytest.raises(SystemExit) as exited:
        worker_main.main()
    assert exited.value.code == 1
    assert any("without being asked" in record.getMessage() for record in caplog.records)


class _Disposable:
    def dispose(self) -> None:
        pass


class _IdleSupervisor:
    def __init__(self, *_args: object) -> None:
        pass

    def run(self, stop: threading.Event) -> None:
        stop.wait()


async def _nothing() -> None:
    pass
