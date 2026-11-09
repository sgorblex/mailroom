"""``python -m mailroom.worker``'s start: it reads ``MAILROOM_FEATURES`` before the database."""

from __future__ import annotations

import logging

import pytest

from mailroom.worker import __main__ as worker_main


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
