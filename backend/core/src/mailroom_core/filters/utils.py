"""Shared utilities."""

from imap_thingy.core.pattern import Pattern


def matches(pattern: str, string: str) -> bool:
    """Return True if the whole string matches the regex pattern, within the match timeout."""
    return Pattern(pattern).fullmatch(string)
