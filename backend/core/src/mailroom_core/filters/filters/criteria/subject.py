"""Subject-based email filtering criteria."""

from imap_thingy.core import Message, Q
from imap_thingy.filters.criteria.criterion import Criterion
from imap_thingy.utils import matches


class SubjectContains(Criterion):
    """Matches messages with subject containing the given substring."""

    def __init__(self, substring: str) -> None:
        """Initialize a SubjectContains criterion.

        Args:
            substring: Substring to search for in the subject.

        """

        def func(msg: Message) -> bool:
            return substring in (msg.parsed.subject or "")

        super().__init__(func, Q(("SUBJECT", substring)), is_efficient=True)


class SubjectIs(Criterion):
    """Matches messages with the exact subject line."""

    def __init__(self, subj: str) -> None:
        """Initialize a SubjectIs criterion.

        Args:
            subj: Exact subject line to match.

        """

        def func(msg: Message) -> bool:
            return (msg.parsed.subject or "") == subj

        super().__init__(func, imap_query=Q(("SUBJECT", subj)), is_efficient=True)


class SubjectMatches(Criterion):
    """Matches messages with subject matching the regex pattern."""

    def __init__(self, pattern: str) -> None:
        """Initialize a SubjectMatches criterion.

        Args:
            pattern: Regular expression pattern to match against the subject.

        """

        def func(msg: Message) -> bool:
            return matches(pattern, msg.parsed.subject or "")

        super().__init__(func)
