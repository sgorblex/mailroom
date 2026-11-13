"""Body-based email filtering criteria."""

from __future__ import annotations

from typing import TYPE_CHECKING

import regex

from mailroom_core.filters.criteria.criterion import Criterion
from mailroom_core.filters.pattern import Pattern
from mailroom_core.filters.query import Q

if TYPE_CHECKING:
    from mailroom_core.filters.message import Message


class BodyContains(Criterion):
    """Matches messages whose body contains the given substring.

    Uses IMAP ``BODY`` for server-side prefiltering (``is_efficient=True``), so
    :meth:`mailroom_core.filters.Folder.run` does not fetch messages solely for
    this criterion; the UID set from the server search is authoritative. The
    client predicate matches the same substring on the decoded body text (the
    plain parts, else the HTML ones' visible text) for :meth:`Criterion.select`.
    """

    spec_type = "body_contains"

    def __init__(self, substring: str) -> None:
        """Initialize a BodyContains criterion.

        Args:
            substring: Substring to search for in the message body.

        """

        def func(msg: Message) -> bool:
            return substring in msg.parsed.text

        super().__init__(func, Q(("BODY", substring)), is_efficient=True)


class BodyMatches(Criterion):
    r"""Matches messages whose body matches the regex pattern somewhere.

    Unlike :class:`SubjectMatches` and address ``*Matches`` criteria, which match
    the whole of a short string, this criterion searches the body with ``DOTALL``
    so patterns can span lines in a typical mail body.

    Requires fetching message data (not expressible as a single IMAP regex);
    ``imap_query`` defaults to all messages unless combined with other criteria.
    """

    spec_type = "body_matches"

    def __init__(self, pattern: str) -> None:
        """Initialize a BodyMatches criterion.

        Args:
            pattern: Regular expression searched anywhere in the decoded body
                (the plain parts, else the HTML ones' visible text), with DOTALL enabled.

        """
        compiled = Pattern(pattern, regex.DOTALL)

        def func(msg: Message) -> bool:
            return compiled.search(msg.parsed.text)

        super().__init__(func)
