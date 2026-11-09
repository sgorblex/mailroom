"""HTML to plain text (`src/render/html.ts`).

A port of the part of `html-to-text` 10 that `html.ts` configures (no word wrap, links as
`[text](url)`, no images, tables as blocks, quote containers marked), over lxml. The walk is
iterative and the text is built in chunks, so neither depth nor breadth costs recursion or
quadratic copying.

lxml (libxml2) and htmlparser2 build the same tree for well-formed mail. They differ on misnested
markup (a block inside a `<p>`, stray text in a table, content outside `<body>`), where the text
can differ a little; nothing the TypeScript tests cover depends on it.
"""

import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from typing import Final

import lxml.etree
import lxml.html
from lxml.etree import _Attrib, _Element

from mailroom_core.render._js import (
    WHITESPACE,
    number,
    number_text,
    to_int32,
    to_uint16,
    trim_start,
    utf16_length,
    utf16_prefix,
)
from mailroom_core.render.quotes import CUT_MARK, QUOTE_END, QUOTE_START

MAX_HTML_CHARS: Final = 2_000_000
"""HTML beyond this (in UTF-16 units, as the TypeScript counts) is cut before parsing."""
MAX_DEPTH: Final = 1_000
"""Nesting beyond this is cut, at CUT_MARK."""

_HTML_SPACE: Final = " \t\r\n\f\u200b"
"""html-to-text's whitespace: what separates words."""
_WORD: Final = re.compile(f"[^{_HTML_SPACE}]+")
_LITERAL_PIECE: Final = re.compile(r"\n|[^\n]+")
_JS_BLANK: Final = re.compile(f"[{re.escape(WHITESPACE)}]*")
_CLASS_SEPARATOR: Final = re.compile("[ \t]+")
_LINK_SCHEME: Final = re.compile("(?:https?:|mailto:)", re.IGNORECASE)
_URL_UNSAFE: Final = re.compile(f"[(){re.escape(WHITESPACE)}]")
_BRACKET: Final = re.compile(r"[\[\]]")
_TAG_START: Final = r"<{}[\t\n\f\r /> ]|<{}\Z"

# Quote containers (Gmail, Apple Mail, Thunderbird, Yahoo, Outlook web), each collapsed where it
# stands so answers written between quotes stay; and Outlook desktop separators, after which
# everything is quoted.
_QUOTE_CLASSES: Final = frozenset(
    {"gmail_quote", "gmail_quote_container", "moz-cite-prefix", "yahoo_quoted"}
)
_QUOTE_ID: Final = "OLK_SRC_BODY_SECTION"
_QUOTE_START_IDS: Final = frozenset({"divRplyFwdMsg", "appendonsend"})

_SKIPPED: Final = frozenset({"img", "style", "script", "head"})
_BLOCKS: Final = {
    **dict.fromkeys(
        ("div", "article", "aside", "footer", "form", "header", "main", "nav", "section"), (1, 1)
    ),
    **dict.fromkeys(("p", "table", "tr", "td", "th"), (2, 2)),
    **dict.fromkeys(("h1", "h2", "h3"), (3, 2)),
    **dict.fromkeys(("h4", "h5", "h6"), (2, 2)),
}


class _Inline:
    """html-to-text's InlineTextBuilder without wrapping: lines of words."""

    __slots__ = ("lines", "stashed_space", "words")

    def __init__(self) -> None:
        self.lines: list[list[str]] = []
        self.words: list[str] = []
        self.stashed_space = False

    def push(self, word: str) -> None:
        self.words.append(word)

    def concat(self, word: str) -> None:
        if self.words:
            self.words[-1] += word
        else:
            self.words.append(word)

    def new_line(self, count: int = 1) -> None:
        self.lines.append(self.words)
        self.lines.extend([] for _ in range(count - 1))
        self.words = []

    def is_empty(self) -> bool:
        return not self.lines and not self.words

    def clear(self) -> None:
        self.lines = []
        self.words = []

    def text(self) -> str:
        return "\n".join(" ".join(words) for words in (*self.lines, self.words))


