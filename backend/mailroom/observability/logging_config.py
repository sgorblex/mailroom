from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from mailroom.database import load_local_environment

_CONFIGURED = False

_EXTRA_LOG_KEYS = (
    "event",
    "http_method",
    "http_path",
    "http_status_code",
    "duration_ms",
    "task_name",
    "job_id",
    "attempt",
    "queue",
    "user_id",
    "target_user_id",
    "account_id",
    "outcome",
    "run_filters",
    "password_changed",
    "rule_set_id",
    "filter_id",
    "run_id",
    "trigger",
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        from mailroom.observability.request_context import request_id_ctx

        current = request_id_ctx.get()
        record.request_id = current if current is not None else "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key in _EXTRA_LOG_KEYS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_LIBRARY_LOGGERS = ("procrastinate",)
"""Libraries whose records go through Mailroom's handler too (not Python's last-resort one)."""

_REQUEST_ID_DISPLAY_LEN = 8

_LEVEL_COLORS = {
    "DEBUG": "36",  # cyan
    "INFO": "32",  # green
    "WARNING": "33",  # yellow
    "ERROR": "31",  # red
    "CRITICAL": "31",  # red
}


def _duration_text(duration_ms: object) -> str:
    if isinstance(duration_ms, int | float):
        return f"{duration_ms:.0f}ms"
    return "?ms"


def _short_request_id(request_id: object) -> str:
    if not isinstance(request_id, str) or request_id in ("", "-"):
        return "-"
    return request_id[:_REQUEST_ID_DISPLAY_LEN]


class HumanFormatter(logging.Formatter):
    """One readable line per record, for local development.

    The JSON formatter is the source of truth for every field; this trades completeness for
    scannability. Every line starts with the time, level and a shortened request id; HTTP requests
    and job runs -- almost all of the line volume in dev -- then get a dedicated layout
    (method/path/status or task/attempt), and everything else falls back to the logger name,
    message and ``key=value`` extras.
    """

    def __init__(self, *, use_color: bool = False) -> None:
        super().__init__()
        self._use_color = use_color

    def _level(self, record: logging.LogRecord) -> str:
        text = f"{record.levelname:<7}"
        color = _LEVEL_COLORS.get(record.levelname) if self._use_color else None
        return f"\033[{color}m{text}\033[0m" if color else text

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, "%H:%M:%S")
        request_id = _short_request_id(getattr(record, "request_id", "-"))
        prefix = f"{timestamp} {self._level(record)} [{request_id}]"
        event = getattr(record, "event", None)
        if event == "http_request":
            body = self._http_body(record)
        elif event == "job_run" and hasattr(record, "task_name"):
            body = self._job_body(record)
        else:
            body = self._generic_body(record)
        line = f"{prefix} {body}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line

    def _http_body(self, record: logging.LogRecord) -> str:
        method = getattr(record, "http_method", "?")
        path = getattr(record, "http_path", "?")
        status = getattr(record, "http_status_code", "?")
        duration = _duration_text(getattr(record, "duration_ms", None))
        message = record.getMessage()
        label = "" if message == "request_completed" else f" {message}"
        return f"{method:<6} {path} {status} {duration}{label}"

    def _job_body(self, record: logging.LogRecord) -> str:
        task_name = getattr(record, "task_name", "?")
        attempt = getattr(record, "attempt", "?")
        duration = _duration_text(getattr(record, "duration_ms", None))
        return f"{record.getMessage()} task={task_name} attempt={attempt} {duration}"

    def _generic_body(self, record: logging.LogRecord) -> str:
        extras = " ".join(
            f"{key}={getattr(record, key)}"
            for key in _EXTRA_LOG_KEYS
            if key != "event" and hasattr(record, key)
        )
        suffix = f" {extras}" if extras else ""
        return f"{record.name} {record.getMessage()}{suffix}"


def _default_log_format() -> str:
    raw = os.environ.get("MAILROOM_LOG_FORMAT", "").strip().lower()
    if raw in ("json", "text"):
        return raw
    if os.environ.get("MAILROOM_ENV", "production").strip().lower() == "development":
        return "text"
    return "json"


_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _log_level() -> int:
    raw = os.environ.get("MAILROOM_LOG_LEVEL", "").strip()
    if not raw:
        return logging.INFO
    name = raw.upper()
    if name not in _LOG_LEVELS:
        msg = f"MAILROOM_LOG_LEVEL must be one of {', '.join(_LOG_LEVELS)}; got {raw!r}"
        raise ValueError(msg)
    return logging.getLevelNamesMapping()[name]


def configure_logging(*, force: bool = False) -> None:
    global _CONFIGURED  # noqa: PLW0603
    if _CONFIGURED and not force:
        return
    load_local_environment()

    log_format = _default_log_format()

    mailroom_log = logging.getLogger("mailroom")
    mailroom_log.handlers.clear()
    mailroom_log.setLevel(_log_level())

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestIdFilter())
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        use_color = sys.stderr.isatty() and not os.environ.get("NO_COLOR")
        handler.setFormatter(HumanFormatter(use_color=use_color))
    mailroom_log.addHandler(handler)
    mailroom_log.propagate = False
    for name in _LIBRARY_LOGGERS:
        library_log = logging.getLogger(name)
        library_log.handlers.clear()
        library_log.setLevel(max(_log_level(), logging.INFO))
        library_log.addHandler(handler)
        library_log.propagate = False
    _CONFIGURED = True


def reset_logging_configuration_for_tests() -> None:
    global _CONFIGURED  # noqa: PLW0603
    _CONFIGURED = False
    mailroom_log = logging.getLogger("mailroom")
    mailroom_log.handlers.clear()
    mailroom_log.propagate = True
    for name in _LIBRARY_LOGGERS:
        library_log = logging.getLogger(name)
        library_log.handlers.clear()
        library_log.propagate = True
