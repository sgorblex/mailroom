"""Criterion-based filtering system for email messages.

Provides a flexible system for building email filters using criteria (conditions)
and actions. Criteria can be combined with boolean operators and actions can be chained.
"""

import logging
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import mailparser
from imapclient import IMAPClient, imapclient

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.interfaces import OneAccountFilter

logger = logging.getLogger("imap-thingy")

# Type alias for parsed mail message
ParsedMail = Any  # mailparser doesn't have type stubs


def _get_mail(client: IMAPClient, imap_query: list[str | list[Any]]) -> list[tuple[int, ParsedMail]]:
    logger.info(f"Fetching mail with IMAP query {imap_query}")

    msg_ids = client.search(imap_query)
    fetched = client.fetch(msg_ids, ["BODY.PEEK[]"])

    messages: list[tuple[int, ParsedMail]] = []
    for msgid, data in fetched.items():
        msg = mailparser.parse_from_bytes(data[b"BODY[]"])
        messages.append((msgid, msg))

    return messages


def _matches(pattern: str, string: str) -> bool:
    return bool(re.fullmatch(pattern, string))


class FilterCriterion:
    """Represents a criterion (condition) for filtering email messages.

    Criteria can be combined using boolean operators (&, |, ~) and support
    both server-side (IMAP query) and client-side (parsed message) filtering.
    The imap_query is a preliminary filter applied at the IMAP level to limit
    client-side filtering when an appropriate query is provided.
    """

    def __init__(self, func: Callable[[ParsedMail], bool], imap_query: list[str | list[Any]] | None = None) -> None:
        """Initialize a filter criterion.

        Args:
            func: Function that takes a parsed mail message and returns True if it matches.
            imap_query: Optional IMAP query to pre-filter messages server-side.

        """
        self.func = func
        self.imap_query = imap_query

    def filter(self, connection: IMAPClient) -> list[int]:
        """Filter messages using this criterion.

        Args:
            connection: IMAP connection to use for filtering.

        Returns:
            List of message IDs that match the criterion.

        """
        imap_query = self.imap_query if self.imap_query else ["ALL"]
        messages = _get_mail(connection, imap_query)
        return [msgid for msgid, msg in messages if self.func(msg)]

    def __and__(self, other: "FilterCriterion") -> "FilterCriterion":
        def func(msg: ParsedMail) -> bool:
            return self.func(msg) and other.func(msg)

        imap_query: list[str | list[Any]] | None
        if self.imap_query and other.imap_query:
            imap_query = self.imap_query + other.imap_query
        else:
            imap_query = self.imap_query if self.imap_query else other.imap_query
        return FilterCriterion(func, imap_query)

    def __or__(self, other: "FilterCriterion") -> "FilterCriterion":
        def func(msg: ParsedMail) -> bool:
            return self.func(msg) or other.func(msg)

        imap_query: list[str | list[Any]] | None = None
        if self.imap_query and other.imap_query:
            imap_query = ["OR", self.imap_query, other.imap_query]
        return FilterCriterion(func, imap_query)

    def __invert__(self) -> "FilterCriterion":
        def func(msg: ParsedMail) -> bool:
            return not self.func(msg)

        imap_query: list[str | list[Any]] | None = ["NOT", self.imap_query] if self.imap_query else None
        return FilterCriterion(func, imap_query)


class EfficientCriterion(FilterCriterion):
    """A criterion that can be evaluated entirely server-side via IMAP queries.

    If a criterion only needs information obtainable via an IMAP query, there is
    no need to fetch and parse messages, so it can be performed more efficiently.
    """

    def __init__(self, func: Callable[[ParsedMail], bool], imap_query: list[str | list[Any]]) -> None:
        """Initialize an efficient criterion.

        Args:
            func: Function for client-side validation (used when needed).
            imap_query: IMAP query that can filter messages server-side (required).

        """
        super().__init__(func, imap_query)

    def filter(self, connection: IMAPClient) -> list[int]:
        """Filter messages using server-side IMAP query only.

        Args:
            connection: IMAP connection to use for filtering.

        Returns:
            List of message IDs that match the IMAP query.

        """
        logger.info(f"Fetching mail efficiently with IMAP query {self.imap_query}")
        return connection.search(self.imap_query)

    def __and__(self, other: FilterCriterion) -> FilterCriterion:
        criterion = super().__and__(other)
        if isinstance(other, EfficientCriterion):
            return _make_efficient(criterion)
        else:
            return criterion

    def __or__(self, other: FilterCriterion) -> FilterCriterion:
        criterion = super().__or__(other)
        if isinstance(other, EfficientCriterion):
            return _make_efficient(criterion)
        else:
            return criterion

    def __invert__(self) -> "EfficientCriterion":
        return _make_efficient(super().__invert__())


