"""A user's audit trail as CSV: the records ``mailroom.audit.listing`` lists, a row each."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from mailroom.audit.listing import record_pages
from mailroom.errors import Refusal

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from sqlalchemy.orm import Session

    from mailroom.audit.listing import ListedRecord
    from mailroom.reach import Reach

COLUMNS: Final = (
    "id",
    "at",
    "actor",
    "app",
    "client_id",
    "grant_id",
    "token_id",
    "rule_set",
    "rule_set_id",
    "run_id",
    "action",
    "account_id",
    "account",
    "result",
    "detail",
)
"""``ListedRecord``'s fields, with the login of the ``account``, while there is one; ``at`` in UTC
and ``detail`` as JSON."""
_FORMULA: Final = "=+-@＝＋－＠"  # noqa: RUF001 -- the full-width forms, as Excel reads
"""What a spreadsheet takes a cell starting with for a formula."""
_RUN: Final = re.compile(
    rf"\A(?=[\t\r\n{re.escape(_FORMULA)}])"
    rf'|(?<=[;\t\r\n])(?=[\s"]*[{re.escape(_FORMULA)}])'
)
"""Where a cell can start with one: the text's start, and after what a spreadsheet splitting the
file on another separator (Excel on ``;`` in most of Europe) takes for the end of a cell. It then
reads the quotes around the text otherwise than ``csv`` wrote them, so those before do not count."""


def trail_csv(
    db: Session,
    user_id: int,
    *,
    reach: Reach,
    logins: Mapping[int, str],
    since: datetime | None,
    until: datetime | None,
) -> Iterator[str]:
    """The trail's CSV (RFC 4180) a chunk at a time, its header first, newest record first.
    ``logins``: those of the accounts the caller has, by id."""
    yield _csv([COLUMNS])
    for page in record_pages(db, user_id, reach=reach, since=since, until=until):
        yield _csv(_row(record, logins) for record in page)


def day_bounds(
    since: date | None, until: date | None, zone: str
) -> tuple[datetime | None, datetime | None]:
    """The instants ``since`` starts and ``until`` ends at in ``zone``, for a trail of those days
    and the ones between; ``invalid_range`` for days in the wrong order, or that no record can
    have."""
    if since is not None and until is not None and until < since:
        raise Refusal("invalid", "invalid_range", "until is before since.")
    return (
        None if since is None else _start(since, zone),
        None if until is None else _start(until, zone, plus=1),
    )


def _start(day: date, zone: str, *, plus: int = 0) -> datetime:
    try:
        local = datetime.combine(day + timedelta(days=plus), time.min, ZoneInfo(zone))
        return local.astimezone(UTC)
    except OverflowError:
        raise Refusal("invalid", "invalid_range", "Those days cannot be exported.") from None


def _csv(rows: Iterable[Iterable[object]]) -> str:
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue()


def _row(record: ListedRecord, logins: Mapping[int, str]) -> list[object]:
    values = {
        **record.model_dump(exclude={"at", "detail"}),
        "at": record.at.astimezone(UTC).isoformat(),
        "account": logins.get(record.account_id),
        "detail": json.dumps(record.detail, ensure_ascii=False).replace(";", r"\u003b"),
    }
    return [_cell(values[column]) for column in COLUMNS]


def _cell(value: object) -> object:
    """``value``, an apostrophe before text that a spreadsheet would run (an app names itself)."""
    return _RUN.sub("'", value) if isinstance(value, str) else value
