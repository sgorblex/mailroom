"""Designed mail: an .eml or an HTML file and its files imported as built, the draft marker that
keeps what the import changed, and every check before sending (docs/core.md, *Designed mail*)."""

import base64
from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import cast

import pytest

from mailroom_core.mail import (
    DRAFT_HEADER,
    GMAIL_CLIP_BYTES,
    MAX_HTML_BYTES,
    MAX_IMPORT_PARTS,
    Mailbox,
    MailError,
    MessageInput,
    OutgoingAttachment,
    check_message,
    compose,
    draft_answers,
    draft_designed,
    import_eml,
    import_html,
    is_own_draft,
)
from mailroom_core.render import unsafe_parts

CAP = 10 * 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\nimage"
ME = Mailbox("me@example.org", "Me")


def parsed(raw: bytes) -> EmailMessage:
    return cast("EmailMessage", message_from_bytes(raw, policy=policy.default))


def shape(message: EmailMessage) -> object:
    """The MIME tree as content types, nested."""
    if message.is_multipart():
        return (message.get_content_type(), [shape(p) for p in message.iter_parts()])
    return message.get_content_type()


def eml(html: str, *, text: str | None = None, images: dict[str, bytes] | None = None) -> bytes:
    """A designed message as a builder exports it: mixed(alternative(text, related(html,
    images)), a PDF), with headers Mailroom must not keep."""
    message = EmailMessage()
    message["From"] = "Designer <designer@studio.example>"
    message["To"] = "Ana <ana@example.org>, bo@example.org"
    message["Cc"] = "cy@example.org"
    message["Subject"] = "Spring news"
    message["Message-ID"] = "<old@studio.example>"
    message["DKIM-Signature"] = "v=1; d=studio.example; b=abc"
    message["X-Mailroom-Draft"] = "1; answers=forged"
    message.set_content(text or "Spring news, in text.")
    message.add_alternative(html, subtype="html")
    if images:
        related = message.get_payload()[1]
        for cid, content in images.items():
            related.add_related(
                content, "image", "png", cid=f"<{cid}>", disposition="inline", filename=f"{cid}.png"
            )
    message.add_attachment(b"%PDF-1.4", "application", "pdf", filename="terms.pdf")
    return message.as_bytes()


def body_html(message: EmailMessage) -> str:
    part = message.get_body(("html",))
    assert part is not None
    return cast("str", part.get_content())


def codes(raw: bytes, changes: dict[str, int] | None = None, cap: int = CAP) -> list[str]:
    return [finding.code for finding in check_message(raw, cap, changes)]


def stored(built_body: bytes, changes: tuple[tuple[str, int], ...] = ()) -> bytes:
    draft = MessageInput(
        sender=ME, to=(Mailbox("ana@example.org"),), subject="s", text="", draft=True,
        designed=changes,
    )  # fmt: skip
    return compose(draft, body=built_body).stored


def test_an_eml_keeps_its_structure_and_parts_and_loses_its_headers() -> None:
    html = '<p>Hello <img src="cid:logo" alt="Logo"></p>'
    source = eml(html, images={"logo": PNG})
    built = import_eml(source, CAP)
    message = parsed(stored(built.body, built.changes))

    assert shape(message) == shape(parsed(source))
    assert shape(message) == (
        "multipart/mixed",
        [
            (
                "multipart/alternative",
                ["text/plain", ("multipart/related", ["text/html", "image/png"])],
            ),
            "application/pdf",
        ],
    )
    assert message.get_body(("plain",)).get_content().strip() == "Spring news, in text."
    [image] = [p for p in message.walk() if p["Content-ID"] == "<logo>"]
    assert image.get_content() == PNG
    [pdf] = message.iter_attachments()
    assert pdf.get_content() == b"%PDF-1.4"
    assert '<img src="cid:logo" alt="Logo">' in body_html(message)
    for gone in ("DKIM-Signature", "designer@studio.example", "<old@studio.example>", "forged"):
        assert gone not in stored(built.body).decode()
    assert built.to == (Mailbox("ana@example.org", "Ana"), Mailbox("bo@example.org"))
    assert built.cc == (Mailbox("cy@example.org"),)
    assert built.subject == "Spring news"
    assert built.attachments == 1
    assert built.changes == ()