def _make_efficient(criterion: FilterCriterion) -> EfficientCriterion:
    if criterion.imap_query is None:
        raise ValueError("Cannot make criterion efficient: imap_query is None")
    return EfficientCriterion(criterion.func, criterion.imap_query)


# Efficient criteria: Can be evaluated entirely server-side via IMAP queries
def select_all() -> EfficientCriterion:
    """Create a criterion that matches all messages."""
    return EfficientCriterion(lambda _: True, ["ALL"])


def from_contains(addr: str) -> EfficientCriterion:
    """Create a criterion that matches messages from addresses containing the given string."""
    return EfficientCriterion(lambda msg: any(addr in email for name, email in msg.from_), ["FROM", addr])


def to_contains_contains(addr: str) -> EfficientCriterion:
    """Create a criterion that matches messages to addresses containing the given string."""
    return EfficientCriterion(lambda msg: any(addr in email for name, email in msg.to), ["TO", addr])


def subject_contains(substring: str) -> EfficientCriterion:
    """Create a criterion that matches messages with subject containing the given substring."""
    return EfficientCriterion(lambda msg: substring in (msg.subject or ""), imap_query=["SUBJECT", substring])


def older_than(cutoff_date: date | datetime | str) -> EfficientCriterion:
    """Create a criterion that matches messages older than the given date.

    Args:
        cutoff_date: Date to compare against. Can be a datetime.date, datetime.datetime, or
                     a string in format 'DD-MMM-YYYY' (e.g., '01-Jan-2025').

    Returns:
        An EfficientCriterion that matches messages sent before the cutoff date.

    """
    if isinstance(cutoff_date, str):
        cutoff_date = datetime.strptime(cutoff_date, "%d-%b-%Y").date()
    elif isinstance(cutoff_date, datetime):
        cutoff_date = cutoff_date.date()

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

    return EfficientCriterion(func, imap_query=["SENTBEFORE", imap_date_str])


# Semi-efficient criteria: Use IMAP queries for pre-filtering but require message parsing for exact matching
def from_is(addr: str) -> FilterCriterion:
    """Create a criterion that matches messages from the exact email address."""
    return FilterCriterion(lambda msg: any(addr == email for name, email in msg.from_), imap_query=["FROM", addr])


def to_contains_is(addr: str) -> FilterCriterion:
    """Create a criterion that matches messages to the exact email address."""
    return FilterCriterion(lambda msg: any(addr == email for name, email in msg.to), imap_query=["TO", addr])


def cc_contains_is(addr: str) -> FilterCriterion:
    """Create a criterion that matches messages CC'd to the exact email address."""
    return FilterCriterion(lambda msg: any(addr == email for name, email in msg.cc), imap_query=["CC", addr])


def bcc_contains_is(addr: str) -> FilterCriterion:
    """Create a criterion that matches messages BCC'd to the exact email address."""
    return FilterCriterion(lambda msg: any(addr == email for name, email in msg.bcc), imap_query=["BCC", addr])


def subject_is(subj: str) -> FilterCriterion:
    """Create a criterion that matches messages with the exact subject line."""
    return FilterCriterion(lambda msg: (msg.subject or "") == subj, imap_query=["SUBJECT", subj])


# Non-efficient criteria: Require full message parsing (no IMAP query optimization possible)
def from_matches(pattern: str) -> FilterCriterion:
    """Create a criterion that matches messages from addresses matching the regex pattern."""
    return FilterCriterion(lambda msg: any(_matches(pattern, email) for name, email in msg.from_))


def from_matches_name(pattern: str) -> FilterCriterion:
    """Create a criterion that matches messages from sender names matching the regex pattern."""
    return FilterCriterion(lambda msg: any(_matches(pattern, name) for name, email in msg.from_))


