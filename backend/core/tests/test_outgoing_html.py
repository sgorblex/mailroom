"""`render.outgoing_html`: what the owner's HTML may carry out, and the `data:` images it turns
into parts."""

import base64
from html import escape

import pytest

from mailroom_core.render import outgoing_html

PNG = b"\x89PNG\r\n\x1a\nfake"
DATA_PNG = f"data:image/png;base64,{base64.b64encode(PNG).decode()}"


def test_keeps_the_editors_formatting() -> None:
    html = (
        "<p><b>b</b> <i>i</i> <u>u</u> <s>s</s> <a href='https://x.org/a'>l</a></p>"
        "<ul><li>one</li></ul><ol><li>two</li></ol><blockquote type='cite'>q</blockquote>"
    )
    assert outgoing_html(html).html == (
        '<p><b>b</b> <i>i</i> <u>u</u> <s>s</s> <a href="https://x.org/a">l</a></p>'
        '<ul><li>one</li></ul><ol><li>two</li></ol><blockquote type="cite">q</blockquote>'
    )


def test_drops_what_runs_submits_frames_or_styles_the_page() -> None:
    html = (
        "<p onclick='x()'>a<script>alert(1)</script></p>"
        "<style>p{color:red}</style><link rel=stylesheet href='https://x.org/s.css'>"
        "<form action='https://x.org'><input name=p></form>"
        "<iframe src='https://x.org'></iframe><object data='x.swf'></object>"
        "<svg><script>alert(2)</script></svg><math>m</math>"
    )
    out = outgoing_html(html).html
    assert out == "<p>a</p>"


def test_keeps_web_and_mail_links_only() -> None:
    out = outgoing_html(
        "<a href='javascript:alert(1)'>j</a><a href='data:text/html,x'>d</a><a href='/here'>r</a>"
        "<a href='cid:a'>c</a><a href='mailto:a@x.org'>m</a><a href=' https:\\\\x.org\\a'>w</a>"
    ).html
    assert out == (
        '<a>j</a><a>d</a><a>r</a><a>c</a><a href="mailto:a@x.org">m</a>'
        '<a href="https://x.org/a">w</a>'
    )


def test_keeps_only_safe_inline_styles() -> None:
    out = outgoing_html(
        '<span style="color:red;background-image:url(https://x.org/t.png);'
        'width:expression(x);position:fixed;font-weight:bold">s</span>'
    ).html
    assert out == '<span style="color:red;font-weight:bold">s</span>'


@pytest.mark.parametrize(
    ("style", "kept"),
    [
        ("mso-line-height-rule:exactly", None),
        (";", None),
        ("  ", None),
        ("color", None),
        ("color:", None),
        ("color:/* nothing */", None),
        ("co/**/lor:red", None),
        ("COLOR: Red ;  Margin : 0", "color:Red;margin:0"),
        ('font-family:"a;b", serif', "font-family:&quot;a;b&quot; , serif"),
        ("margin:0 /* top */ 1px", "margin:0 1px"),
        ("color:rgb(1,2,3);width:calc(100% - 2px)", "color:rgb(1, 2, 3);width:calc(100% - 2px)"),
        # What nh3 would mangle, differently each time: bare and nested blocks, broken strings.
        ("margin:(1px) 2px;color:red", "color:red"),
        ("width:calc(1px + (2px));width:max(1px, min(2px, 3px))", None),
        ("color:#fff(red)", None),
        ("margin:[1px];color:{red}", None),
        ('font-family:"a\nb"', None),
        ("font-family:'open", None),
    ],
)
def test_a_style_keeps_valid_allowed_declarations_or_goes(style: str, kept: str | None) -> None:
    out = outgoing_html(f'<p style="{escape(style)}">s</p>').html
    assert out == ("<p>s</p>" if kept is None else f'<p style="{kept}">s</p>')
    # Cleaning twice gives what cleaning once did: a designed draft is checked that way.
    assert outgoing_html(out).html == out


def test_keeps_cid_images_it_knows_and_drops_every_other_source() -> None:
    out = outgoing_html(
        "<img src='cid:one@x' alt='a'><img src='cid:%3Ctwo@x%3E'><img src='cid:three@x'>"
        "<img src='https://x.org/p.png'><img src='data:image/svg+xml;base64,PHN2Zz4='>",
        content_ids=["<one@x>", "two@x"],
        data_images=True,
    )
    assert out.html == '<img src="cid:one@x" alt="a"><img src="cid:two@x">'
    assert out.content_ids == {"one@x", "two@x"}
    assert out.images == ()


def test_turns_data_images_into_parts_once_each() -> None:
    out = outgoing_html(f"<p><img src='{DATA_PNG}'><img src='{DATA_PNG}'></p>")
    [image] = out.images
    assert image.content_type == "image/png"
    assert image.content == PNG
    assert (
        out.html == f'<p><img src="cid:{image.content_id}"><img src="cid:{image.content_id}"></p>'
    )
    assert out.content_ids == {image.content_id}


def test_drops_data_images_when_told_and_ones_that_do_not_decode() -> None:
    assert outgoing_html(f"a<img src='{DATA_PNG}'>b", data_images=False).html == "ab"
    broken = outgoing_html("<img src='data:image/png;base64,@@@'><img alt=x>")
    assert broken.images == ()
    assert broken.html == ""


def test_answers_nothing_for_empty_markup() -> None:
    for html in ("", "   ", "<!-- c -->"):
        out = outgoing_html(html)
        assert out.html.strip() == ""
        assert out.content_ids == frozenset()


def test_keeps_the_signature_mark_on_a_div_unless_told() -> None:
    html = "<div data-mailroom-signature='x'><p>Ada</p></div><p data-mailroom-signature>p</p>"
    assert outgoing_html(html).html == ('<div data-mailroom-signature=""><p>Ada</p></div><p>p</p>')
    assert outgoing_html(html, signature_mark=False).html == "<div><p>Ada</p></div><p>p</p>"
