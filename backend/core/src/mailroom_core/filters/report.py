"""Run reports: what a rule run matched and did, as JSON-compatible dicts.

``Folder.run`` returns a :class:`FolderReport`; ``run_rules`` returns one per folder. Every value
is a JSON type (``json.dumps`` works on a report as is), so a caller can store or send it.
"""

from __future__ import annotations

from datetime import UTC
from email.header import decode_header, make_header
from typing import TYPE_CHECKING, NotRequired, TypedDict

from mailroom_core.filters.spec.nodes import SpecNode  # noqa: TC001 -- runtime, for get_type_hints

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from imapclient.response_types import Address, Envelope

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
    limited_to_newest: NotRequired[int]
    """Set when a run with ``newest`` looked at the folder's newest messages only: how many."""


def summaries(
    uids: Sequence[int], envelopes: Mapping[int, Envelope | None]
) -> list[MessageSummary]:
    """Each of ``uids``' UID, From, Subject and Date, in ``uids`` order, from its ENVELOPE; a
    message with none keeps its UID with the other fields None."""
    out: list[MessageSummary] = []
    for uid in uids:
        envelope = envelopes.get(uid)
        if envelope is None:
            out.append({"uid": uid, "from": None, "subject": None, "date": None})
            continue
        out.append(
            {
                "uid": uid,
                "from": format_addresses(envelope.from_),
                "subject": decode_text(envelope.subject),
                "date": envelope.date.astimezone(UTC).isoformat() if envelope.date else None,
            }
        )
    return out


def decode_text(value: bytes | str | None) -> str | None:
    """An RFC 2047 header value from an ENVELOPE, decoded; undecodable bytes are replaced."""
    if value is None:
        return None
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    try:
        return str(make_header(decode_header(text)))
    except Exception:
        return text


def format_addresses(addresses: Sequence[Address] | None) -> str | None:
    """ENVELOPE addresses as ``Name <mailbox@host>``, comma-separated; None for none."""
    if not addresses:
        return None
    out: list[str] = []
    for a in addresses:
        mailbox = decode_text(a.mailbox)
        host = decode_text(a.host)
        address = f"{mailbox}@{host}" if mailbox and host else (mailbox or host or "")
        name = decode_text(a.name)
        out.append(f"{name} <{address}>" if name else address)
    return ", ".join(out)