def to_contains_matches(pattern: str, incl_cc: bool = True, incl_bcc: bool = True) -> FilterCriterion:
    """Create a criterion that matches messages to addresses matching the regex pattern.

    Args:
        pattern: Regular expression pattern to match against email addresses.
        incl_cc: Whether to also check CC field (default: True).
        incl_bcc: Whether to also check BCC field (default: True).

    """
    criterion = FilterCriterion(lambda msg: any(_matches(pattern, email) for name, email in msg.to))
    if incl_cc:
        criterion |= cc_contains_matches(pattern)
    if incl_bcc:
        criterion |= bcc_contains_matches(pattern)
    return criterion


def cc_contains_matches(pattern: str) -> FilterCriterion:
    """Create a criterion that matches messages CC'd to addresses matching the regex pattern."""
    return FilterCriterion(lambda msg: any(_matches(pattern, email) for name, email in msg.cc))


def bcc_contains_matches(pattern: str) -> FilterCriterion:
    """Create a criterion that matches messages BCC'd to addresses matching the regex pattern."""
    return FilterCriterion(lambda msg: any(_matches(pattern, email) for name, email in msg.bcc))


def subject_matches(pattern: str) -> FilterCriterion:
    """Create a criterion that matches messages with subject matching the regex pattern."""
    return FilterCriterion(lambda msg: _matches(pattern, msg.subject or ""))


class MailAction:
    """Represents an action to perform on email messages.

    Actions can be combined using the & operator to chain multiple actions.
    """

    def __init__(self, func: Callable[[EMailAccount, list[int]], None], name: str = "<no name>") -> None:
        """Initialize a mail action.

        Args:
            func: Function that performs the action on a list of message IDs.
            name: Human-readable name for the action (default: "<no name>").

        """
        self.func = func
        self.name = name

    def execute(self, account: EMailAccount, msgids: list[int]) -> None:
        """Execute this action on the given message IDs.

        Args:
            account: Email account to perform the action on.
            msgids: List of message IDs to act upon.

        """
        self.func(account, msgids)

    def __and__(self, other: "MailAction") -> "MailAction":
        def newfunc(account: EMailAccount, msg: list[int]) -> None:
            self.func(account, msg)
            other.func(account, msg)

        return MailAction(newfunc, self.name + "; " + other.name)

    def __str__(self) -> str:
        return self.name


def move_to(folder: str) -> MailAction:
    """Create an action that moves messages to the specified folder.

    Args:
        folder: Destination folder name.

    """

    def func(account: EMailAccount, msgids: list[int]) -> None:
        account.connection.move(msgids, folder)

    return MailAction(func, name=f"move to {folder}")


def trash() -> MailAction:
    """Create an action that moves messages to the trash folder."""

    def func(account: EMailAccount, msgids: list[int]) -> None:
        account.connection.move(msgids, account.connection.find_special_folder(imapclient.TRASH))

    return MailAction(func, name="trash")


def mark_as_read() -> MailAction:
    """Create an action that marks messages as read."""

    def func(account: EMailAccount, msgids: list[int]) -> None:
        account.connection.add_flags(msgids, [b"\\Seen"])

    return MailAction(func, name="mark as read")


def mark_as_unread() -> MailAction:
    """Create an action that marks messages as unread."""

    def func(account: EMailAccount, msgids: list[int]) -> None:
        account.connection.remove_flags(msgids, [b"\\Seen"])

    return MailAction(func, name="mark as unread")


class CriterionFilter(OneAccountFilter):
    """Filter that applies an action to messages matching a criterion."""

    def __init__(self, account: EMailAccount, criterion: FilterCriterion, action: MailAction, base_folder: str = "INBOX") -> None:
        """Initialize a criterion filter.

        Args:
            account: Email account to filter.
            criterion: Criterion (condition) to match messages.
            action: Action to perform on matching messages.
            base_folder: Folder to search in (default: "INBOX").

        """
        super().__init__(account)
        self.base_folder = base_folder
        self.criterion = criterion
        self.action = action

    def apply(self, dry_run: bool = False) -> None:
        """Apply the filter to matching messages.

        Args:
            dry_run: If True, log actions without executing them (default: False).

        """
        self.account.connection.select_folder(self.base_folder, readonly=False)
        msgs = self.criterion.filter(self.account.connection)
        if msgs:
            if dry_run:
                logger.info(f"[Dry-Run] Would select: {msgs}")
                logger.info(f"[Dry-Run] Would execute: {self.action}")
            else:
                logger.info(f"Selected: {msgs}")
                self.action.execute(self.account, msgs)
                logger.info(f"Executed: {self.action}")
