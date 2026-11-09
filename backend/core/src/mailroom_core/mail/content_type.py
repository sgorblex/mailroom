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


def is_text_type(kind: str) -> bool:
    """Whether an attachment of type `kind` (as `content_type` gives it) is shown as text."""
    return _TEXT_TYPES.match(kind) is not None
