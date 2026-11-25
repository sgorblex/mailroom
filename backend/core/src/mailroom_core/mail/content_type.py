"""An attachment's type for the client, from its bytes first (`src/mail/content-type.ts`)."""

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable

DISPLAYED_IMAGES: Final = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
"""Image types clients display inline: a claim to be one must be backed by the bytes."""

_SIGNATURES: Final[tuple[tuple[str, Callable[[bytes], bool]], ...]] = (
    ("application/pdf", lambda head: head[:5] == b"%PDF-"),
    ("image/png", lambda head: head[:8] == b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", lambda head: head[:3] == b"\xff\xd8\xff"),
    ("image/gif", lambda head: head[:6] in (b"GIF87a", b"GIF89a")),
    ("image/webp", lambda head: head[:4] == b"RIFF" and head[8:12] == b"WEBP"),
)
_GENERIC: Final = frozenset(
    {"application/octet-stream", "application/x-download", "binary/octet-stream"}
)
"""Declared types that say nothing about the content."""
_TEXT_EXTENSIONS: Final = {
    "txt": "text/plain",
    "csv": "text/csv",
    "md": "text/markdown",
    "json": "application/json",
}
"""Text types worth recovering from a file name when the declared type is generic."""
_ALIASES: Final = {"image/jpg": "image/jpeg", "application/x-pdf": "application/pdf"}
_UTF_MARKS: Final = (b"\xff\xfe", b"\xfe\xff")
"""How UTF-16 and UTF-32 text starts, which holds NULs a text otherwise never does."""
_PLAIN_TYPE: Final = re.compile(r"[a-z0-9][\w.+-]*/[\w.+-]+", re.ASCII)
_EXTENSION: Final = re.compile(r"\.([a-z0-9]+)\Z", re.ASCII)
_TEXT_TYPES: Final = re.compile(r"text/|application/(?:json|xml|csv)\Z")


def content_type(data: bytes, declared: str, name: str | None = None) -> str:
    """The type to label `data` with. Senders often declare `application/octet-stream`, and the
    declared type is theirs to make up, so the content's own signature wins. A displayed image
    type is only kept when the bytes are that image (other types go on as files, where a wrong
    label does no harm); a file name only recovers text types, since it cannot be checked."""
    for kind, matches in _SIGNATURES:
        if matches(data):
            return kind
    lowered = declared.lower()
    if not _PLAIN_TYPE.fullmatch(lowered) or lowered in DISPLAYED_IMAGES:
        return "application/octet-stream"
    if lowered not in _GENERIC:
        return lowered
    found = _EXTENSION.search((name or "").lower())
    return _TEXT_EXTENSIONS.get(found.group(1) if found else "", "application/octet-stream")


def upload_type(data: bytes, declared: str, name: str | None = None) -> str:
    """`content_type` of a new file, whose declared type may carry parameters (`; charset=`)."""
    return content_type(data, _bare(declared), name)


def named_type(data: bytes, declared: str, name: str) -> str:
    """`content_type` under a name an agent chose, which makes no text of binary bytes."""
    found = content_type(data, declared, name)
    if found != declared.lower() and is_text_type(found) and _is_binary(data):
        return "application/octet-stream"
    return found


def given_type(data: bytes, declared: str | None, name: str) -> str | None:
    """The type of a file an agent gives, None when `data` contradicts the one it declared: bytes
    that show another type, a type known by its bytes that they are not, a binary file called
    text, or no media type at all. Undeclared, it is what the bytes, then the name, say."""
    if declared is None:
        return named_type(data, "application/octet-stream", name)
    kind = _bare(declared)
    kind = _ALIASES.get(kind, kind)
    found = named_type(data, kind, name)
    if kind in _GENERIC:
        return found
    signed = {signed for signed, _matches in _SIGNATURES}
    binary_text = is_text_type(kind) and _is_binary(data)
    if found != kind or binary_text or (kind in signed and not _is_signed(data)):
        return None
    return found


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data and not data.startswith(_UTF_MARKS)


def _is_signed(data: bytes) -> bool:
    return any(matches(data) for _kind, matches in _SIGNATURES)


def _bare(declared: str) -> str:
    return declared.partition(";")[0].strip().lower()


def is_text_type(kind: str) -> bool:
    """Whether an attachment of type `kind` (as `content_type` gives it) is shown as text."""
    return _TEXT_TYPES.match(kind) is not None
