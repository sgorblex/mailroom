"""Quoted history and signatures collapsed into marker lines (`src/render/quotes.ts`)."""

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from mailroom_core.render import _reply_parser
from mailroom_core.render._js import WHITESPACE, js_regex, trim_end

_NONCE: Final = secrets.token_urlsafe(9)
QUOTE_START: Final = f"\ue000q{_NONCE}\ue000"
"""Marks the HTML stage leaves for `collapse_quotes` and `render_body`. Random per process and
never shown, so an email cannot contain one to hide its own text behind a quote marker."""
QUOTE_END: Final = f"\ue000e{_NONCE}\ue000"
CUT_MARK: Final = f"\ue000c{_NONCE}\ue000"
"""Where the HTML converter dropped content nested too deeply."""

_MARKS: Final = re.compile("\ue000[qe][A-Za-z0-9_-]+\ue000")
_PIECES: Final = re.compile("(\ue000[qe][A-Za-z0-9_-]+\ue000)")

_HEADER_LINE: Final = js_regex(
    r"^\s*\*?(From|Sent|To|Cc|Subject|Date|Da|Inviato|A|Oggetto|Data)\*?\s*:", "i"
)
"""Outlook's reply header once rendered as text, in English and Italian."""
_FROM_LINE: Final = js_regex(r"^\s*\*?(From|Da)\*?\s*:", "i")
_SENT_LINE: Final = js_regex(r"^\s*\*?(Sent|Inviato)\*?\s*:", "i")
_FORWARD: Final = js_regex(
    r"forwarded message|messaggio inoltrato|^\s*\*?(Subject|Oggetto)\*?\s*:\s*(Fwd?|I)\s*:", "im"
)
"""A forward is the point of its message: its content is never collapsed."""

type Omitted = Literal["quoted text", "signature"]
type Marker = Callable[[Omitted, int], str]
"""The line put in place of what was hidden, from what it was and how many lines of text."""

MARKER_LINE: Final = js_regex(
    r"^\[(quoted text|signature): \d+ lines? omitted, read with include_quoted: true\]$"
)
"""A line `default_marker` wrote."""


def default_marker(what: Omitted, lines: int) -> str:
    """The TypeScript's marker, which names its `read` tool's `include_quoted` parameter."""
    plural = "" if lines == 1 else "s"
    return f"[{what}: {lines} line{plural} omitted, read with include_quoted: true]"


@dataclass(frozen=True, slots=True)
class Collapsed:
    text: str
    quoted_lines: int
    """Lines of quoted text hidden (blank lines not counted); 0 if none."""
    signature_lines: int
    """Lines of a signature hidden, when that was all the end of the text held."""


def _without_marks(text: str) -> str:
    return _MARKS.sub("", text)


def _non_blank(text: str) -> list[str]:
    return [line for line in _without_marks(text).split("\n") if line.strip(WHITESPACE) != ""]


def _line_count(text: str) -> int:
    """Lines of actual text: blank lines and our own marks do not count."""
    return len(_non_blank(text))


def _is_forward(text: str) -> bool:
    return _FORWARD.search("\n".join(_non_blank(text)[:12])) is not None


def _collapse_spans(text: str, marker: Marker | None) -> tuple[str, int]:
    """Each marked quote span (outermost only; a start mark never closed runs to the end)
    replaced by its marker where it stands, so answers between quotes stay; and how many lines
    of text that hid."""
    out: list[str] = []
    span: list[str] = []
    depth = 0
    hidden = 0

    def close() -> None:
        nonlocal hidden
        quoted = "".join(span)
        if _is_forward(quoted):
            out.append(_without_marks(quoted))
        else:
            lines = _line_count(quoted)
            hidden += lines
            out.append("\n" if marker is None else f"\n{marker('quoted text', lines)}\n")
        span.clear()

    for piece in _PIECES.split(text):
        if piece == QUOTE_START:
            depth += 1
        elif piece == QUOTE_END and depth > 0:
            depth -= 1
            if depth == 0:
                close()
        elif depth > 0:
            span.append(piece)
        else:
            out.append(piece)
    if depth > 0:
        close()
    return "".join(out), hidden


def _outlook_header_start(text: str, lines: list[str]) -> int | None:
    """Where an Outlook-style reply header block starts (index of its first line), if there is
    one: the first line from which at least three header lines run, a From and a Sent among
    them, that is not a forward's header. Linear: each line is matched once, and a forward is
    looked for in the block's first lines only, all that `_is_forward` reads."""
    count = len(lines)
    header_line = _HEADER_LINE.for_text(text).search
    from_line = _FROM_LINE.for_text(text).search
    sent_line = _SENT_LINE.for_text(text).search
    run_end = [count] * (count + 1)
    next_from = [count] * (count + 1)
    next_sent = [count] * (count + 1)
    for index in range(count - 1, -1, -1):
        line = lines[index]
        if header_line(line) is None:
            run_end[index] = index
            next_from[index] = next_from[index + 1]
            next_sent[index] = next_sent[index + 1]
            continue
        # From and Sent lines are header lines.
        run_end[index] = run_end[index + 1]
        next_from[index] = index if from_line(line) else next_from[index + 1]
        next_sent[index] = index if sent_line(line) else next_sent[index + 1]
    for start in range(1, count - 2):
        end = run_end[start]
        if (
            end - start >= 3
            and next_from[start] < end
            and next_sent[start] < end
            and not _is_forward("\n".join(lines[max(0, start - 2) : min(end, start + 12)]))
        ):
            return start
    return None


def collapse_quotes(text: str, *, marker: Marker | None = default_marker) -> Collapsed:
    """Collapses quoted history and the signature into marker lines: marked HTML quotes where
    they stand, then a trailing Outlook header block or the reply parser's hidden fragments into
    one marker at the end. Detection is heuristic, so the marker should say how to get the full
    text back. Forwarded content and quotes interleaved with the reply stay.

    `marker` writes the marker lines (the TypeScript's by default); None leaves them out, for a
    host that shows what was collapsed its own way, from the counts returned."""
    text, quoted = _collapse_spans(text, marker)
    omitted = 0

    lines = text.split("\n")
    header = _outlook_header_start(text, lines)
    if header is not None:
        omitted += _line_count("\n".join(lines[header:]))
        text = "\n".join(lines[:header])

    fragments = _reply_parser.parse(text)
    hidden = [fragment for fragment in fragments if fragment.hidden]
    if hidden:
        omitted += sum(_line_count(fragment.content) for fragment in hidden)
        text = "\n".join(fragment.content for fragment in fragments if not fragment.hidden)

    if omitted == 0:
        return Collapsed(text, quoted, 0)
    signature_only = header is None and all(fragment.signature for fragment in hidden)
    what: Omitted = "signature" if signature_only else "quoted text"
    kept = trim_end(text)
    result = kept if marker is None else f"{kept}\n\n{marker(what, omitted)}"
    if signature_only:
        return Collapsed(result, quoted, omitted)
    return Collapsed(result, quoted + omitted, 0)
