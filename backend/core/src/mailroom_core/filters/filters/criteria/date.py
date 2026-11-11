"""Date-based email filtering criteria."""

from datetime import date, datetime, timedelta

from imap_thingy.core import IMAPQuery, Message, Q
from imap_thingy.core.spec import DATE, as_date, codecs
from imap_thingy.filters.criteria.criterion import Criterion


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
    return Q(("SENTBEFORE", cutoff.strftime("%d-%b-%Y")))


def _sent_before(msg: Message, cutoff: date) -> bool:
    msg_date = getattr(msg.parsed, "date", None)
    if msg_date is None:
        return False
    if isinstance(msg_date, datetime):
        msg_date = msg_date.date()
    elif isinstance(msg_date, date):
        pass
    elif isinstance(msg_date, str):
        try:
            msg_date = datetime.strptime(msg_date, "%d-%b-%Y").date()  # noqa: DTZ007 -- date only
        except ValueError, TypeError:
            return False
    else:
        return False
    return msg_date < cutoff
