"""User-supplied regular expressions, matched with a time limit.

Patterns come from rule authors, possibly through a UI, so a pathological one
(catastrophic backtracking) must not hang a run: matching uses the ``regex``
package, whose matches can be interrupted, instead of :mod:`re`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import regex

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_MATCH_TIMEOUT = 1.0
"""Seconds a single match may take before :class:`MatchTimeoutError`."""

_match_timeout: float | None = DEFAULT_MATCH_TIMEOUT


class MatchTimeoutError(TimeoutError):
    """A pattern took longer than the match timeout on one string."""


def set_match_timeout(seconds: float | None) -> None:
    """Set how long one match of a user pattern may take, process-wide.

    Args:
        seconds: The limit in seconds (default :data:`DEFAULT_MATCH_TIMEOUT`),
            or None for no limit.

    """
    if seconds is not None and seconds <= 0:
        raise ValueError(f"match timeout must be positive or None, got {seconds}")
    global _match_timeout  # noqa: PLW0603
    _match_timeout = seconds


def get_match_timeout() -> float | None:
    """Return the current match timeout in seconds (None: no limit)."""
    return _match_timeout


class Pattern:
    """A regular expression compiled once, whose matches obey the match timeout.

    The syntax is that of :mod:`re` (``regex``'s compatible mode).
    """

    def __init__(self, pattern: str, flags: int = 0) -> None:
        """Compile pattern.

        Raises:
            ValueError: pattern is not a valid regular expression.

        """
        try:
            self._compiled = regex.compile(pattern, flags)
        except regex.error as e:
            raise ValueError(f"invalid regular expression {pattern!r}: {e}") from e
        self.pattern = pattern

    def fullmatch(self, string: str) -> bool:
        """Return True if the whole string matches."""
        return self._run(self._compiled.fullmatch, string)

    def search(self, string: str) -> bool:
        """Return True if the pattern matches somewhere in string."""
        return self._run(self._compiled.search, string)

    def _run(self, method: Callable[..., object], string: str) -> bool:
        timeout = _match_timeout
        try:
            return method(string, concurrent=True, timeout=timeout) is not None
        except TimeoutError as e:
            raise MatchTimeoutError(
                f"regular expression {self.pattern!r} timed out after {timeout}s"
            ) from e
