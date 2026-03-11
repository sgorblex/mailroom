"""Address-based email filtering criteria (From, To, CC, BCC)."""

from mailparser.core import MailParser

from imap_thingy.core import Message, Q
from imap_thingy.filters.criteria.criterion import Criterion
from imap_thingy.utils import matches


def _extract_emails(msg: MailParser, field: str) -> list[tuple[str, str]]:
    """Extract email addresses from a message field.

    Args:
        msg: Parsed email message.
        field: Field name ('from_', 'to', 'cc', 'bcc').

    Returns:
        List of (name, email) tuples.

    """
    field_value = getattr(msg, field, None)
    if not field_value:
        return []
    return field_value if isinstance(field_value, list) else [field_value]


class FromContains(Criterion):
    """Matches messages from addresses containing the given string."""

    def __init__(self, addr: str) -> None:
        """Initialize a FromContains criterion.

        Args:
            addr: String to search for in sender addresses.

        """

        def func(msg: Message) -> bool:
            return any(addr in email for name, email in _extract_emails(msg.parsed, "from_"))

        super().__init__(func, Q(("FROM", addr)), is_efficient=True)


class FromIs(Criterion):
    """Matches messages from the exact email address."""

    def __init__(self, addr: str) -> None:
        """Initialize a FromIs criterion.

        Args:
            addr: Exact email address to match.

        """

        def func(msg: Message) -> bool:
            return any(addr == email for name, email in _extract_emails(msg.parsed, "from_"))

        super().__init__(func, imap_query=Q(("FROM", addr)), is_efficient=True)


class FromMatches(Criterion):
    """Matches messages from addresses matching the regex pattern."""

    def __init__(self, pattern: str) -> None:
        """Initialize a FromMatches criterion.

        Args:
            pattern: Regular expression pattern to match against email addresses.

        """

        def func(msg: Message) -> bool:
            return any(matches(pattern, email) for name, email in _extract_emails(msg.parsed, "from_"))

        super().__init__(func)


class FromMatchesName(Criterion):
    """Matches messages from sender names matching the regex pattern."""

    def __init__(self, pattern: str) -> None:
        """Initialize a FromMatchesName criterion.

        Args:
            pattern: Regular expression pattern to match against sender names.

        """

        def func(msg: Message) -> bool:
            return any(matches(pattern, name) for name, email in _extract_emails(msg.parsed, "from_"))

        super().__init__(func)


class ToContains(Criterion):
    """Matches messages to addresses containing the given string.

    Can optionally include CC and BCC fields in the match.
    """

    def __init__(self, addr: str, incl_cc: bool = False, incl_bcc: bool = False) -> None:
        """Initialize a ToContains criterion.

        Args:
            addr: String to search for in recipient addresses.
            incl_cc: Whether to also check CC field (default: False).
            incl_bcc: Whether to also check BCC field (default: False).

        """

        def func(msg: Message) -> bool:
            return any(addr in email for name, email in _extract_emails(msg.parsed, "to"))

        criterion = Criterion(func, Q(("TO", addr)), is_efficient=True)
        if incl_cc:
            criterion |= CcContains(addr)
        if incl_bcc:
            criterion |= BccContains(addr)
        super().__init__(criterion.func, criterion.imap_query, criterion.is_efficient)


class ToIs(Criterion):
    """Matches messages to the exact email address.

    Can optionally include CC and BCC fields in the match.
    """

    def __init__(self, addr: str, incl_cc: bool = False, incl_bcc: bool = False) -> None:
        """Initialize a ToIs criterion.

        Args:
            addr: Exact email address to match.
            incl_cc: Whether to also check CC field (default: False).
            incl_bcc: Whether to also check BCC field (default: False).

        """

        def func(msg: Message) -> bool:
            return any(addr == email for name, email in _extract_emails(msg.parsed, "to"))

        criterion = Criterion(func, imap_query=Q(("TO", addr)), is_efficient=True)
        if incl_cc:
            criterion |= CcIs(addr)
        if incl_bcc:
            criterion |= BccIs(addr)
        super().__init__(criterion.func, criterion.imap_query, criterion.is_efficient)


class CcContains(Criterion):
    """Matches messages CC'd to addresses containing the given string."""

    def __init__(self, addr: str) -> None:
        """Initialize a CcContains criterion.

        Args:
            addr: String to search for in CC addresses.

        """

        def func(msg: Message) -> bool:
            return any(addr in email for name, email in _extract_emails(msg.parsed, "cc"))

        super().__init__(func, Q(("CC", addr)), is_efficient=True)


class CcIs(Criterion):
    """Matches messages CC'd to the exact email address."""

    def __init__(self, addr: str) -> None:
        """Initialize a CcIs criterion.

        Args:
            addr: Exact email address to match.

        """

        def func(msg: Message) -> bool:
            return any(addr == email for name, email in _extract_emails(msg.parsed, "cc"))

        super().__init__(func, imap_query=Q(("CC", addr)), is_efficient=True)


class CcMatches(Criterion):
    """Matches messages CC'd to addresses matching the regex pattern."""

    def __init__(self, pattern: str) -> None:
        """Initialize a CcMatches criterion.

        Args:
            pattern: Regular expression pattern to match against CC addresses.

        """

        def func(msg: Message) -> bool:
            return any(matches(pattern, email) for name, email in _extract_emails(msg.parsed, "cc"))

        super().__init__(func)


class BccContains(Criterion):
    """Matches messages BCC'd to addresses containing the given string."""

    def __init__(self, addr: str) -> None:
        """Initialize a BccContains criterion.

        Args:
            addr: String to search for in BCC addresses.

        """

        def func(msg: Message) -> bool:
            return any(addr in email for name, email in _extract_emails(msg.parsed, "bcc"))

        super().__init__(func, Q(("BCC", addr)), is_efficient=True)


class BccIs(Criterion):
    """Matches messages BCC'd to the exact email address."""

    def __init__(self, addr: str) -> None:
        """Initialize a BccIs criterion.

        Args:
            addr: Exact email address to match.

        """

        def func(msg: Message) -> bool:
            return any(addr == email for name, email in _extract_emails(msg.parsed, "bcc"))

        super().__init__(func, imap_query=Q(("BCC", addr)), is_efficient=True)


class BccMatches(Criterion):
    """Matches messages BCC'd to addresses matching the regex pattern."""

    def __init__(self, pattern: str) -> None:
        """Initialize a BccMatches criterion.

        Args:
            pattern: Regular expression pattern to match against BCC addresses.

        """

        def func(msg: Message) -> bool:
            return any(matches(pattern, email) for name, email in _extract_emails(msg.parsed, "bcc"))

        super().__init__(func)


class ToMatches(Criterion):
    """Matches messages to addresses matching the regex pattern.

    Can optionally include CC and BCC fields in the match.
    """

    def __init__(self, pattern: str, incl_cc: bool = False, incl_bcc: bool = False) -> None:
        """Initialize a ToMatches criterion.

        Args:
            pattern: Regular expression pattern to match against email addresses.
            incl_cc: Whether to also check CC field (default: False).
            incl_bcc: Whether to also check BCC field (default: False).

        """

        def func(msg: Message) -> bool:
            return any(matches(pattern, email) for name, email in _extract_emails(msg.parsed, "to"))

        criterion = Criterion(func)
        if incl_cc:
            criterion |= CcMatches(pattern)
        if incl_bcc:
            criterion |= BccMatches(pattern)
        super().__init__(criterion.func, criterion.imap_query)
