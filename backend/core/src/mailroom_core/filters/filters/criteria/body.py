"""Body-based email filtering criteria."""

from __future__ import annotations

import re

from mailparser.core import MailParser

from imap_thingy.core import Message, Q
from imap_thingy.filters.criteria.criterion import Criterion


def _parts_to_str(value: object) -> str:
    """Join mail-parser text parts (list of str) into one string; accept str or empty."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(x) for x in value if x)
    return str(value)


def _body_text(parsed: MailParser) -> str:
    """Return decoded body text for matching: plain parts first, else HTML parts.

    mail-parser exposes ``text_plain`` and ``text_html`` as lists of decoded
    parts; this matches the common case of preferring plain text and falling
    back to HTML when there is no plain body.
    """
    plain = _parts_to_str(parsed.text_plain)
    if plain.strip():
        return plain
    return _parts_to_str(parsed.text_html)


class BodyContains(Criterion):
    """Matches messages whose body contains the given substring.

    Uses IMAP ``BODY`` for server-side prefiltering (``is_efficient=True``), so
    :meth:`imap_thingy.accounts.Folder.run` does not fetch messages solely for
    this criterion; the UID set from the server search is authoritative. The
    client predicate matches the same substring on normalized decoded body text
    (plain parts, else HTML) for :meth:`Criterion.select` and consistency with
    parsed mail.
    """

    def __init__(self, substring: str) -> None:
        """Initialize a BodyContains criterion.

        Args:
            substring: Substring to search for in the message body.

        """

        def func(msg: Message) -> bool:
            return substring in _body_text(msg.parsed)

        super().__init__(func, Q(("BODY", substring)), is_efficient=True)


class BodyMatches(Criterion):
    r"""Matches messages whose body matches the regex pattern somewhere.

    Unlike :class:`SubjectMatches` and address ``*Matches`` criteria, which use
    :func:`~imap_thingy.utils.matches` (``re.fullmatch`` on a short string),
    this criterion uses :func:`re.search` with :data:`re.DOTALL` so patterns can
    span lines in a typical mail body.

    Requires fetching message data (not expressible as a single IMAP regex);
    ``imap_query`` defaults to all messages unless combined with other criteria.
    """

    def __init__(self, pattern: str) -> None:
        """Initialize a BodyMatches criterion.

        Args:
            pattern: Regular expression searched anywhere in the normalized body
                (plain parts, else HTML), with DOTALL enabled.

        """

        def func(msg: Message) -> bool:
            return bool(re.search(pattern, _body_text(msg.parsed), flags=re.DOTALL))

        super().__init__(func)
