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
        '<a href="https://example.org/x" title="example.org" target="_blank" '
        'rel="noopener noreferrer nofollow">web</a>'
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
    assert out.remote_images == 1


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
    assert blocked.remote_images == 1
    assert "t.example" not in blocked.html
    shown = clean(html, remote=True)
    assert shown.remote_images == 1
    assert 'src="https://t.example/open.gif"' in shown.html
    assert clean("<p>plain</p>").remote_images == 0


def test_counts_every_remote_image_it_blocks():
    out = clean(
        '<img src=" https://t.example/a.gif" srcset="https://t.example/1x.gif 1x,'
        'HTTP://t.example/2x.gif 2x, /local.gif 3x">'
        '<table background="https://t.example/bg.gif"><tr><td background="cid:x">c</td></tr></table>'
        "<p style=\"background: url( 'https://t.example/p.gif'); color: red\">p</p>"
        "<div style='background-image:URL(http://t.example/d.gif), url(cid:y)'>d</div>"
        '<style>body{background:url(https://t.example/s.gif)} p{background:url("https://t.example/q.gif")}</style>'
        '<img src="cid:z"><img src="data:image/png;base64,iVBORw0KGgo="><img src="//t.example/r.gif">'
    )
    # src, two srcset candidates, the table's background, one url() in each style attribute, two
    # in the <style> element. Scheme-relative and local sources never load, so they are not counted.
    assert out.remote_images == 8
    assert out.loadable_remote_images == 1
    assert "t.example" not in out.html


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
    assert out.remote_images == 0


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


def link(html: str) -> str:
    return clean(html).html


def test_a_link_s_title_is_the_host_it_goes_to_never_the_sender_s():
    out = link(
        '<a href="https://www.Example.org/path?q=1" title="Your bank">Example</a>'
        '<a href="mailto:Someone@Example.org?subject=hi" title="x">write</a>'
        '<a href="https://user:pw@example.net:8443/">port</a>'
        '<a href="javascript:x()" title="kept?">js</a><a title="no href">plain</a>'
        '<p title="paragraphs keep theirs">p</p>'
    )
    assert 'title="www.example.org"' in out
    assert 'title="example.org"' in out
    assert 'title="example.net"' in out
    assert "Your bank" not in out
    assert "kept?" not in out
    assert "no href" not in out
    assert 'title="paragraphs keep theirs"' in out


def test_a_link_s_title_is_its_host_in_idna():
    out = link(
        '<a href="https://bücher.example/">books</a><a href="https://xn--pypal-4ve.com/">p</a>'
    )
    assert 'title="xn--bcher-kva.example"' in out
    assert 'title="xn--pypal-4ve.com"' in out


def test_a_backslash_ends_the_host_as_in_a_browser():
    out = link('<a href="https://evil.example\\@paypal.com/">paypal.com</a>')
    assert 'href="https://evil.example/@paypal.com/"' in out
    assert 'title="evil.example"' in out
    assert "⚠ evil.example" in out


def test_marks_a_link_whose_text_names_another_host():
    out = link(
        '<p>Log in at <a href="https://evil.example/login">https://www.paypal.com/login</a> now</p>'
    )
    assert out.startswith(
        '<p>Log in at <a href="https://evil.example/login" title="evil.example" target="_blank" '
        'rel="noopener noreferrer nofollow">https://www.paypal.com/login</a><span style="'
    )
    assert out.endswith('">⚠ evil.example</span> now</p>')


def test_does_not_mark_the_same_host_a_parent_or_a_child():
    out = link(
        '<a href="https://www.paypal.com/x">paypal.com</a>'
        '<a href="https://paypal.com/">www.PayPal.com</a>'
        '<a href="https://example.org/">Read more</a>'
        '<a href="mailto:help@example.org">help@example.org</a>'
        '<a href="https://xn--bcher-kva.example/">bücher.example</a>'
        '<a href="https://example.org/"><img src="cid:x" alt="evil.example"></a>'
    )
    assert "⚠" not in out


def test_marks_what_hides_another_host_in_the_text():
    for text in (
        "\uff50\uff41\uff59\uff50\uff41\uff4c.\uff43\uff4f\uff4d",  # fullwidth, folded by NFKC
        "PAYPAL.COM",
        "pay\u200bpal.com",  # a zero-width space, removed before looking
        "<b>pay</b>pal.com",
        "support@paypal.com",
        "evil.example.paypal.com",  # a child of the text's host, not of the link's
    ):
        out = link(f'<a href="https://evil.example/">{text}</a>')
        assert "⚠ evil.example</span>" in out, text