def test_the_marker_keeps_what_the_import_changed() -> None:
    raw = stored(b"Content-Type: text/plain\r\n\r\nx\r\n", (("scripts", 2), ("text", 1)))
    head = raw.split(b"\r\n\r\n")[0]
    assert f"{DRAFT_HEADER}: 1; designed=scripts.2-text.1".encode() in head
    assert is_own_draft(head)
    assert draft_designed(head) == {"scripts": 2, "text": 1}
    assert draft_answers(head) is None
    plain = stored(b"Content-Type: text/plain\r\n\r\nx\r\n")
    assert f"{DRAFT_HEADER}: 1; designed\r\n".encode() in plain
    assert draft_designed(plain) == {}
    assert draft_designed(f"{DRAFT_HEADER}: 1".encode()) is None
    assert draft_designed(f"{DRAFT_HEADER}: 1; designed=x".encode()) is None


def test_what_can_never_be_sent_is_removed_and_counted() -> None:
    html = (
        "<html><head><style>p{color:red}</style><link rel=stylesheet href='https://x.example/s.css'>"
        "<script>steal()</script></head><body>"
        '<p onclick="steal()" style="background:url(https://x.example/b.png)">Hi'
        '<a href="javascript:steal()">x</a><a href="https://shop.example/">shop</a></p>'
        '<img src="https://track.example/p.gif" alt=""><img src="data:image/png;base64,iVBO" alt="">'
        '<form action="https://x.example/"><input name="q"></form><iframe src="https://x.example/"></iframe>'
        "</body></html>"
    )
    built = import_eml(eml(html), CAP)
    sent = body_html(parsed(stored(built.body)))
    for gone in ("<script", "steal", "<style", "stylesheet", "track.example", "data:", "<form",
                 "<input", "<iframe", "url("):  # fmt: skip
        assert gone not in sent
    assert '<a href="https://shop.example/">shop</a>' in sent
    assert dict(built.changes) == {
        "data": 1, "forms": 2, "handlers": 1, "images": 1, "remote": 3, "scripts": 2, "styles": 1,
    }  # fmt: skip
    findings = check_message(stored(built.body), CAP, dict(built.changes))
    assert [f.code for f in findings] == [
        "removed_scripts", "removed_forms", "removed_handlers", "removed_images",
        "removed_data_images", "removed_remote", "removed_styles",
    ]  # fmt: skip
    assert findings[0].text == "2 scripts or javascript: links were removed."
    assert "many mail clients block remote images" in findings[3].text


def test_a_missing_text_part_is_made_from_the_html() -> None:
    message = EmailMessage()
    message["Subject"] = "Only HTML"
    message.set_content("<p>Only <b>HTML</b></p>", subtype="html")
    built = import_eml(message.as_bytes(), CAP)
    made = parsed(stored(built.body, built.changes))
    assert shape(made) == ("multipart/alternative", ["text/plain", "text/html"])
    assert made.get_body(("plain",)).get_content().strip() == "Only HTML"
    assert dict(built.changes) == {"text": 1}
    assert "text_derived" in codes(stored(built.body), dict(built.changes))
    assert "no_text_part" not in codes(stored(built.body), dict(built.changes))


def test_a_related_message_without_text_gets_an_alternative_around_it() -> None:
    message = EmailMessage()
    message.set_content('<p><img src="cid:a" alt="a"></p>', subtype="html")
    message.add_related(PNG, "image", "png", cid="<a>")
    built = import_eml(message.as_bytes(), CAP)
    assert shape(parsed(built.body)) == (
        "multipart/alternative",
        ["text/plain", ("multipart/related", ["text/html", "image/png"])],
    )


