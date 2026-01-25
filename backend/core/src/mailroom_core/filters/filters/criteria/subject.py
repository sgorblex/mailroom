"""Subject-based email filtering criteria."""

import re

from imap_thingy.filters.criteria.base import Criterion, EfficientCriterion, ParsedMail


def _matches(pattern: str, string: str) -> bool:
    """Check if a string fully matches a regex pattern."""
    return bool(re.fullmatch(pattern, string))


class SubjectContains(EfficientCriterion):
    """Matches messages with subject containing the given substring."""

    def __init__(self, substring: str) -> None:
        """Initialize a SubjectContains criterion.

        Args:
            substring: Substring to search for in the subject.

        """

        def func(msg: ParsedMail) -> bool:
            return substring in (msg.subject or "")

        super().__init__(func, imap_query=["SUBJECT", substring])


class SubjectIs(Criterion):
    """Matches messages with the exact subject line."""

    def __init__(self, subj: str) -> None:
        """Initialize a SubjectIs criterion.

        Args:
            subj: Exact subject line to match.

        """

        def func(msg: ParsedMail) -> bool:
            return (msg.subject or "") == subj

        super().__init__(func, imap_query=["SUBJECT", subj])


class SubjectMatches(Criterion):
    """Matches messages with subject matching the regex pattern."""

    def __init__(self, pattern: str) -> None:
        """Initialize a SubjectMatches criterion.

        Args:
            pattern: Regular expression pattern to match against the subject.

        """

        def func(msg: ParsedMail) -> bool:
            return _matches(pattern, msg.subject or "")

        super().__init__(func)