def test_marks_a_link_with_no_host_that_names_one():
    out = link('<a href="https:///nowhere">paypal.com</a>')
    assert ">⚠</span>" in out


def test_a_marker_cannot_be_hidden_by_the_link_s_own_style():
    out = link(
        '<div style="font-size:0;color:#fff;letter-spacing:-20px;font-family:Wingdings" dir="rtl">'
        '<a href="https://evil.example/" style="font-size:0">paypal.com</a></div>'
    )
    for declaration in (
        "font-size:12px",
        "color:#b00020",
        "letter-spacing:normal",
        "font-family:sans-serif",
        "direction:ltr",
    ):
        assert declaration in out
    assert 'dir="ltr">⚠ evil.example</span>' in out


def test_a_host_is_read_as_a_browser_reads_it():
    # UTS #46, non-transitional: sharp s and final sigma stay, so these hosts are not folded.
    out = link('<a href="https://busineß.com/">business.com</a>')
    assert 'title="xn--busine-gta.com"' in out
    assert "⚠ xn--busine-gta.com" in out
    assert 'title="xn--3xab.example"' in link('<a href="https://σς.example/">x</a>')
    # A joiner out of place, or text no browser takes as a host: no title, a bare marker.
    joined = link('<a href="https://pay\u200dpal.example/">paypal.com</a>')
    assert "title=" not in joined
    assert ">⚠</span>" in joined


def test_a_mailto_link_s_host_is_never_text_the_sender_wrote():
    out = link(
        '<a href="mailto:x@evil.example%3E%20(verified%20paypal.com)">support@paypal.com</a>'
    )
    assert "title=" not in out
    assert "verified" not in out.split("</a>")[1]
    assert ">⚠</span>" in out


def test_a_title_cannot_come_through_markup_lxml_reads_as_text():
    # lxml writes <style> and <script> content out raw: in a foreign element, html5ever would
    # read it as markup, and a <p> there would break out of the <svg> with the link after it.
    out = link(
        '<svg><style><p><a href="https://evil.example/" title="paypal.com">paypal.com</a>'
        "</p></style></svg>"
        '<math><script><p><a href="https://evil.example/" title="paypal.com">x</a></p></script></math>'
    )
    assert 'title="paypal.com"' not in out
    from mailroom_core.render.safe_html import _Filter

    title = _Filter({}, remote=False, max_inline_bytes=0)
    assert title("a", "title", "paypal.com") is None
    assert title("a", "title", f"{title.title_prefix}evil.example") == "evil.example"


def test_marks_links_on_a_wide_body_quickly():
    html = '<a href="https://evil.example/">paypal.com</a> ' * 50_000
    started = time.perf_counter()
    out = link(html)
    assert time.perf_counter() - started < 10.0
    assert out.count("⚠ evil.example") == 50_000


def test_reads_only_the_start_of_a_link_s_text():
    out = link(f'<a href="https://example.org/">{"word " * 400_000}paypal.com</a>')
    assert "⚠" not in out


def test_reports_whether_the_kept_html_sets_its_own_colours():
    assert not clean(
        "<p>plain <b>text</b> <a href='https://example.org/'>link</a></p>"
    ).sets_colours
    for coloured in (
        '<table bgcolor="#000"><tr><td>a</td></tr></table>',
        '<font color="red">a</font>',
        '<div style="background-color: #222">a</div>',
        '<p style="font-size:12px; COLOR:#333">a</p>',
    ):
        assert clean(coloured).sets_colours, coloured


def test_colours_that_are_dropped_or_set_nothing_do_not_count():
    for plain in (
        "<style>body{background:#000;color:#fff}</style><p>a</p>",
        '<body bgcolor="#000"><p>a</p></body>',
        '<div style="background:#000">shorthand, dropped</div>',
        '<div style="background-color:transparent;color:inherit;border-color:red">a</div>',
        '<p style="color:url(https://t.example/x)">a</p>',
    ):
        assert not clean(plain).sets_colours, plain


def test_the_link_marker_is_not_the_senders_colours():
    out = clean('<a href="https://evil.example/">https://bank.example/</a>')
    assert "evil.example" in out.html
    assert "background-color" in out.html
    assert not out.sets_colours
