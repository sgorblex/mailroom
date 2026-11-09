"""The pure parts of `mailroom_core.mail`: refs and cursors against the TypeScript's own strings
(the `message refs` cases of `src/render/render.test.ts`), `analyseStructure`'s cases over
imapclient's BODYSTRUCTURE, `src/mail/content-type.test.ts`, and the decoding imapflow did."""

import base64
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from imapclient.response_types import BodyData

from mailroom_core.mail import (
    MailboxContext,
    MailConnections,
    MailError,
    MessageRef,
    Position,
    SearchQuery,
    Source,
    analyse_structure,
    check_fresh,
    content_type,
    decode_cursor,
    encode_cursor,
    format_ref,
    parse_ref,
    preview_of,
    read_page,
    search_messages,
)
from mailroom_core.mail._mime import TransferDecoder, decode_words, parameters, unflow
from mailroom_core.render import PartInfo

if TYPE_CHECKING:
    from collections.abc import Callable

# What the TypeScript writes (Buffer.from(JSON.stringify(…)).toString('base64url')), from Node.
TS_REF = "WzEsImhvbWUiLCJBcmNoaXZpby8yMDI2IFwiT3R0b2JyZVwiwrfDvCIsIjE3OTExNDkzNDUiLDQyXQ"
TS_REF_ESCAPES = "WzEsImFcXGIiLCJUYWJcdGhlcmVcdTAwMDHigKjwn5iAIiwiNyIsNDI5NDk2NzI5NV0"
TS_CURSOR = "WzEsW1sid29yayIsIklOQk9YIiwiMTc5MTE0OTM0NSIsMTIwXSxbIndvcmsiLCJTZW50IiwiIiwwXV1d"


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class TestRefs:
    def test_round_trips_any_folder_name_and_refuses_anything_else(self) -> None:
        ref = MessageRef("home", 'Archivio/2026 "Ottobre"·ü', 1791149345, 42)
        text = format_ref(ref)
        assert text == TS_REF
        assert parse_ref(text) == ref
        bad = [
            "",
            "abc",
            format_ref(MessageRef("a", "b", 1, 0)),
            b64('[2,"a","b","1",1]'),
            b64('[1,"a","b","1","5"]'),
            b64('[1,"a","b","1",-5]'),
            b64('[1,"a","b","x1",5]'),
            b64('[1,"a","b","1",1e300]'),
            b64('[1,"a","b","12345678901",5]'),
            b64('[1,"a","b","1",true]'),
            b64('[1,"a","b","1",NaN]'),
        ]
        for text in bad:
            with pytest.raises(MailError, match="Not a message reference") as raised:
                parse_ref(text)
            assert raised.value.code == "invalid_argument"

    def test_escapes_as_javascript_does(self) -> None:
        ref = MessageRef("a\\b", "Tab\there\x01\u2028😀", 7, 0xFFFFFFFF)
        assert format_ref(ref) == TS_REF_ESCAPES
        assert parse_ref(TS_REF_ESCAPES) == ref

    def test_reads_what_node_reads(self) -> None:
        # Padding and the standard alphabet, which Buffer's base64url decoder accepts.
        assert parse_ref("WzEsImEiLCJiIiwiMSIsMV0=") == MessageRef("a", "b", 1, 1)
        assert parse_ref(b64('[1.0,"a","b","1",5.0]')) == MessageRef("a", "b", 1, 5)

    def test_reports_a_ref_whose_folder_was_recreated_as_stale(self) -> None:
        with pytest.raises(MailError, match="out of date") as raised:
            check_fresh(MessageRef("a", "INBOX", 1, 5), 2)
        assert raised.value.code == "stale_reference"


class TestCursors:
    def test_byte_compatible_with_the_typescript(self) -> None:
        positions = [Position("work", "INBOX", "1791149345", 120), Position("work", "Sent", "", 0)]
        assert encode_cursor(positions) == TS_CURSOR
        assert decode_cursor(TS_CURSOR) == positions
        assert decode_cursor("WzEsW11d") == []

    @pytest.mark.parametrize(
        "text",
        [
            "nope",
            "",
            b64("[2,[]]"),
            b64("[1,{}]"),
            b64('[1,[["a","b","1"]]]'),
            b64('[1,[["a","b",1,2]]]'),
            b64('[1,[["a","b","1",1.5]]]'),
            b64('[1,[["a","b","1",-1]]]'),
            b64('[1,[["a","b","1",4294967296]]]'),
        ],
    )
    def test_refuses_anything_else(self, text: str) -> None:
        with pytest.raises(MailError, match="Not a cursor"):
            decode_cursor(text)


