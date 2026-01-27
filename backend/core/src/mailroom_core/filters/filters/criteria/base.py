"""Base classes for email filtering criteria."""

import logging
from collections.abc import Callable
from typing import Any

import mailparser
from imapclient import IMAPClient

logger = logging.getLogger("imap-thingy")

ParsedMail = Any


def _get_mail(client: IMAPClient, imap_query: list[str | list[Any]]) -> list[tuple[int, ParsedMail]]:
    """Fetch and parse mail messages from IMAP server.

    Args:
        client: IMAP connection to use.
        imap_query: IMAP query to search for messages.

    Returns:
        List of tuples (message_id, parsed_message).
        The parsed message objects have an _imap_flags attribute containing the message flags.

    """
    logger.info(f"Fetching mail with IMAP query {imap_query}")

    msg_ids = client.search(imap_query)
    fetched = client.fetch(msg_ids, ["BODY.PEEK[]", "FLAGS"])

    messages: list[tuple[int, ParsedMail]] = []
    for msgid, data in fetched.items():
        body_data = data.get(b"BODY[]")
        if not body_data:
            logger.warning(f"Message {msgid} has no or empty body data, skipping")
            continue

        try:
            msg = mailparser.parse_from_bytes(body_data)
            flags = data.get(b"FLAGS", [])
            msg._imap_flags = flags
            messages.append((msgid, msg))
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            logger.warning(f"Failed to parse message {msgid}: {e}, skipping")
            continue

    return messages


class Criterion:
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

    def __and__(self, other: "Criterion") -> "Criterion":
        """Combine two criteria with AND logic."""

        def func(msg: ParsedMail) -> bool:
            return self.func(msg) and other.func(msg)

        imap_query: list[str | list[Any]] | None
        if self.imap_query and other.imap_query:
            imap_query = self.imap_query + other.imap_query
        else:
            imap_query = self.imap_query if self.imap_query else other.imap_query
        return Criterion(func, imap_query)

    def __or__(self, other: "Criterion") -> "Criterion":
        """Combine two criteria with OR logic."""

        def func(msg: ParsedMail) -> bool:
            return self.func(msg) or other.func(msg)

        imap_query: list[str | list[Any]] | None = None
        if self.imap_query and other.imap_query:
            imap_query = ["OR", self.imap_query, other.imap_query]
        return Criterion(func, imap_query)

    def __invert__(self) -> "Criterion":
        """Negate this criterion."""

        def func(msg: ParsedMail) -> bool:
            return not self.func(msg)

        imap_query: list[str | list[Any]] | None = ["NOT", self.imap_query] if self.imap_query else None
        return Criterion(func, imap_query)


class EfficientCriterion(Criterion):
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

    def __and__(self, other: Criterion) -> Criterion:
        """Combine with another criterion, preserving efficiency when possible."""
        criterion = super().__and__(other)
        if isinstance(other, EfficientCriterion):
            return _make_efficient(criterion)
        return criterion

    def __or__(self, other: Criterion) -> Criterion:
        """Combine with another criterion, preserving efficiency when possible."""
        criterion = super().__or__(other)
        if isinstance(other, EfficientCriterion):
            return _make_efficient(criterion)
        return criterion

    def __invert__(self) -> "EfficientCriterion":
        """Negate this criterion, preserving efficiency."""
        return _make_efficient(super().__invert__())


def _make_efficient(criterion: Criterion) -> EfficientCriterion:
    """Convert a criterion to an efficient one if it has an IMAP query."""
    if criterion.imap_query is None:
        raise ValueError("Cannot make criterion efficient: imap_query is None")
    return EfficientCriterion(criterion.func, criterion.imap_query)


class SelectAll(EfficientCriterion):
    """Matches all messages."""

    def __init__(self) -> None:
        """Initialize a SelectAll criterion."""
        super().__init__(lambda _: True, ["ALL"])