@dataclass(slots=True)
class _Block:
    """A block, list or list item on the builder's stack."""

    leading: int = 1
    pre: bool = False
    chunks: list[str] = field(default_factory=list)
    """`rawText`, in pieces: joining once at the end keeps a long message linear."""
    has_raw: bool = False
    stashed: int = 0
    inline: _Inline = field(default_factory=_Inline)
    prefix: str = ""
    max_prefix: int = 0

    def text(self) -> str:
        raw = "".join(self.chunks)
        return raw if self.inline.is_empty() else raw + self.inline.text()

    def add_text(self, text: str, leading: int, trailing: int) -> None:
        breaks = max(self.stashed, leading)
        if not self.inline.is_empty():
            inline = self.inline.text()
            self.chunks.append(inline)
            self.has_raw = self.has_raw or inline != ""
            self.inline.clear()
        if self.has_raw:
            self.chunks += ["\n" * breaks, text]
        else:
            self.chunks = [text]
            self.leading = breaks
        self.has_raw = self.has_raw or text != ""
        self.stashed = trailing

    def add_raw(self, text: str) -> None:
        self.chunks.append(text)
        self.has_raw = self.has_raw or text != ""


class _Builder:
    """html-to-text's BlockTextBuilder, for the formatters `html.ts` uses."""

    def __init__(self) -> None:
        self.stack = [_Block()]
        self.transforms: list[Callable[[str], str]] = []

    @property
    def top(self) -> _Block:
        return self.stack[-1]

    def _transform(self, word: str) -> str:
        for transform in reversed(self.transforms):
            word = transform(word)
        return word

    def add_inline(self, text: str, *, no_transform: bool = False) -> None:
        block = self.top
        if block.pre:
            block.add_raw(text)
            return
        if text == "" or (block.stashed and all(char in _HTML_SPACE for char in text)):
            return
        if block.stashed:
            block.inline.new_line(block.stashed)
        inline = block.inline
        stashed_space = inline.stashed_space
        any_word = False
        transform = None if no_transform or not self.transforms else self._transform
        for match in _WORD.finditer(text):
            word = match.group() if transform is None else transform(match.group())
            if not any_word and not (stashed_space or text[0] in _HTML_SPACE):
                inline.concat(word)
            else:
                inline.push(word)
            any_word = True
        inline.stashed_space = (stashed_space and not any_word) or text[-1] in _HTML_SPACE
        block.stashed = 0

    def add_literal(self, text: str) -> None:
        block = self.top
        if text == "":
            return
        if block.pre:
            block.add_raw(text)
            return
        if block.stashed:
            block.inline.new_line(block.stashed)
        inline = block.inline
        stashed_space = inline.stashed_space
        any_piece = False
        for match in _LITERAL_PIECE.finditer(text):
            piece = match.group()
            if piece == "\n":
                inline.new_line()
            elif not any_piece and not stashed_space:
                inline.concat(piece)
            else:
                inline.push(piece)
            any_piece = True
        inline.stashed_space = stashed_space and not any_piece
        block.stashed = 0

    def line_break(self) -> None:
        block = self.top
        if block.pre:
            block.add_raw("\n")
        else:
            block.inline.new_line()

    def open_block(self, leading: int, *, pre: bool = False) -> None:
        self.stack.append(_Block(leading=leading, pre=pre or self.top.pre))

    def close_block(self, trailing: int, transform: Callable[[str], str] | None = None) -> None:
        block = self.stack.pop()
        text = block.text()
        self.top.add_text(
            transform(text) if transform else text, block.leading, max(block.stashed, trailing)
        )

    def open_list(self, leading: int, max_prefix: int) -> None:
        self.stack.append(_Block(leading=leading, pre=self.top.pre, max_prefix=max_prefix))

    def open_item(self, prefix: str) -> None:
        self.stack.append(_Block(leading=1, pre=self.top.pre, prefix=prefix))

    def close_item(self) -> None:
        item = self.stack.pop()
        width = max(len(item.prefix), self.top.max_prefix)
        text = item.prefix.ljust(width) + item.text().replace("\n", "\n" + " " * width)
        self.top.add_text(text, item.leading, max(item.stashed, 1))

    def close_list(self, trailing: int) -> None:
        block = self.stack.pop()
        text = block.text()
        if text:
            self.top.add_text(text, block.leading, trailing)

    def mark(self, text: str) -> None:
        self.open_block(1)
        self.add_literal(text)
        self.close_block(1)

    def result(self) -> str:
        return self.stack[0].text()


type _Node = _Element | str
"""An element (comments and processing instructions included) or a text node."""
type _Frame = tuple[Iterator[_Node], int] | Callable[[], None]


def _children(element: _Element) -> Iterator[_Node]:
    if element.text:
        yield element.text
    for child in element:
        yield child
        if child.tail:
            yield child.tail


def _tag(node: _Node) -> str | None:
    if isinstance(node, str):
        return None
    return node.tag if isinstance(node.tag, str) else None