def leaf(
    kind: str,
    *,
    params: tuple[bytes, ...] = (),
    cid: bytes | None = None,
    encoding: bytes = b"7bit",
    size: int = 100,
    disposition: tuple[bytes, tuple[bytes, ...]] | None = None,
) -> tuple[object, ...]:
    main, sub = (part.encode() for part in kind.split("/"))
    head = (main, sub, params or None, cid, None, encoding, size)
    if main.lower() == b"text":
        return (*head, 1, None, disposition, None, None)
    if kind.lower() == "message/rfc822":
        return (*head, (None,) * 10, (), 3, None, disposition, None, None)
    return (*head, None, disposition, None, None)


def multipart(subtype: str, *children: tuple[object, ...]) -> tuple[object, ...]:
    return (*children, subtype.encode(), (b"boundary", b"x"), None, None, None)


def structure(node: tuple[object, ...]):
    return analyse_structure(BodyData.create(node))


class TestAnalyseStructure:
    def test_prefers_text_plain_and_lists_attachments_with_their_part_ids(self) -> None:
        root = multipart(
            "mixed",
            multipart("alternative", leaf("text/plain"), leaf("text/html")),
            leaf(
                "application/pdf",
                disposition=(b"attachment", (b"filename", b"report.pdf")),
                size=52_000,
            ),
            leaf("text/calendar", params=(b"method", b"REQUEST")),
            leaf("message/rfc822"),
        )
        found = structure(root)
        assert found.body is not None
        assert found.body.info == PartInfo(part="1.1", type="text/plain", size=100)
        assert found.body_format == "text"
        assert found.html is not None
        assert found.html.part == "1.2"
        assert found.attachments == (
            PartInfo(part="2", type="application/pdf", size=52_000, name="report.pdf"),
            PartInfo(part="4", type="message/rfc822", size=100),
        )
        assert found.calendar == PartInfo(part="3", type="text/calendar", size=100)

    def test_single_part_html_is_part_1_and_a_text_attachment_is_an_attachment(self) -> None:
        single = structure(leaf("TEXT/HTML"))
        assert single.body is not None
        assert single.body.info == PartInfo(part="1", type="text/html", size=100)
        assert single.body_format == "html"
        root = multipart(
            "mixed",
            leaf("text/plain"),
            leaf(
                "text/plain",
                params=(b"name", b"notes.txt"),
                disposition=(b"attachment", ()),
            ),
        )
        assert [a.name for a in structure(root).attachments] == ["notes.txt"]

    def test_keeps_what_decoding_needs(self) -> None:
        root = multipart(
            "related",
            leaf(
                "text/plain",
                params=(b"CHARSET", b"ISO-8859-1", b"format", b"flowed", b"DelSp", b"Yes"),
                encoding=b"QUOTED-PRINTABLE",
            ),
            leaf("image/png", cid=b"<logo@x>", encoding=b"base64"),
        )
        found = structure(root)
        assert found.body is not None
        assert (found.body.charset, found.body.encoding, found.body.flowed, found.body.delsp) == (
            "ISO-8859-1",
            "quoted-printable",
            True,
            True,
        )
        assert found.parts["2"].content_id == "logo@x"

    def test_a_cid_part_of_a_related_body_is_inline_not_an_attachment(self) -> None:
        root = multipart(
            "mixed",
            multipart(
                "related",
                leaf("text/html"),
                leaf("image/png", cid=b"<logo@x>", encoding=b"base64", size=780),
                leaf(
                    "image/jpeg",
                    cid=b"<photo@x>",
                    disposition=(b"inline", (b"filename", b"image001.jpg")),
                ),
                leaf("image/gif", cid=b"<kept@x>", disposition=(b"attachment", ())),
            ),
            # A Content-ID outside multipart/related says nothing about its use.
            leaf("image/png", cid=b"<other@x>"),
        )
        found = structure(root)
        assert [p.part for p in found.inline_parts] == ["1.2", "1.3"]
        assert [a.part for a in found.attachments] == ["1.4", "2"]
        assert [p.part for p in found.downloadable] == ["1.4", "2", "1.2", "1.3"]
        # Sizes are decoded: base64 takes 78 bytes per 57.
        assert found.inline_parts[0].size == 570
        assert found.parts["1.2"].size == 780

    def test_an_empty_structure_has_no_parts(self) -> None:
        for root in ((), [], None):
            found = analyse_structure(root)  # type: ignore[arg-type]
            assert (found.body, found.attachments, found.parts) == (None, (), {})

    def test_decodes_file_names(self) -> None:
        def name(*params: bytes) -> str | None:
            node = leaf("application/pdf", disposition=(b"attachment", params))
            return structure(node).attachments[0].name

        assert name(b"FILENAME", "relazione_giovedì_€.pdf".encode()) == "relazione_giovedì_€.pdf"
        assert name(b"filename", b"caf\xe9.pdf") == "café.pdf"
        assert name(b"filename", b"=?utf-8?B?cmVsYXppb25lLnBkZg==?=") == "relazione.pdf"
        assert name(b"filename*", b"utf-8''r%C3%A9sum%C3%A9.pdf") == "résumé.pdf"
        assert (
            name(b"filename*0*", b"utf-8''r%C3%A9", b"filename*1", b"port.pdf", b"filename", b"x")
            == "réport.pdf"
        )


