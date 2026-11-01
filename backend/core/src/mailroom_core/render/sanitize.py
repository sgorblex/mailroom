"""Text cleaning shared by bodies and headers (`src/render/sanitize.ts`)."""

import re
from dataclasses import dataclass
from typing import Final

from mailroom_core.render._js import WHITESPACE, Utf16Index

# Unicode 16.0, as Node 24's ICU and Python 3.14's unicodedata have it; the tests check the
# table against unicodedata.
_DEFAULT_IGNORABLE: Final = (
    "\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e"
    "\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8"
    "\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff"
)
_FORMAT: Final = (
    "\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e\u200b-\u200f\u202a-\u202e"
    "\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb\U000110bd\U000110cd\U00013430-\U0001343f"
    "\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0001\U000e0020-\U000e007f"
)
_CONTROLS: Final = "\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"

INVISIBLE: Final = re.compile(f"[{_DEFAULT_IGNORABLE}{_FORMAT}{_CONTROLS}]")
"""Every character a reader cannot see but a model still reads: zero-width and bidi controls, the
Unicode tag block used for "ASCII smuggling", variation selectors (Default_Ignorable_Code_Point),
other format characters (Cf: Arabic number signs, interlinear annotations, ...), plus C0/C1
controls other than tab and newline."""

_LINE_BREAKS: Final = re.compile("\r\n?|[\u2028\u2029]")
_SPACE_RUNS: Final = re.compile(f"[{re.escape(WHITESPACE)}]+")


@dataclass(frozen=True, slots=True)
class CleanText:
    text: str
    removed: int
    """How many invisible characters were removed: worth telling the model, it is a phishing
    signal."""


def clean_text(text: str) -> CleanText:
    """Bodies: newlines normalised to `\\n`, invisible characters removed."""
    cleaned, removed = INVISIBLE.subn("", _LINE_BREAKS.sub("\n", text))
    return CleanText(cleaned, removed)


def safe_cut(text: str, offset: int) -> int:
    """Where a cut at UTF-16 `offset` would split a surrogate pair, one unit earlier."""
    return Utf16Index(text).safe_cut(offset)


def clean_header(text: str, max_length: int = 998) -> str:
    """Headers and other single-line values: also no line breaks of any kind (a CRLF in a subject
    must never start a new header or a new line in our output), whitespace collapsed, capped at
    `max_length` UTF-16 units like an RFC 5322 line."""
    flat = _SPACE_RUNS.sub(" ", clean_text(text).text).strip(WHITESPACE)
    index = Utf16Index(flat)
    if index.length <= max_length:
        return flat
    return index.slice(0, index.safe_cut(max_length - 1)) + "…"
