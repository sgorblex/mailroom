from __future__ import annotations

import io
import json
import logging
import sys
from typing import TYPE_CHECKING

import pytest

from mailroom.observability.logging_config import (
    configure_logging,
    reset_logging_configuration_for_tests,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def json_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    """Mailroom's logging as in production (JSON, from INFO), its handler writing here."""
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    monkeypatch.setenv("MAILROOM_LOG_FORMAT", "json")
    monkeypatch.delenv("MAILROOM_LOG_LEVEL", raising=False)
    configure_logging(force=True)
    yield stream
    reset_logging_configuration_for_tests()


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_core_mail_records_are_json_with_their_fields(json_logs: io.StringIO) -> None:
    """mailroom-core's mail logger goes through Mailroom's handler, not Python's last-resort one
    (which printed the bare message), with the account, folder and mapped code."""
    logging.getLogger("mailroom_core.mail").warning(
        "idle_ended",
        extra={"event": "idle_ended", "account": "acc", "folder": "INBOX", "mapped": "auth_failed"},
    )
    [line] = _lines(json_logs)
    assert (line["logger"], line["level"]) == ("mailroom_core.mail", "WARNING")
    assert (line["event"], line["account"], line["folder"], line["mapped"]) == (
        "idle_ended",
        "acc",
        "INBOX",
        "auth_failed",
    )


def test_core_send_records_go_through_from_info(json_logs: io.StringIO) -> None:
    logging.getLogger("mailroom_core.send").info(
        "smtp_quit_failed", extra={"event": "smtp_quit_failed", "error_type": "OSError"}
    )
    [line] = _lines(json_logs)
    assert (line["logger"], line["event"], line["error_type"]) == (
        "mailroom_core.send",
        "smtp_quit_failed",
        "OSError",
    )


def test_the_filter_engine_stays_quiet_below_warning(json_logs: io.StringIO) -> None:
    logging.getLogger("mailroom_core.filters.account").info("searching")
    logging.getLogger("mailroom_core.filters.account").warning("skipped")
    assert [line["message"] for line in _lines(json_logs)] == ["skipped"]
