from __future__ import annotations

import logging
import os
import signal
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DisconnectionError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

CONNECTIVITY_ERRORS: tuple[type[BaseException], ...] = (
    OperationalError,
    DisconnectionError,
    SQLAlchemyTimeoutError,
)


def _raise_keyboard_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


@contextmanager
def interruptible_connectivity_wait() -> Iterator[None]:
    """Restore Ctrl+C during a blocking connectivity retry loop.

    Uvicorn's own SIGINT/SIGTERM handlers only set a ``should_exit`` flag instead of
    raising; that flag is checked by the event loop, which is exactly what this
    synchronous retry loop blocks, so an interrupt during startup is otherwise silently
    ignored until the loop finishes on its own (up to ``max_attempts * delay_sec``
    seconds). Temporarily reinstate the default raise-immediately behaviour and restore
    the previous handlers afterward.
    """
    previous_handlers: dict[signal.Signals, object] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.signal(sig, _raise_keyboard_interrupt)
        except ValueError:
            # Not the main thread — leave whatever handler is installed; Ctrl+C will
            # just be slow to take effect in that case.
            continue
    try:
        yield
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)  # type: ignore[arg-type]


def connectivity_retry_settings() -> tuple[int, float]:
    """Attempts and delay (seconds) between database connectivity checks."""
    try:
        max_attempts = int(os.environ.get("MAILROOM_STARTUP_MIGRATION_MAX_ATTEMPTS", "30").strip())
    except ValueError:
        max_attempts = 30
    max_attempts = max(1, min(max_attempts, 300))

    try:
        delay_sec = float(os.environ.get("MAILROOM_STARTUP_MIGRATION_RETRY_SECONDS", "1").strip())
    except ValueError:
        delay_sec = 1.0
    delay_sec = max(0.0, min(delay_sec, 60.0))
    return max_attempts, delay_sec


def wait_for_sync_database(database_url: str) -> Engine:
    """Return an engine after ``SELECT 1`` succeeds, retrying on connectivity errors."""
    engine = create_engine(database_url, pool_pre_ping=True)
    max_attempts, retry_delay_sec = connectivity_retry_settings()

    with interruptible_connectivity_wait():
        for attempt in range(1, max_attempts + 1):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
            except CONNECTIVITY_ERRORS as exc:
                if attempt >= max_attempts:
                    logger.warning(
                        "Database connectivity error (no success after %s attempt(s))",
                        max_attempts,
                        exc_info=exc,
                    )
                    engine.dispose()
                    raise
                logger.info(
                    "Database not ready (attempt %s/%s); retrying in %s s",
                    attempt,
                    max_attempts,
                    retry_delay_sec,
                )
                if retry_delay_sec > 0:
                    time.sleep(retry_delay_sec)
            else:
                return engine

    engine.dispose()
    message = "Database connectivity check failed without raising"
    raise RuntimeError(message)