def _quote_kind(tag: str, attrs: _Attrib) -> str | None:
    """`quote`, `start` or nothing, by the selectors' precedence (an id beats a class)."""
    element_id = attrs.get("id")
    if element_id in _QUOTE_START_IDS:
        return "start"
    classes = _CLASS_SEPARATOR.split(attrs.get("class") or "")
    if (
        element_id == _QUOTE_ID
        or not _QUOTE_CLASSES.isdisjoint(classes)
        or (tag == "blockquote" and attrs.get("type") == "cite")
    ):
        return "quote"
    return None


def _letters(value: float, base_char: str) -> str:
    """html-to-text's numberToLetterSequence, JavaScript arithmetic included."""
    digits: list[float] = []
    while True:
        value -= 1
        digits.append(math.fmod(value, 26) if math.isfinite(value) else math.nan)
        value = to_int32(value / 26) if math.isfinite(value) else 0
        if not value > 0:
            break
    return "".join(chr(to_uint16(ord(base_char) + digit)) for digit in reversed(digits))


_ROMAN_ONES: Final = ("I", "X", "C", "M")
_ROMAN_FIVES: Final = ("V", "L", "D")


def _roman(value: float) -> str:
    """html-to-text's numberToRoman. Where it would throw (5 digits or more), the number."""

    def at(table: tuple[str, ...], i: int) -> str:
        return table[i] if i < len(table) else "undefined"

    out: list[str] = []
    for i, char in enumerate(reversed(number_text(value))):
        digit = int(char) if char in "0123456789" else None
        if digit is not None and digit % 5 < 4:
            if i >= len(_ROMAN_ONES):
                return number_text(value)
            out.append(("" if digit < 5 else at(_ROMAN_FIVES, i)) + _ROMAN_ONES[i] * (digit % 5))
        else:
            five = digit is not None and digit < 5
            after = at(_ROMAN_FIVES, i) if five else at(_ROMAN_ONES, i + 1)
            out.append(at(_ROMAN_ONES, i) + after)
    return "".join(reversed(out))


def _list_index(kind: str | None) -> Callable[[float], str]:
    match kind:
        case "a" | "A":
            return lambda value: _letters(value, kind)
        case "i":
            return lambda value: _roman(value).lower()
        case "I":
            return _roman
        case _:
            return number_text


def _url(href: str) -> str:
    """Escaped so the URL cannot end the markdown link early."""
    return _URL_UNSAFE.sub(lambda match: {"(": "%28", ")": "%29"}.get(match.group(), "%20"), href)


def _escape_brackets(word: str) -> str:
    """Brackets in link text escaped, so the text cannot close the link and show a fake
    target."""
    return _BRACKET.sub(lambda match: "\\" + match.group(), word)


def _quote_lines(text: str) -> str:
    return "\n".join("> " + line for line in text.strip("\n").split("\n"))


