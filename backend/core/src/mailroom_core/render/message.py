"""List lines and the header block the model reads (`src/render/message.ts`)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from mailroom_core.render.sanitize import clean_header

if TYPE_CHECKING:
    from collections.abc import Sequence
    from collections.abc import Set as AbstractSet


@dataclass(frozen=True, slots=True)
class Address:
    """One mailbox of an address header, as parsed (imapflow's `MessageAddressObject`)."""

    name: str | None = None
    address: str | None = None


@dataclass(frozen=True, slots=True)
class PartInfo:
    """A MIME part worth naming: an attachment, the body part, a calendar invite."""

    part: str
    """The IMAP part number, `1.2`."""
    type: str
    size: int
    """Bytes once decoded: `mailroom_core.mail` estimates it from the encoded size."""
    name: str | None = None


type DateValue = datetime | str | None
"""A date as the server or a header gives it; a naive datetime is taken as UTC."""


def _parse_date(value: DateValue) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    text = value.strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text)
    except TypeError, ValueError, IndexError:
        return None


def format_date(value: DateValue, time_zone: str) -> str:
    """`2026-10-03 14:12` in the configured time zone; every date the model sees uses this
    form."""
    date = _parse_date(value)
    if date is None:
        return "unknown date"
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    try:
        local = date.astimezone(ZoneInfo(time_zone))
    except OverflowError, ValueError:
        return "unknown date"
    return local.strftime("%Y-%m-%d %H:%M")


def _format_address(address: Address) -> str:
    name = clean_header(address.name or "", 120)
    addr = clean_header(address.address or "", 254)
    if addr == "":
        return name or "(unknown)"
    return addr if name in ("", addr) else f"{name} <{addr}>"


def _format_addresses(addresses: Sequence[Address] | None) -> str:
    return ", ".join(_format_address(address) for address in addresses or ())


def format_size(size: int) -> str:
    """A size in bytes as `B`, `KB` or `MB`, as the TypeScript writes it."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{_round_half_up(size / 1024)} KB"
    return f"{_to_fixed_1(size / (1024 * 1024))} MB"


def _round_half_up(value: float) -> int:
    """`Math.round`: halves go up, unlike Python's `round`."""
    whole = int(value)
    return whole + 1 if value - whole >= 0.5 else whole


def _to_fixed_1(value: float) -> str:
    """`value.toFixed(1)`, which rounds the exact binary value half up."""
    return str(Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _format_flags(flags: AbstractSet[str] | None, has_attachments: bool) -> str:
    """Unread, flagged, answered, draft, has attachments: the flags worth a glance in a list."""

    def has(flag: str) -> bool:
        return flags is not None and flag in flags

    shown = [
        "unread" if not has("\\Seen") else "",
        "flagged" if has("\\Flagged") else "",
        "answered" if has("\\Answered") else "",
        "draft" if has("\\Draft") else "",
        "attachments" if has_attachments else "",
    ]
    return ",".join(flag for flag in shown if flag)


@dataclass(frozen=True, slots=True)
class ListItem:
    """One message as `list_line` shows it."""

    ref: str
    date: DateValue
    sender: Sequence[Address] | None
    """The From header (`from` in the TypeScript)."""
    subject: str | None
    flags: AbstractSet[str] | None
    has_attachments: bool
    preview: str | None = None


def list_line(item: ListItem, time_zone: str) -> str:
    """One line per message: `ref · date · from · subject · flags · preview`."""
    subject = clean_header(item.subject or "", 120) or "(no subject)"
    preview = "" if item.preview is None else clean_header(item.preview, 100)
    fields = [
        item.ref,
        format_date(item.date, time_zone),
        _format_addresses(item.sender) or "(unknown sender)",
        subject,
        _format_flags(item.flags, item.has_attachments),
        preview,
    ]
    return " · ".join(value for value in fields if value)


@dataclass(frozen=True, slots=True)
class HeaderView:
    """The headers, attachments and calendar part of one message, as `header_block` shows them."""

    sender: Sequence[Address] | None
    """The From header (`from` in the TypeScript)."""
    to: Sequence[Address] | None
    cc: Sequence[Address] | None
    date: DateValue
    subject: str | None
    attachments: Sequence[PartInfo] = field(default_factory=tuple)
    calendar: PartInfo | None = None


def _part_line(info: PartInfo) -> str:
    name = "(unnamed)" if info.name is None else clean_header(info.name, 200)
    return f"{info.part}: {name} ({clean_header(info.type, 100)}, {format_size(info.size)})"


def header_block(view: HeaderView, time_zone: str) -> str:
    """The headers `read` shows; everything else only with `raw_headers`."""
    lines = [
        f"From: {_format_addresses(view.sender) or '(unknown)'}",
        f"To: {_format_addresses(view.to) or '(none)'}",
        *([f"Cc: {_format_addresses(view.cc)}"] if view.cc else []),
        f"Date: {format_date(view.date, time_zone)}",
        f"Subject: {clean_header(view.subject or '') or '(no subject)'}",
    ]
    lines.extend(f"Attachment {_part_line(attachment)}" for attachment in view.attachments)
    if view.calendar is not None:
        lines.append(
            f"Calendar invite {_part_line(view.calendar)}; invite parsing comes in a later version."
        )
    return "\n".join(lines)
