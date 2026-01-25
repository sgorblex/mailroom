"""Date-based email filtering criteria."""

from datetime import date, datetime

from imap_thingy.filters.criteria.base import EfficientCriterion, ParsedMail


class OlderThan(EfficientCriterion):
    """Matches messages older than the given date."""

    def __init__(self, cutoff_date: date | datetime | str) -> None:
        """Initialize an OlderThan criterion.

        Args:
            cutoff_date: Date to compare against. Can be a datetime.date, datetime.datetime, or
                         a string in format 'DD-MMM-YYYY' (e.g., '01-Jan-2025').

        """
        if isinstance(cutoff_date, str):
            cutoff_date = datetime.strptime(cutoff_date, "%d-%b-%Y").date()
        elif isinstance(cutoff_date, datetime):
            cutoff_date = cutoff_date.date()

        self.cutoff_date = cutoff_date
        imap_date_str = cutoff_date.strftime("%d-%b-%Y")

        def func(msg: ParsedMail) -> bool:
            msg_date = getattr(msg, "date", None)
            if msg_date is None:
                return False
            if isinstance(msg_date, datetime):
                msg_date = msg_date.date()
            elif isinstance(msg_date, date):
                pass
            elif isinstance(msg_date, str):
                try:
                    msg_date = datetime.strptime(msg_date, "%d-%b-%Y").date()
                except (ValueError, TypeError):
                    return False
            else:
                return False
            return msg_date < cutoff_date

        super().__init__(func, imap_query=["SENTBEFORE", imap_date_str])