class TestDecoding:
    def test_encoded_words(self) -> None:
        assert decode_words("=?utf-8?q?Caf=C3=A9?= \u202ex") == "Café \u202ex"
        assert decode_words("=?UTF-8?B?w6g=?= =?UTF-8?B?w6g=?=") == "èè"
        # A character split across two words.
        assert decode_words("=?utf-8?q?=C3?= =?utf-8?q?=A8?=") == "è"
        assert decode_words("a =?iso-8859-1?q?b=E9_c?= d") == "a bé c d"
        assert decode_words("=?x-unknown?q?a?=") == "=?x-unknown?q?a?="

    @pytest.mark.parametrize("charset", ["hex", "rot13", "base64", "zlib", "idna", "punycode"])
    def test_a_codec_that_is_not_a_charset_is_unknown(self, charset: str) -> None:
        from mailroom_core.mail._mime import decode_bytes, header_text

        word = f"=?{charset}?Q?41?="
        assert decode_words(word) == word
        assert header_text(word.encode()) == word
        assert decode_bytes(b"41\xe9", charset) == "41�"
        assert parameters((b"filename*", f"{charset}''41".encode())) == {"filename": "41"}

    def test_parameters(self) -> None:
        assert parameters((b"Name", b"a.pdf", b"charset", b"UTF-8")) == {
            "name": "a.pdf",
            "charset": "UTF-8",
        }
        assert parameters(None) == {}

    @pytest.mark.parametrize("cut", [1, 2, 3, 5, 17, 40])
    def test_base64_in_pieces(self, cut: int) -> None:
        data = bytes(range(256)) * 3
        encoded = base64.encodebytes(data)
        decoder = TransferDecoder("base64")
        out = b""
        for start in range(0, len(encoded), cut):
            piece = encoded[start : start + cut]
            out += decoder.feed(piece, final=start + cut >= len(encoded))
        assert out == data

    def test_base64_without_padding_at_the_end(self) -> None:
        assert TransferDecoder("base64").feed(b"QQ", final=True) == b"A"
        assert TransferDecoder("BASE64").feed(b"QUJD\r\nRA==", final=True) == b"ABCD"

    @pytest.mark.parametrize("cut", [1, 2, 3, 7])
    def test_quoted_printable_in_pieces(self, cut: int) -> None:
        encoded = b"gioved=C3=AC alle 18, perch=C3=A9 no=\r\nn ci vediamo=3D\r\nfine"
        decoder = TransferDecoder("quoted-printable")
        out = b""
        for start in range(0, len(encoded), cut):
            out += decoder.feed(encoded[start : start + cut], final=start + cut >= len(encoded))
        assert out.decode() == "giovedì alle 18, perché non ci vediamo=\r\nfine"

    def test_format_flowed(self) -> None:
        assert unflow("one \r\ntwo\r\n> q \r\n> r\r\n-- \r\nsig", False) == (
            "one two\n> q > r\n-- \nsig"
        )
        assert unflow("lo \nng", True) == "long"
        assert unflow(" From me", False) == "From me"


