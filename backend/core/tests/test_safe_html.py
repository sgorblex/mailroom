"""`render.safe_html` on hostile markup: nothing that runs, submits, frames or fetches survives."""

import base64
import time

from mailroom_core.render import safe_html

PNG = b"\x89PNG\r\n\x1a\nfake"


def clean(html: str, *, remote: bool = False, images=None, max_inline_bytes: int = 1_000_000):
    return safe_html(
        html, inline_images=images or {}, remote=remote, max_inline_bytes=max_inline_bytes
    )


def test_drops_scripts_and_their_content():
    out = clean("<p>a<script>alert(1)</script>b</p><script src='https://x/s.js'></script>").html
    assert "script" not in out
    assert "alert" not in out
    assert out == "<p>ab</p>"


def test_drops_event_handlers():
    out = clean('<p onclick="steal()" onmouseover="x()">hi</p><img src="cid:a" onerror="x()">')
    assert "on" not in out.html.replace("noopener", "")
    assert "steal" not in out.html


def test_drops_javascript_and_relative_links_but_keeps_web_and_mail_links():
    out = clean(
        '<a href="javascript:alert(1)">js</a><a href="JaVaScRiPt:x">js2</a>'
        '<a href="/api/v1/mail">rel</a><a href="#top">anchor</a><a href="cid:x">cid</a>'
        '<a href="data:text/html,<script>alert(1)</script>">data</a>'
        '<a href="https://example.org/x" target="_self" rel="opener">web</a>'
        '<a href="mailto:a@example.org">mail</a>'
    ).html
    assert "javascript" not in out.lower()
    assert "/api" not in out
    assert "#top" not in out
    assert "cid:" not in out
    assert "data:" not in out
    assert (
        '<a href="https://example.org/x" target="_blank" rel="noopener noreferrer nofollow">web</a>'
    ) in out
    assert 'href="mailto:a@example.org"' in out
    assert "_self" not in out
    assert 'rel="opener"' not in out


def test_drops_forms_and_their_fields():
    out = clean(
        '<form action="https://evil.example/"><input name="p" type="password">'
        "<textarea>t</textarea><select><option>o</option></select><button>go</button></form>after"
    ).html
    for word in ("form", "input", "textarea", "select", "option", "button", "evil"):
        assert word not in out
    assert out == "after"


def test_drops_base_meta_refresh_and_the_head():
    out = clean(
        '<html><head><base href="https://evil.example/"><meta http-equiv="refresh" '
        'content="0;url=https://evil.example/"><title>T</title><link rel="stylesheet" '
        'href="https://evil.example/x.css"></head><body><base href="https://evil.example/">'
        '<meta http-equiv="refresh" content="0;url=https://evil.example/">body</body></html>'
    ).html
    assert out == "body"


def test_drops_frames_objects_svg_and_math_with_content():
    out = clean(
        '<iframe src="https://evil.example/">f</iframe><object data="x">o</object>'
        '<embed src="x"><svg><use href="https://x/#a"/><text>s</text></svg><math><mi>m</mi></math>'
        "<noscript>n</noscript><template>t</template><style>p{}</style>kept"
    ).html
    assert out == "kept"


def test_drops_links_and_images_of_svg_and_math_and_treats_an_img_there_as_any_other():
    out = clean(
        '<svg><a href="https://evil.example/">s</a><image href="https://x/s.png"/></svg>'
        '<math><a href="https://evil.example/">m</a></math>'
        '<svg><img src="https://x/i.png"></svg><math><img src="cid:a"></math>kept',
        images={"a": ("image/png", PNG)},
    )
    # An <img> ends the foreign element in an HTML parser, as in a browser: it is an ordinary
    # image after it, inlined or removed by the usual rules.
    data = base64.b64encode(PNG).decode()
    assert out.html == f'<img><img src="data:image/png;base64,{data}">kept'
    assert out.has_remote


def test_drops_url_expression_and_escapes_in_styles_but_keeps_layout():
    out = clean(
        '<p style="color: red; background-image: url(https://t.example/p.gif); '
        "background: URL('https://t.example/q.gif'); width: expression(alert(1)); "
        'font-size: 12px; position: fixed; z-index: 9999">a</p>'
        '<span style="color: b\\6c ue; background: u\\72l(https://t.example/e.gif)">b</span>'
        "<style>body{background:url(https://t.example/s.gif)}</style>"
    ).html
    assert "url" not in out.lower()
    assert "expression" not in out
    assert "t.example" not in out
    assert "position" not in out
    assert "\\" not in out
    assert 'style="color:red;font-size:12px"' in out.replace(" ", "")


def test_never_keeps_srcset_background_or_poster():
    for remote in (False, True):
        out = clean(
            '<img src="https://t.example/a.gif" srcset="https://t.example/2x.gif 2x">'
            '<table background="https://t.example/bg.gif"><tr><td>c</td></tr></table>'
            '<video poster="https://t.example/p.jpg"></video>',
            remote=remote,
        ).html
        assert "srcset" not in out
        assert "background" not in out
        assert "poster" not in out


