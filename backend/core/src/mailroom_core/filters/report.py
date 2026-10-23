"""Run reports: what a rule run matched and did, as JSON-compatible dicts.

``Folder.run`` returns a :class:`FolderReport`; ``run_rules`` returns one per folder. Every value
is a JSON type (``json.dumps`` works on a report as is), so a caller can store or send it.
"""

from __future__ import annotations

from typing import TypedDict

from imap_thingy.core.spec import SpecNode  # noqa: TC001 -- runtime, for get_type_hints()

MessageSummary = TypedDict(
    "MessageSummary",
    {"uid": int, "from": str | None, "subject": str | None, "date": str | None},
)
"""A matched message: its UID, decoded ``From`` and ``Subject``, and ``Date`` in ISO 8601 UTC."""


class FilterReport(TypedDict):
    """One filter's outcome in a folder run."""

    spec: SpecNode | None
    """The filter's ``to_spec()``, or None when it has no data form."""
    action: str
    """The action's ``repr()``, or its name when the filter has no spec."""
    matched: list[MessageSummary]
    """The messages the criterion selected, in search order."""
    executed: bool
    """True when the action was applied to every matched message (never in a dry run)."""


class FolderReport(TypedDict):
    """One folder's run: its filters' reports in order, and the error that stopped it, if any."""

    account: str
    folder: list[str]
    """The folder's path segments."""
    dry_run: bool
    filters: list[FilterReport]
    """Reports of the filters that ran; on error, those finished before it."""
    error: str | None
    """Set by ``run_rules(parallel=True)`` for a folder whose run raised; None otherwise."""