def test_an_eml_that_is_no_message_or_too_big_is_refused() -> None:
    with pytest.raises(MailError, match="not a message"):
        import_eml(b"", CAP)
    with pytest.raises(MailError) as over:
        import_eml(eml("<p>x</p>"), 4)
    assert over.value.code == "too_large"
    wide = eml("<p>" + "x" * (MAX_HTML_BYTES + 1) + "</p>")
    with pytest.raises(MailError, match="HTML is at most"):
        import_eml(wide, CAP)
    many = EmailMessage()
    many.set_content("x")
    for index in range(MAX_IMPORT_PARTS):
        many.add_attachment(b"x", "application", "octet-stream", filename=f"{index}.bin")
    with pytest.raises(MailError, match="too many parts"):
        import_eml(many.as_bytes(), CAP)
    deep = b"Content-Type: text/plain\r\n\r\nx\r\n"
    for level in range(40):
        deep = (
            f'Content-Type: multipart/mixed; boundary="b{level}"\r\n\r\n--b{level}\r\n'.encode()
            + deep
            + f"\r\n--b{level}--\r\n".encode()
        )
    with pytest.raises(MailError) as nested:
        import_eml(deep, CAP)
    assert nested.value.code == "invalid_argument"


def test_header_injection_from_the_file_goes_nowhere() -> None:
    source = eml("<p>x</p>").replace(
        b"Subject: Spring news", b"Subject: =?utf-8?q?a=0D=0ABcc:_x@evil.example?="
    )
    built = import_eml(source, CAP)
    assert "\r" not in built.subject
    assert "\n" not in built.subject
    assert b"\r\nBcc:" not in stored(built.body).split(b"\r\n\r\n")[0]


def test_an_html_file_takes_its_images_by_relative_path_or_cid_and_attaches_the_rest() -> None:
    files = [
        OutgoingAttachment("logo.png", "image/png", PNG),
        OutgoingAttachment("my photo.png", "image/png", PNG + b"2"),
        OutgoingAttachment("hero.png", "image/png", PNG + b"3", "hero@x"),
        OutgoingAttachment("terms.pdf", "application/pdf", b"%PDF"),
    ]
    html = (
        '<p><img src="images/logo.png" alt="Logo"><img src="cid:my%20photo.png" alt="Me">'
        '<img src="cid:hero@x" alt="Hero"><img src="./images/logo.png?v=2" alt="Again"></p>'
    )
    built = import_html(html, files, CAP)
    message = parsed(built.body)
    assert shape(message) == (
        "multipart/mixed",
        [
            (
                "multipart/alternative",
                [
                    "text/plain",
                    ("multipart/related", ["text/html", "image/png", "image/png", "image/png"]),
                ],
            ),
            "application/pdf",
        ],
    )
    sent = body_html(message)
    assert '<img src="cid:logo.png" alt="Logo">' in sent
    assert sent.count("cid:logo.png") == 2
    inline = {p["Content-ID"]: p.get_content() for p in message.walk() if p["Content-ID"]}
    assert inline["<logo.png>"] == PNG
    assert inline["<hero@x>"] == PNG + b"3"
    [photo] = [cid for cid, content in inline.items() if content == PNG + b"2"]
    assert f"cid:{photo[1:-1]}" in sent
    assert [p.get_filename() for p in message.iter_attachments()] == ["terms.pdf"]
    assert built.attachments == 1
    assert (built.to, built.subject) == ((), "")
    assert codes(built.body, dict(built.changes)) == ["text_derived"]


def test_an_image_without_alt_is_found() -> None:
    built = import_html(
        '<img src="cid:a"><img src="cid:a" alt="">',
        [OutgoingAttachment("a", "image/png", PNG, "a")],
        CAP,
    )
    [finding] = [f for f in check_message(built.body, CAP) if f.code == "image_without_alt"]
    assert (finding.count, finding.text) == (1, "1 image has no alt text.")


def test_a_cid_with_no_part_and_a_part_never_shown_are_found() -> None:
    built = import_html(
        '<img src="cid:gone" alt="g">',
        [OutgoingAttachment("unused.png", "image/png", PNG, "unused")],
        CAP,
    )
    findings = {f.code: f for f in check_message(built.body, CAP)}
    assert findings["cid_without_part"].items == ("gone",)
    assert findings["part_not_referenced"].items == ("unused.png",)


