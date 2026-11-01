"""The Python render functions against the TypeScript's own outputs, byte for byte.

`fixtures/render-typescript-outputs.json` holds what `src/render` (html-to-text, the reply parser,
the sanitiser, the formatters) returned for each input there, generated under `TZ=UTC` with Node
24. The large inputs are rebuilt here instead of stored.
"""

import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from mailroom_core.render import (
    CUT_MARK,
    QUOTE_END,
    QUOTE_START,
    RenderedBody,
    clean_header,
    clean_text,
    format_date,
    format_size,
    html_to_text,
    render_body,
)
from mailroom_core.render.sanitize import INVISIBLE

OUTPUTS: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "render-typescript-outputs.json").read_text("utf-8")
)


def _nested(
    depth: int, tag: str = "div", *, before: str = "<p>top</p>", inner: str = "deep"
) -> str:
    return f"{before}{f'<{tag}>' * depth}{inner}{f'</{tag}>' * depth}"


GENERATED = {
    "spike_nested500": _nested(500),
    "spike_nested999": _nested(999),
    "spike_nested1000": _nested(1000),
    "spike_nested5000": _nested(5000),
    "spike_spans5000": _nested(5000, "span"),
    "spike_wide": f"<div>{'<br>'.join(f'l{i}' for i in range(6000))}</div>",
    "explicitbody999": f"<body>{_nested(999)}</body>",
    "explicitbody1000": f"<body>{_nested(1000)}</body>",
    "html999": f"<html>{_nested(999)}</html>",
    "html1000": f"<html>{_nested(1000)}</html>",
    "list1000": _nested(999, before="", inner="<ul><li>item</li></ul>"),
    "list1001": _nested(1000, before="", inner="<ul><li>item</li></ul>"),
    "quoteDeep": f'<div class="gmail_quote">{_nested(1200, before="", inner="x")}</div>after',
}

# Where lxml and htmlparser2 build different trees from misnested markup (html.py says so): a
# block closing a <b> that holds it, an <a> inside an <a>; and libxml2 turning CRLF into LF
# inside <pre>, which only the unrendered text shows (render_body normalises it anyway).
DIFFERENT_TREES = {"spike_misnest3", "spike_misnest4", "links"}
RAW_TEXT_DIFFERS = {"spike_misnest1", "crlf"}


def _marks(text: str) -> str:
    return text.replace(QUOTE_START, "<Q>").replace(QUOTE_END, "<E>").replace(CUT_MARK, "<CUT>")


def _as_ts(rendered: RenderedBody) -> dict[str, Any]:
    out: dict[str, Any] = {
        "text": rendered.text,
        "removedChars": rendered.removed_chars,
        "totalChars": rendered.total_chars,
        "incomplete": rendered.incomplete,
    }
    if rendered.next_offset is not None:
        out["nextOffset"] = rendered.next_offset
    return out


def _body(text: str, format: str, include_quoted: bool) -> dict[str, Any]:  # noqa: A002
    return _as_ts(
        render_body(text, format, include_quoted=include_quoted, offset=0, max_chars=6000)
    )


@pytest.mark.parametrize("name", sorted(OUTPUTS["html"]))
def test_html_bodies(name: str):
    if name in DIFFERENT_TREES:
        pytest.skip("misnested markup: lxml's tree differs from htmlparser2's")
    expected = OUTPUTS["html"][name]
    html = GENERATED.get(name) or expected["input"]
    assert _body(html, "html", False) == expected["body"]
    assert _body(html, "html", True) == expected["bodyQ"]
    assert html_to_text(html, False).cut == expected["cut"]
    if name not in RAW_TEXT_DIFFERS and expected["full"] is not None:
        assert _marks(html_to_text(html, True).text) == expected["marked"]
        assert _marks(html_to_text(html, False).text) == expected["full"]


@pytest.mark.parametrize("name", sorted(OUTPUTS["text"]))
def test_text_bodies(name: str):
    expected = OUTPUTS["text"][name]
    assert _body(expected["input"], "text", False) == expected["body"]
    assert _body(expected["input"], "text", True) == expected["bodyQ"]


def test_headers_and_cleaning():
    for case in OUTPUTS["headers"]:
        text = case["input"]
        assert clean_header(text) == case["clean"]
        assert clean_header(text, 10) == case["clean10"]
        cleaned = clean_text(text)
        assert {"text": cleaned.text, "removed": cleaned.removed} == case["text"]


def test_dates_and_sizes():
    for text, expected in OUTPUTS["dates"].items():
        assert format_date(text, "UTC") == expected["utc"], text
        assert format_date(text, "Europe/Rome") == expected["rome"], text
        assert format_date(text, "America/New_York") == expected["ny"], text
    for size, expected in OUTPUTS["sizes"]:
        assert format_size(size) == expected


def test_invisible_covers_every_format_character():
    """The hard-coded Cf table is Unicode 16's, the version both runtimes use."""
    assert unicodedata.unidata_version == "16.0.0"
    for code in range(0x110000):
        char = chr(code)
        if unicodedata.category(char) == "Cf":
            assert INVISIBLE.fullmatch(char), f"U+{code:04X}"