# Declared type, file name, expected type, content (src/mail/content-type.test.ts).
PDF = b"%PDF-1.4\n%%EOF\n"
PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0, 0])
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0, 0, 0])
GIF = "GIF89a……".encode()
WEBP = b"RIFF\0\0\0\0WEBPVP8 "
TEXT = b"plain words, which mention %PDF- later on"
CONTENT_TYPES = [
    ("application/octet-stream", "report.pdf", "application/pdf", PDF),
    ("application/x-download", None, "application/pdf", PDF),
    ("application/octet-stream", "scan", "image/png", PNG),
    ("image/png", "photo.png", "image/jpeg", JPEG),
    ("application/octet-stream", None, "image/gif", GIF),
    ("application/octet-stream", None, "image/webp", WEBP),
    ("text/plain", "notes.txt", "application/pdf", PDF),
    ("text/plain", "notes.txt", "text/plain", TEXT),
    ("application/vnd.ms-excel", "sheet.xls", "application/vnd.ms-excel", TEXT),
    ("Application/PDF", "x.pdf", "application/pdf", TEXT),
    ("image/svg+xml", "logo.svg", "image/svg+xml", TEXT),
    ("application/octet-stream", "notes.TXT", "text/plain", TEXT),
    ("application/octet-stream", "fake.pdf", "application/octet-stream", TEXT),
    ("application/octet-stream", "txt", "application/octet-stream", TEXT),
    ("image/png", "x.png", "application/octet-stream", TEXT),
    ("text/html; charset=x", None, "application/octet-stream", TEXT),
]


@pytest.mark.parametrize(("declared", "name", "expected", "data"), CONTENT_TYPES)
def test_content_type(declared: str, name: str | None, expected: str, data: bytes) -> None:
    assert content_type(data, declared, name) == expected


def test_search_criteria() -> None:
    from datetime import date

    assert SearchQuery().criteria() == ["ALL"]
    assert SearchQuery(
        sender="billing",
        since=date(2026, 10, 1),
        unread=True,
        flagged=False,
        has_attachment=True,
        gmail_query="has:attachment",
    ).criteria() == [
        "FROM",
        "billing",
        "SINCE",
        date(2026, 10, 1),
        "UNSEEN",
        "UNFLAGGED",
        "HEADER",
        "Content-Type",
        "multipart/mixed",
        "X-GM-RAW",
        "has:attachment",
    ]


def test_gmail_finds_attachments_through_its_own_search() -> None:
    """Gmail ignores SEARCH HEADER, so `has_attachment` becomes X-GM-RAW there, one criterion
    with any `gmail_query` grouped after it."""
    from mailroom_core.mail import search_arguments

    assert SearchQuery(has_attachment=True).criteria(gmail=True) == ["X-GM-RAW", "has:attachment"]
    combined = SearchQuery(
        unread=True, has_attachment=True, gmail_query="from:a OR from:b"
    ).criteria(gmail=True)
    assert combined == ["UNSEEN", "X-GM-RAW", "has:attachment (from:a OR from:b)"]
    assert search_arguments(combined) == (
        [b"UNSEEN", b"X-GM-RAW", b'"has:attachment (from:a OR from:b)"'],
        False,
    )
    assert SearchQuery(has_attachment=True, gmail_query=" ").criteria(gmail=True) == [
        "X-GM-RAW",
        "has:attachment",
    ]
    assert SearchQuery(has_attachment=False).criteria(gmail=True) == ["ALL"]
    assert SearchQuery(gmail_query="label:x").criteria(gmail=True) == ["X-GM-RAW", "label:x"]
    assert SearchQuery(has_attachment=True).criteria() == [
        "HEADER",
        "Content-Type",
        "multipart/mixed",
    ]


def test_each_account_gets_its_own_search() -> None:
    """`search_messages` builds criteria per account and `read_page` sends each its own: Gmail's
    X-GM-RAW to the Gmail account, HEADER to the other. A mapping that leaves out a source's
    account is a programming error, refused before any search."""
    sent: dict[str, list[object]] = {}

    class Client:
        def __init__(self, account: str) -> None:
            self.account = account

        def _raw_command_untagged(self, command: bytes, arguments: list[object]) -> list[bytes]:
            sent[self.account] = arguments
            return [b""]

    class Connections:
        def capabilities(self, account: str) -> SimpleNamespace:
            return SimpleNamespace(gmail=account == "gmail")

        def with_mailbox(
            self, account: str, path: str, read_only: bool, fn: Callable[[MailboxContext], object]
        ) -> object:
            return fn(MailboxContext(Client(account), path, read_only, 1, 0, None))  # type: ignore[arg-type]

    connections = cast("MailConnections", Connections())
    page = search_messages(
        connections, ["gmail", "other"], "INBOX", SearchQuery(has_attachment=True)
    )
    assert page.messages == ()
    assert page.failures == ()
    assert sent == {
        "gmail": [b"X-GM-RAW", b"has:attachment"],
        "other": [b"HEADER", b"Content-Type", b"multipart/mixed"],
    }

    sent.clear()
    sources = [Source("gmail", "INBOX"), Source("other", "INBOX")]
    with pytest.raises(ValueError, match="other"):
        read_page(connections, sources, {"gmail": ["ALL"]}, limit=5)
    assert sent == {}


