"""`parsed_message`: a raw message as the filter engine reads it."""

from __future__ import annotations

from datetime import UTC, datetime

from mailroom_core.mail import parsed_message
from mailroom_core.render import Address


def message(headers: str, body: bytes = b"hello\r\n") -> bytes:
    return headers.replace("\n", "\r\n").encode() + b"\r\n" + body


def test_headers_are_decoded() -> None:
    parsed = parsed_message(
        message(
            "From: =?utf-8?q?Ad=C3=A0?= <ada@example.org>\n"
            'To: a@example.org, "Bee, B." <b@example.org>\n'
            "Cc: Team: c@example.org, d@example.org;\n"
            "Bcc: e@example.org\n"
            "Subject: =?utf-8?b?Q2lhbyDwn5GL?=\n"
            "Date: Mon, 02 Mar 2026 23:30:00 -0200\n"
        )
    )
    assert parsed.sender == (Address("Adà", "ada@example.org"),)
    assert parsed.to == (Address(None, "a@example.org"), Address("Bee, B.", "b@example.org"))
    assert [a.address for a in parsed.cc] == ["c@example.org", "d@example.org"]
    assert parsed.bcc == (Address(None, "e@example.org"),)
    assert parsed.subject == "Ciao 👋"
    # 23:30 at -02:00 is the next day in UTC, as the server's SENTBEFORE would not see it.
    assert parsed.date == datetime(2026, 3, 3, 1, 30, tzinfo=UTC)


def test_missing_and_unreadable_headers_are_empty() -> None:
    parsed = parsed_message(message("Date: not a date\nTo: undisclosed-recipients:;\n"))
    assert (parsed.sender, parsed.to, parsed.cc, parsed.bcc, parsed.subject, parsed.date) == (
        (),
        (),
        (),
        (),
        "",
        None,
    )


def test_one_malformed_address_does_not_drop_its_neighbours() -> None:
    for to in ("a@x.org, b@y.org,", "a@x.org; b@y.org"):
        parsed = parsed_message(message(f"To: {to}\n"))
        assert [a.address for a in parsed.to if a.address] == ["a@x.org", "b@y.org"]
    commented = parsed_message(message("From: a@x.org (Doe, John)\n"))
    assert [a.address for a in commented.sender] == ["a@x.org"]
    encoded = parsed_message(message("From: =?utf-8?q?Doe=2C_John?= <j@x.org>\n"))
    assert encoded.sender == (Address("Doe, John", "j@x.org"),)


def test_only_the_first_of_a_repeated_field_counts() -> None:
    """As in the ENVELOPE every client shows: a second From cannot satisfy a sender rule."""
    parsed = parsed_message(
        message("From: real@sender.org\nFrom: trusted@bank.example\nTo: a@x.org\nTo: b@y.org\n")
    )
    assert [a.address for a in parsed.sender] == ["real@sender.org"]
    assert [a.address for a in parsed.to] == ["a@x.org"]


def test_a_date_without_a_zone_is_utc() -> None:
    for value in ("Mon, 02 Mar 2026 10:00:00 -0000", "Mon, 02 Mar 2026 10:00:00"):
        assert parsed_message(message(f"Date: {value}\n")).date == datetime(
            2026, 3, 2, 10, tzinfo=UTC
        )


def test_a_date_out_of_range_is_none() -> None:
    assert parsed_message(message("Date: Fri, 31 Dec 9999 23:59:59 -1200\n")).date is None


def test_a_folded_encoded_subject_is_joined() -> None:
    parsed = parsed_message(message("Subject: =?utf-8?q?Ciao?=\n =?utf-8?q?_mondo?=\n"))
    assert parsed.subject == "Ciao mondo"


def test_8bit_headers_are_read_as_utf8_or_latin1() -> None:
    raw = b"Subject: caff\xc3\xa8\r\nFrom: Ren\xe9 <rene@example.org>\r\n\r\nx"
    parsed = parsed_message(raw)
    assert parsed.subject == "caffè"
    assert parsed.sender == (Address("René", "rene@example.org"),)


def test_the_text_is_every_inline_plain_part() -> None:
    body = (
        b"--b\r\nContent-Type: text/plain; charset=iso-8859-1\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n\r\ncaff=E8\r\n"
        b"--b\r\nContent-Type: text/html\r\n\r\n<p>ignored</p>\r\n"
        b"--b\r\nContent-Type: text/plain\r\n\r\nsecond\r\n"
        b"--b\r\nContent-Type: text/plain; name=notes.txt\r\n"
        b"Content-Disposition: attachment; filename=notes.txt\r\n\r\nattached\r\n"
        b"--b--\r\n"
    )
    parsed = parsed_message(message('Content-Type: multipart/mixed; boundary="b"\n', body))
    assert "caffè" in parsed.text
    assert "second" in parsed.text
    assert "ignored" not in parsed.text
    assert "attached" not in parsed.text


def test_without_a_plain_part_the_html_is_read_as_text() -> None:
    body = (
        b"<html><head><style>p{}</style></head><body><p class=k>total <b>due</b></p></body></html>"
    )
    parsed = parsed_message(message("Content-Type: text/html; charset=utf-8\n", body))
    assert "total due" in parsed.text
    assert "class" not in parsed.text
    assert "<" not in parsed.text


def test_a_charset_nobody_knows_is_decoded_as_utf8() -> None:
    parsed = parsed_message(
        message("Content-Type: text/plain; charset=x-nonsense\n", b"ok \xc3\xa8")
    )
    assert parsed.text.startswith("ok è")


def test_garbage_never_raises() -> None:
    for raw in (b"", b"\x00\xff\xfe", b"Content-Type: multipart/mixed\r\n\r\n--", b"no headers"):
        parsed = parsed_message(raw)
        assert isinstance(parsed.text, str)


def test_an_attached_message_is_not_the_body() -> None:
    body = (
        b"--b\r\nContent-Type: text/html\r\n\r\n<p>outer</p>\r\n"
        b"--b\r\nContent-Type: message/rfc822\r\n\r\n"
        b"Subject: inner\r\nContent-Type: text/plain\r\n\r\nforwarded text\r\n"
        b"--b--\r\n"
    )
    parsed = parsed_message(message('Content-Type: multipart/mixed; boundary="b"\n', body))
    assert "outer" in parsed.text
    assert "forwarded" not in parsed.text


def test_a_named_inline_text_part_is_a_file_as_in_the_view() -> None:
    body = (
        b"--b\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b"--b\r\nContent-Type: text/plain\r\nContent-Disposition: inline; filename=a.txt\r\n"
        b"\r\nfile\r\n--b--\r\n"
    )
    parsed = parsed_message(message('Content-Type: multipart/mixed; boundary="b"\n', body))
    assert "body" in parsed.text
    assert "file" not in parsed.text


def test_a_blank_plain_part_gives_way_to_the_html() -> None:
    body = (
        b"--b\r\nContent-Type: text/plain\r\n\r\n \r\n"
        b"--b\r\nContent-Type: text/html\r\n\r\n<p>visible</p>\r\n--b--\r\n"
    )
    parsed = parsed_message(message('Content-Type: multipart/alternative; boundary="b"\n', body))
    assert parsed.text.strip() == "visible"


def test_line_breaks_are_newlines_and_flowed_text_is_joined() -> None:
    crlf = parsed_message(message("Content-Type: text/plain\n", b"one\r\ntwo\r\n"))
    assert crlf.text == "one\ntwo\n"
    flowed = parsed_message(
        message("Content-Type: text/plain; format=flowed\n", b"soft \r\nbreak\r\n")
    )
    assert flowed.text.startswith("soft break")