def test_html_past_gmails_clip_is_found() -> None:
    fits = import_html("<p>" + "x" * (GMAIL_CLIP_BYTES - 400) + "</p>", [], CAP)
    assert "html_clipped" not in codes(fits.body)
    clipped = import_html("<p>" + "x" * GMAIL_CLIP_BYTES + "</p>", [], CAP)
    [finding] = [f for f in check_message(clipped.body, CAP) if f.code == "html_clipped"]
    assert "Gmail clips HTML past 102 KB" in finding.text


def test_remote_images_and_a_missing_text_part_are_found_in_a_message_not_imported() -> None:
    raw = (
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b'<p><img src="https://x.example/a.png" alt="a"><img src="//x.example/b.png" alt="b"></p>\r\n'
    )
    findings = {f.code: f for f in check_message(raw, CAP)}
    assert findings["remote_images"].count == 2
    assert "no_text_part" in findings


def test_a_message_past_the_attachment_cap_is_found() -> None:
    built = import_html(
        "<p>x</p>", [OutgoingAttachment("a.bin", "application/octet-stream", b"x" * 3000)], CAP
    )
    assert "too_large" not in codes(built.body, cap=CAP)
    [finding] = [f for f in check_message(built.body, 3000) if f.code == "too_large"]
    assert "over the 3 KB attachment limit" in finding.text


def test_a_clean_message_has_nothing_to_look_at() -> None:
    built = import_eml(eml('<p><img src="cid:logo" alt="Logo"></p>', images={"logo": PNG}), CAP)
    assert check_message(stored(built.body, built.changes), CAP, dict(built.changes)) == ()


def test_unsafe_parts_counts_by_kind() -> None:
    assert unsafe_parts("") == {}
    assert unsafe_parts(
        '<p><a href=" JavaScript:x()">a</a><img src="cid:a" background="b.png"></p>'
    ) == {
        "scripts": 1,
        "images": 1,
    }


def test_findings_name_only_what_they_must_and_keep_it_to_one_line() -> None:
    cids = "".join(f'<img src="cid:c{i}%0D%0Aline" alt="">' for i in range(30))
    findings = check_message(import_html(cids, [], CAP).body, CAP)
    [missing] = [f for f in findings if f.code == "cid_without_part"]
    assert missing.count == 30
    assert len(missing.items) == 20
    assert all("\n" not in item and "\r" not in item for item in missing.items)


def test_a_data_image_in_an_html_file_stays_out() -> None:
    data = base64.b64encode(PNG).decode()
    built = import_html(f'<img src="data:image/png;base64,{data}" alt="x">', [], CAP)
    assert "data:" not in body_html(parsed(built.body))
    assert dict(built.changes)["data"] == 1


def test_a_content_id_unfit_for_a_header_is_refused() -> None:
    for content_id in ("a\nb", "a b", "x>y"):
        with pytest.raises(MailError, match="content ID") as refused:
            import_html(
                "<p>x</p>", [OutgoingAttachment("a.png", "image/png", PNG, content_id)], CAP
            )
        assert refused.value.code == "invalid_argument"


def test_a_related_root_html_with_a_content_id_is_no_part_never_shown() -> None:
    raw = (
        b'Content-Type: multipart/related; boundary="r"; type="text/html"; start="<root@x>"\r\n\r\n'
        b"--r\r\nContent-Type: text/plain; charset=utf-8\r\nContent-ID: <text@x>\r\n\r\nLogo\r\n"
        b"--r\r\nContent-Type: text/html; charset=utf-8\r\nContent-ID: <root@x>\r\n\r\n"
        b'<p><img src="cid:logo" alt="Logo"></p>\r\n'
        b"--r\r\nContent-Type: image/png\r\nContent-ID: <logo>\r\n\r\npng\r\n--r--\r\n"
    )
    assert "part_not_referenced" not in codes(raw)
    assert "part_not_referenced" not in codes(import_eml(raw, CAP).body)


def test_an_attached_message_counts_against_the_cap() -> None:
    inner = EmailMessage()
    inner["Subject"] = "Forwarded"
    inner.set_content("y" * 4000)
    outer = EmailMessage()
    outer.set_content("x")
    outer.add_attachment(inner)
    with pytest.raises(MailError) as over:
        import_eml(outer.as_bytes(), 3000)
    assert over.value.code == "too_large"