class _Converter:
    def __init__(self, mark_quotes: bool) -> None:
        self.builder = _Builder()
        self.mark_quotes = mark_quotes
        self.stack: list[_Frame] = []

    def run(self, nodes: list[_Node], level: int) -> str:
        self.stack.append((iter(nodes), level))
        while self.stack:
            frame = self.stack[-1]
            if callable(frame):
                self.stack.pop()
                frame()
                continue
            nodes_left, node_level = frame
            node = next(nodes_left, None)
            if node is None:
                self.stack.pop()
            elif isinstance(node, str):
                self.builder.add_inline(node)
            else:
                self._element(node, node_level)
        return self.builder.result()

    def _walk(self, nodes: Iterator[_Node], level: int, after: Callable[[], None] | None) -> None:
        """html-to-text's depth-limited walk: past MAX_DEPTH, CUT_MARK instead of the nodes."""
        if after is not None:
            self.stack.append(after)
        if level > MAX_DEPTH:
            self.builder.add_inline(CUT_MARK)
        else:
            self.stack.append((nodes, level))

    def _element(self, element: _Element, level: int) -> None:
        tag = _tag(element)
        if tag is None:
            return
        builder = self.builder
        attrs = element.attrib
        kind = _quote_kind(tag, attrs) if self.mark_quotes else None
        children = _children(element)
        if kind == "quote":
            builder.mark(QUOTE_START)
            self._walk(children, level + 1, lambda: builder.mark(QUOTE_END))
        elif kind == "start":
            builder.mark(QUOTE_START)
            self._walk(children, level + 1, None)
        elif tag in _SKIPPED:
            pass
        elif tag == "a":
            self._link(element, level)
        elif tag == "br":
            builder.line_break()
        elif tag == "hr":
            builder.open_block(2)
            builder.add_inline("-" * 40)
            builder.close_block(2)
        elif tag in _BLOCKS:
            leading, trailing = _BLOCKS[tag]
            builder.open_block(leading)
            self._walk(children, level + 1, lambda: builder.close_block(trailing))
        elif tag == "pre":
            builder.open_block(2, pre=True)
            self._walk(children, level + 1, lambda: builder.close_block(2))
        elif tag == "blockquote":
            builder.open_block(2)
            self._walk(children, level + 1, lambda: builder.close_block(2, _quote_lines))
        elif tag in ("ul", "ol"):
            self._list(element, tag, level)
        else:
            self._walk(children, level + 1, None)

    def _link(self, element: _Element, level: int) -> None:
        """`[text](url)` for web and mail links; the text alone for anything else
        (javascript:, cid:, #)."""
        builder = self.builder
        href = element.attrib.get("href") or ""
        if not _LINK_SCHEME.match(href):
            self._walk(_children(element), level + 1, None)
            return
        builder.add_inline("[", no_transform=True)
        builder.transforms.append(_escape_brackets)

        def end() -> None:
            builder.transforms.pop()
            builder.add_inline(f"]({_url(href)})", no_transform=True)

        self._walk(_children(element), level + 1, end)

    def _list(self, element: _Element, tag: str, level: int) -> None:
        builder = self.builder
        parent = element.getparent()
        nested = parent is not None and _tag(parent) == "li"
        index = number(element.attrib.get("start") or "1") if tag == "ol" else 0.0
        label = _list_index(element.attrib.get("type"))
        items: list[tuple[_Node, str]] = []
        for child in _children(element):
            if isinstance(child, str) and _JS_BLANK.fullmatch(child):
                continue
            if _tag(child) != "li":
                items.append((child, ""))
                continue
            prefix = " * " if tag == "ul" else f" {label(index)}. "
            index += 1
            items.append((child, trim_start(prefix) if nested else prefix))
        if not items:
            return
        builder.open_list(1 if nested else 2, max(len(prefix) for _, prefix in items))
        self.stack.append(lambda: builder.close_list(1 if nested else 2))
        # Pushed in reverse, so each item runs as open, walk, close.
        for node, prefix in reversed(items):
            self.stack.append(builder.close_item)
            self.stack.append(partial(self._walk, iter([node]), level + 1, None))
            self.stack.append(partial(builder.open_item, prefix))


@dataclass(frozen=True, slots=True)
class HtmlText:
    text: str
    cut: bool
    """Whether anything was dropped: past the size limit, or nested too deeply (at CUT_MARK)."""


def _starts_tag(html: str, name: str) -> bool:
    return re.search(_TAG_START.format(name, name), html, re.IGNORECASE) is not None


def html_to_text(html: str, mark_quotes: bool) -> HtmlText:
    """HTML to plain text: links as `[text](url)`, no images (so no tracking pixels), no styles
    or scripts. With `mark_quotes`, quote containers are marked for `collapse_quotes`. Content
    past the size or depth limits is dropped, at CUT_MARK for depth."""
    too_long = utf16_length(html) > MAX_HTML_CHARS
    if too_long:
        html = utf16_prefix(html, MAX_HTML_CHARS)
    parser = lxml.html.HTMLParser(encoding="utf-8", huge_tree=True, no_network=True)
    try:
        document = lxml.html.document_fromstring(html.encode("utf-8", "replace"), parser=parser)
    except lxml.etree.ParserError:
        return HtmlText("", too_long)
    deep = any("Excessive depth" in error.message for error in parser.error_log)
    body = document.find("body")
    converter = _Converter(mark_quotes)
    # htmlparser2 builds no element the markup does not name: html-to-text walks `<body>` as a
    # node if there is one, else the top-level nodes, which sit one level deeper under `<html>`.
    if body is None:
        text = converter.run(list(_children(document)), 0)
    elif _starts_tag(html, "body"):
        text = converter.run([body], 0)
    else:
        # Without `<head>` in the markup, a `<title>` lxml moved there stays text for htmlparser2.
        head = document.find("head")
        nodes = [] if head is None or _starts_tag(html, "head") else list(_children(head))
        nodes += _children(body)
        text = converter.run(nodes, 1 if _starts_tag(html, "html") else 0)
    return HtmlText(text, too_long or deep or CUT_MARK in text)