def test_thread_ids_leave_out_what_cannot_be_searched_for() -> None:
    from mailroom_core.mail.search import _thread_ids

    nul = chr(0)
    headers = (
        f"Message-ID: <n{nul}ul@x>\r\nReferences: <a@x> <sp ace@x> <perché@x>\r\n"
        "In-Reply-To: <b@x>\r\n"
    )
    assert _thread_ids(headers, f"<n{nul}ul@x>") == ["<a@x>", "<b@x>"]
    assert _thread_ids("References: <a@x>\r\n", "<sp ace@x>") == ["<a@x>"]
    assert _thread_ids("", "<own@x>") == ["<own@x>"]
    assert _thread_ids("", "<line\r\nbreak@x>") == []


def test_search_arguments_quote_or_refuse_what_would_change_the_command() -> None:
    from datetime import date

    from imapclient.imapclient import _literal

    from mailroom_core.mail import search_arguments

    def args(*criteria: object) -> tuple[list[bytes], bool]:
        return search_arguments(list(criteria))

    assert args("SUBJECT", "invoice", "SINCE", date(2026, 10, 1), "UID", "1:41") == (
        [b"SUBJECT", b"invoice", b"SINCE", b"01-Oct-2026", b"UID", b"1:41"],
        False,
    )
    assert args("SUBJECT", "(urgent)")[0][1] == b'"(urgent)"'
    assert args("SUBJECT", ")")[0][1] == b'")"'
    assert args("SUBJECT", "{5}")[0][1] == b'"{5}"'
    assert args("TEXT", 'say "hi" \\ bye')[0][1] == b'"say \\"hi\\" \\\\ bye"'
    assert args("SUBJECT", "")[0][1] == b'""'
    assert args("SUBJECT", "a*b%")[0][1] == b'"a*b%"'
    for sequence_like in ("*", "1:*", "*,*"):
        assert args("SUBJECT", sequence_like)[0][1] == b'"' + sequence_like.encode() + b'"'
    tokens, utf8 = args("SUBJECT", "perché")
    assert utf8
    assert isinstance(tokens[1], _literal)
    assert tokens[1] == "perché".encode()
    grouped, _ = args("OR", ["HEADER", "References", "<a)b>"], ["HEADER", "Message-ID", "<c@d>"])
    assert grouped == [
        b"OR",
        b"(HEADER",
        b"References",
        b'"<a)b>")',
        b"(HEADER",
        b"Message-ID",
        b"<c@d>)",
    ]
    for value in ("x\r\nZ1 DELETE Archive", "x\nZ", "x\rZ", "x\0"):
        with pytest.raises(MailError) as caught:
            args("SUBJECT", value)
        assert caught.value.code == "invalid_argument"
    with pytest.raises(MailError):
        args(["SUBJECT", "perché"])
    with pytest.raises(MailError):
        args("SUBJECT", "x", [])


def test_preview_renders_like_read_without_quotes_or_link_targets() -> None:
    html = (
        '<div>See you &amp; the <a href="https://example.org/kids?a=1">kids</a> at 8 &lt;3</div>'
        '<div class="gmail_quote">On Wed, Ana &lt;ana@example.org&gt; wrote:'
        "<blockquote>Dinner?</blockquote></div>"
    )
    assert preview_of(html, "html") == "See you & the kids at 8 <3"
    assert len(preview_of("word " * 500, "text")) == 200
    assert json.dumps(preview_of("[a\\]b](https://x.org) c", "text")) == json.dumps("a]b c")


def test_body_text_decodes_the_charset_and_unwraps_flowed_text() -> None:
    from mailroom_core.mail import MimePart, body_text

    part = MimePart("1", "text/plain", 10, None, "8bit", "ISO-8859-1", None, None, flowed=True)
    assert body_text("perché \r\nno".encode("latin-1"), part, complete=True) == "perché no"
    utf8 = MimePart("1", "text/plain", 10, None, "8bit", "x-unknown", None, None)
    # A character cut at the byte cap is dropped, not replaced.
    assert body_text("già".encode()[:-1], utf8, complete=False) == "gi"
    assert body_text(b"\xff", utf8, complete=True) == "�"