def test_removes_remote_images_unless_asked_and_says_there_were_some():
    html = '<p>hi</p><img src="https://t.example/open.gif" alt="pixel">'
    blocked = clean(html)
    assert blocked.has_remote
    assert "t.example" not in blocked.html
    shown = clean(html, remote=True)
    assert shown.has_remote
    assert 'src="https://t.example/open.gif"' in shown.html
    assert not clean("<p>plain</p>").has_remote


def test_remote_means_web_images_only():
    out = clean(
        '<img src="ftp://t.example/a.gif"><img src="//t.example/b.gif"><img src="/local.gif">'
        '<img src="file:///etc/passwd"><img src="data:image/svg+xml;base64,PHN2Zz4=">',
        remote=True,
    ).html
    assert "src=" not in out


def test_inlines_cid_images_as_data_uris():
    out = clean(
        '<img src="cid:logo@example.org" alt="logo"><img src="CID:%3Clogo@example.org%3E">',
        images={"<logo@example.org>": ("image/png; name=logo.png", PNG)},
    )
    uri = f"data:image/png;base64,{base64.b64encode(PNG).decode()}"
    assert out.html == f'<img src="{uri}" alt="logo"><img src="{uri}">'
    assert not out.has_remote


def test_inlines_only_raster_images_and_only_up_to_the_cap():
    images = {
        "a": ("image/png", b"a" * 60),
        "b": ("image/gif", b"b" * 60),
        "svg": ("image/svg+xml", b"<svg onload='x()'/>"),
        "pdf": ("application/pdf", b"%PDF"),
    }
    out = clean(
        '<img src="cid:a"><img src="cid:a"><img src="cid:b"><img src="cid:svg">'
        '<img src="cid:pdf"><img src="cid:missing">',
        images=images,
        max_inline_bytes=100,
    ).html
    # Every reference costs the part's size, the same part's too: the second one passes the cap.
    assert out.count("data:image/png") == 1
    assert "image/gif" not in out
    assert "svg" not in out
    assert "pdf" not in out
    assert out.count("<img>") == 5


def test_inlined_output_stays_bounded_however_often_a_part_is_used():
    image = b"\x89PNG" + b"x" * 1000
    out = clean(
        '<img src="cid:a">' * 10_000, images={"a": ("image/png", image)}, max_inline_bytes=10_000
    ).html
    assert out.count("data:image/png") == 9
    assert len(out) < 20_000 + 10_000 * len("<img>")


def test_strips_urls_as_a_browser_does():
    out = clean(
        f'<a href="{chr(0xA0)}https://../../api/v1/x">nbsp</a><a href="{chr(0x3000)}https://../x">ideo</a>'
        '<a href=" \thttps://example.org/a">web</a><a href="java\tscript:x">js</a>'
    ).html
    assert "../" not in out
    assert "script" not in out
    assert 'href="https://example.org/a"' in out
    # lxml percent-escapes most of these before nh3 runs; the filter does not rely on that.
    from mailroom_core.render.safe_html import _Filter

    href = _Filter({}, remote=False, max_inline_bytes=0)
    for space in (0xA0, 0x3000, 0x2028):
        assert href("a", "href", f"{chr(space)}https://../../api") is None
    assert href("a", "href", " \t\x00https://example.org/a\r\n") == "https://example.org/a"
    assert href("a", "href", "ht\ntps://example.org/") == "https://example.org/"


def test_keeps_the_message_s_own_raster_data_images():
    out = clean('<img src="data:image/png;base64,iVBORw0KGgo=">').html
    assert out == '<img src="data:image/png;base64,iVBORw0KGgo=">'


def test_keeps_ordinary_formatting():
    html = (
        '<table width="600" cellpadding="0"><tr><td align="center" valign="top">'
        '<font face="Arial" color="#333">t</font></td></tr></table>'
        "<ul><li><b>b</b> <i>i</i></li></ul><blockquote>q</blockquote><pre>p</pre>"
    )
    out = clean(html).html
    for kept in ('width="600"', 'align="center"', 'face="Arial"', "<b>b</b>", "<blockquote>"):
        assert kept in out


def test_drops_comments_and_conditional_comments():
    out = clean("<!--[if mso]><script>x()</script><![endif]-->a<!-- hidden -->b").html
    assert out == "ab"


def test_is_linear_on_deep_nesting():
    html = f"<p>top</p>{'<div>' * 400_000}x{'</div>' * 400_000}"
    started = time.perf_counter()
    out = clean(html)
    assert time.perf_counter() - started < 1.0
    assert out.html.startswith("<p>top</p>")


def test_stays_fast_on_a_wide_flat_body():
    html = '<p class="c" style="color:red">x</p><a href="https://x/">l</a><br>' * 100_000
    started = time.perf_counter()
    out = clean(html)
    assert time.perf_counter() - started < 10.0
    assert out.html.count("<p") == 100_000


def test_empty_input():
    assert clean("").html == ""
    assert clean("   ").html == ""
