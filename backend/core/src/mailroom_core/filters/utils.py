"""Shared utilities."""

import re


def matches(pattern: str, string: str) -> bool:
    """Return True if the whole string matches the regex pattern."""
    return bool(re.fullmatch(pattern, string))
