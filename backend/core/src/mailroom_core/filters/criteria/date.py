"""Date-based email filtering criteria."""

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from mailroom_core.filters.criteria.criterion import Criterion
from mailroom_core.filters.query import IMAPQuery, Q
from mailroom_core.filters.spec.nodes import DATE, as_date, codecs

if TYPE_CHECKING:
    from mailroom_core.filters.message import Message


class OlderThan(Criterion):
    """Matches messages older than the given date."""

    spec_type = "older_than"
    spec_codecs = codecs(cutoff_date=DATE)

    def __init__(self, cutoff_date: date | datetime | str) -> None:
        """Initialize an OlderThan criterion.

        Args:
            cutoff_date: Date to compare against. Can be a datetime.date, datetime.datetime, or
                         a string in format 'DD-MMM-YYYY' (e.g., '01-Jan-2025').

        """
        cutoff_date = as_date(cutoff_date)
        self.cutoff_date = cutoff_date
        super().__init__(
            lambda msg: _sent_before(msg, cutoff_date),
            imap_query=_sent_before_query(cutoff_date),
            is_efficient=True,
        )


MAX_DAYS = 36500
"""Largest ``OlderThanDays`` age: a century, so the cutoff stays a four-digit-year IMAP date."""


class OlderThanDays(Criterion):
    """Matches messages sent before the day that was ``days`` days before the run's date.

    Relative to when it runs, not when it is built: ``OlderThanDays(30)`` in a rule that runs
    every day always means "sent more than 30 whole days before the day it runs" (calendar days,
    like IMAP ``SENTBEFORE``). ``days=0`` means sent before today.
    """

    spec_type = "older_than_days"

    def __init__(self, days: int) -> None:
        """Initialize an OlderThanDays criterion.

        Args:
            days: Age in days, an integer from 0 to MAX_DAYS.

        Raises:
            TypeError: days is not an int.
            ValueError: days is negative or above MAX_DAYS.

        """
        if isinstance(days, bool) or not isinstance(days, int):
            raise TypeError(f"days must be an int, got {type(days).__name__}")
        if not 0 <= days <= MAX_DAYS:
            raise ValueError(f"days must be between 0 and {MAX_DAYS}, got {days}")
        self.days = days

        def cutoff() -> date:
            return _today() - timedelta(days=days)

        super().__init__(
            lambda msg: _sent_before(msg, cutoff()),
            imap_query=IMAPQuery(IMAPQuery.Deferred(lambda: _sent_before_query(cutoff()))),
            is_efficient=True,
        )


def _today() -> date:
    return datetime.now().astimezone().date()


def _sent_before_query(cutoff: date) -> IMAPQuery:
    # A date, which core writes as IMAP does (`01-Mar-2026`) in any locale.
    return Q(("SENTBEFORE", cutoff))


def _sent_before(msg: Message, cutoff: date) -> bool:
    sent = msg.parsed.date
    return sent is not None and sent.date() < cutoff
