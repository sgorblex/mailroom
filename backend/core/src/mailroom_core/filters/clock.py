"""The date a run counts from, for criteria relative to it (`OlderThanDays`)."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mailroom_core.config import TimeZoneName

_run_zone: ContextVar[ZoneInfo | None] = ContextVar("run_zone", default=None)


@contextmanager
def today_in(time_zone: TimeZoneName | None) -> Iterator[None]:
    """Within the block, today is ``time_zone``'s (checked by `time_zone_name`); None leaves the
    machine's."""
    token = _run_zone.set(None if time_zone is None else ZoneInfo(time_zone))
    try:
        yield
    finally:
        _run_zone.reset(token)


def today() -> date:
    """Today in the run's time zone, else the machine's."""
    zone = _run_zone.get()
    return datetime.now(zone).date() if zone is not None else datetime.now().astimezone().date()
