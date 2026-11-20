"""The list, authentication and delivery fields a read parses (`mail.message_headers`), against
headers a sender writes to mislead: huge, many, folded, encoded, commented."""

from mailroom_core.mail import (
    NOT_VERIFIED,
    Authentication,
    AuthResult,
    AuthResults,
    DkimSignature,
    ListInfo,
    MessageHeaders,
    authentication,
    parse_headers,
    trusted_results,
)
from mailroom_core.mail import message_headers as mh


def block(*fields: str) -> bytes:
    return ("\r\n".join(fields) + "\r\n\r\n").encode()


def auth(value: str) -> AuthResults:
    [results] = parse_headers(block(f"Authentication-Results: {value}")).authentication
    return results


def test_the_fetch_item_names_every_field_with_a_byte_cap() -> None:
    assert mh.FETCH_ITEM.startswith("BODY.PEEK[HEADER.FIELDS (FROM LIST-ID ")
    assert mh.FETCH_ITEM.endswith(f"DELIVERED-TO X-ORIGINAL-TO)]<0.{mh.MAX_BYTES}>")


def test_an_empty_block_has_nothing() -> None:
    assert parse_headers(b"") == MessageHeaders()
    assert parse_headers(b"\r\n") == MessageHeaders()


def test_a_list_message_gives_its_links_topmost_first() -> None:
    headers = parse_headers(
        block(
            "From: News <news@example.org>",
            'List-Id: "Weekly =?utf-8?q?n=C3=A9ws?=" <weekly.example.org>',
            "List-Unsubscribe: <mailto:leave@example.org?subject=unsubscribe>,",
            "  <https://example.org/u/abc\r\n def>",
            "List-Unsubscribe-Post: List-Unsubscribe=One-Click",
            "List-Archive: (the archive) <https://example.org/archive>",
            "List-Post: NO (posting not allowed)",
            "List-Help: <ftp://example.org/help>, <https://example.org/help>",
        )
    )
    assert headers.mailing_list == ListInfo(
        id="weekly.example.org",
        name="Weekly néws",
        archive="https://example.org/archive",
        post=None,
        help="https://example.org/help",
        unsubscribe_https="https://example.org/u/abcdef",
        unsubscribe_mailto="mailto:leave@example.org?subject=unsubscribe",
        one_click=True,
        unsubscribe_fields=1,
        unsubscribe_post_fields=1,
    )
    assert headers.from_fields == 1


def test_list_links_other_than_https_and_mailto_are_left_out() -> None:
    info = parse_headers(
        block(
            "List-Unsubscribe: <http://example.org/u>, <javascript:alert(1)>, "
            "<https://user:pw@example.org/u>, <https:///nohost>, <data:text/html,x>, "
            "<mailto:nobody>, <https://example.org/é>",
        )
    ).mailing_list
    assert info is not None
    assert (info.unsubscribe_https, info.unsubscribe_mailto) == (None, None)
    assert info.id is None
    assert info.unsubscribe_fields == 1


def test_overlong_links_and_ids_are_left_out() -> None:
    long_url = "https://example.org/" + "a" * mh.MAX_URL
    info = parse_headers(
        block(f"List-Unsubscribe: <{long_url}>", f"List-Id: <{'x' * 300}.example.org>")
    ).mailing_list
    assert info == ListInfo(unsubscribe_fields=1)


def test_one_click_needs_the_exact_value_and_fields_are_counted() -> None:
    info = parse_headers(
        block(
            "List-Unsubscribe: <https://example.org/a>",
            "List-Unsubscribe: <https://evil.example/b>",
            "List-Unsubscribe-Post: List-Unsubscribe=One-Click; extra",
        )
    ).mailing_list
    assert info is not None
    assert info.unsubscribe_https == "https://example.org/a"
    assert (info.one_click, info.unsubscribe_fields, info.unsubscribe_post_fields) == (
        False,
        2,
        1,
    )


def test_a_message_without_list_fields_has_no_list() -> None:
    assert parse_headers(block("From: a@example.org")).mailing_list is None


def test_authentication_results_with_comments_versions_and_quoted_values() -> None:
    results = auth(
        "mx.google.com (the receiver) 1;\r\n"
        "       dkim=pass (2048-bit key) header.i=@example.org header.s=s1 header.b=AbCd1234;\r\n"
        "       spf=pass (google.com: domain of b@example.org designates 1.2.3.4) "
        "smtp.mailfrom=b@example.org;\r\n"
        '       dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from="Example.ORG"'
    )
    assert results == AuthResults(
        "mx.google.com",
        (
            AuthResult("dkim", "pass", header_b="AbCd1234"),
            AuthResult("spf", "pass", smtp_mailfrom="example.org"),
            AuthResult("dmarc", "pass", header_from="example.org"),
        ),
    )


def test_authentication_results_reads_header_d_and_the_dkim_version() -> None:
    results = auth('Mail.Example.COM; dkim/1=FAIL reason="bad; sig" header.d=Example.org')
    assert results == AuthResults(
        "mail.example.com", (AuthResult("dkim", "fail", header_d="example.org"),)
    )


def test_a_comment_cannot_smuggle_a_result() -> None:
    results = auth("mx.example.net; spf=fail (dmarc=pass header.from=evil.example)")
    assert results.results == (AuthResult("spf", "fail"),)


def test_properties_outside_the_ptype_form_are_skipped() -> None:
    results = auth(
        "mx.fastmail.example; dkim=pass (2048-bit rsa key sha256) header.d=example.org "
        "header.i=@example.org header.b=AbCd header.a=rsa-sha256 header.s=s1 x-bits=2048; "
        "dmarc=pass action=none header.from=example.org"
    )
    assert results == AuthResults(
        "mx.fastmail.example",
        (
            AuthResult("dkim", "pass", header_d="example.org", header_b="AbCd"),
            AuthResult("dmarc", "pass", header_from="example.org"),
        ),
    )


def test_other_methods_are_skipped_and_none_has_no_results() -> None:
    assert auth("mx.example.net; arc=pass; iprev=pass policy.iprev=1.2.3.4").results == ()
    assert auth("mx.example.net; none") == AuthResults("mx.example.net")
    assert auth("mx.example.net; dkim=pass;") == AuthResults(
        "mx.example.net", (AuthResult("dkim", "pass"),)
    )


def test_a_malformed_field_keeps_its_place_but_shows_no_results() -> None:
    assert auth("mx.example.net; dkim=pass; garbage here") == AuthResults("mx.example.net")
    assert auth("mx.example.net; dkim=pass header.d") == AuthResults("mx.example.net")
    # The authserv-id before the first semicolon still names a field that cannot be read.
    assert auth("mx.example.net; dkim=pass (unclosed") == AuthResults("mx.example.net")
    assert auth('mx.example.net; dkim=pass header.d="unclosed') == AuthResults("mx.example.net")
    assert auth("mx.example.net (unclosed; dkim=pass") == AuthResults(None)
    assert auth("two words; dkim=pass") == AuthResults(None)
    assert auth("; dkim=pass") == AuthResults(None)
    assert auth("(" * 20 + "x" + ")" * 20 + " mx.example.net; dkim=pass") == AuthResults(None)
    # 8-bit text cannot be an authserv-id.
    assert auth("mx.éxample.net; dkim=pass") == AuthResults(None)


def test_caps_on_authentication_results() -> None:
    huge = "mx.example.net; " + "; ".join(["dkim=pass header.d=example.org"] * 400)
    assert auth(huge) == AuthResults("mx.example.net")
    many_props = "mx.example.net; dkim=pass " + " ".join(
        f"header.x{i}=v" for i in range(mh.MAX_PROPERTIES + 1)
    )
    assert auth(many_props) == AuthResults("mx.example.net")
    results = "; ".join(["spf=pass"] * (mh.MAX_RESULTS + 5))
    assert len(auth(f"mx.example.net; {results}").results) == mh.MAX_RESULTS
    fields = [f"Authentication-Results: mx{i}.example.net; spf=pass" for i in range(100)]
    parsed = parse_headers(block(*fields)).authentication
    assert len(parsed) == mh.MAX_AUTH_FIELDS
    assert parsed[0].authserv_id == "mx0.example.net"


def test_only_the_topmost_field_of_the_trusted_server_counts() -> None:
    headers = parse_headers(
        block(
            "Authentication-Results: other.example; dmarc=pass header.from=evil.example",
            "Authentication-Results: mx.google.com; dmarc=fail header.from=bank.example",
            "Authentication-Results: mx.google.com; dmarc=pass header.from=bank.example",
        )
    )
    trusted = trusted_results(headers, "MX.google.com ")
    assert trusted is not None
    assert trusted.results == (AuthResult("dmarc", "fail", header_from="bank.example"),)
    assert trusted_results(headers, None) is None
    assert trusted_results(headers, "") is None
    assert trusted_results(headers, "mx.example.net") is None


def test_a_malformed_trusted_field_is_not_skipped_for_a_forged_one_below() -> None:
    headers = parse_headers(
        block(
            "Authentication-Results: mx.google.com; dmarc=pass header.from=bank.example; ???",
            "Authentication-Results: mx.google.com; dmarc=pass header.from=bank.example",
        )
    )
    assert trusted_results(headers, "mx.google.com") == AuthResults("mx.google.com")


def test_an_unreadable_trusted_field_is_not_skipped_for_a_forged_one_below() -> None:
    forged = "Authentication-Results: mx.google.com; dmarc=pass header.from=bank.example"
    broken_values = parse_headers(
        block(
            "Authentication-Results: mx.google.com; spf=pass (domain of (a@evil.example "
            "designates 1.2.3.4) smtp.mailfrom=a@evil.example",
            forged,
        )
    )
    assert trusted_results(broken_values, "mx.google.com") == AuthResults("mx.google.com")
    broken_head = parse_headers(
        block("Authentication-Results: mx.google.com (unclosed; spf=pass", forged)
    )
    assert broken_head.authentication[0] == AuthResults(None)
    assert trusted_results(broken_head, "mx.google.com") is None


TRUSTED = "Authentication-Results: mx.google.com; spf=pass smtp.mailfrom=bank.example;"
TRUSTED_RESULTS = (
    " dkim=fail header.d=other.example header.b=AAAA;",
    " dkim=pass header.d=bank.example header.b=BBBB;",
    " dmarc=pass header.from=Bank.Example",
)


def test_the_verdict_is_the_topmost_field_of_the_accounts_server() -> None:
    headers = parse_headers(block("From: x@bank.example", TRUSTED, *TRUSTED_RESULTS))
    assert authentication(headers, ("x@BANK.example",), "mx.google.com") == Authentication(
        verified=True,
        spf="pass",
        spf_domain="bank.example",
        dkim="pass",
        dkim_domain="bank.example",
        dmarc="pass",
        dmarc_pass=True,
        from_domain="bank.example",
    )


def test_spf_and_dkim_name_the_domain_their_result_is_for() -> None:
    headers = parse_headers(
        block(
            "Authentication-Results: mx.google.com; spf=pass smtp.mailfrom=bounce@Evil.Example;",
            " dkim=fail header.d=bank.example; dkim=pass header.d=XN--bcher-kva.example;",
            " dmarc=fail header.from=bank.example",
        )
    )
    verdict = authentication(headers, ("x@bank.example",), "mx.google.com")
    assert (verdict.spf, verdict.spf_domain) == ("pass", "evil.example")
    assert (verdict.dkim, verdict.dkim_domain) == ("pass", "xn--bcher-kva.example")
    # A domain that is no host name is left out, never shown as written.
    odd = parse_headers(
        block(
            'Authentication-Results: mx.google.com; spf=fail smtp.mailfrom="a b@x y.example";',
            " dkim=fail header.d=bank.example/evil",
        )
    )
    verdict = authentication(odd, ("x@bank.example",), "mx.google.com")
    assert (verdict.spf, verdict.spf_domain, verdict.dkim, verdict.dkim_domain) == (
        "fail",
        None,
        "fail",
        None,
    )


def test_without_the_accounts_server_nothing_is_verified() -> None:
    headers = parse_headers(block("From: x@bank.example", TRUSTED, *TRUSTED_RESULTS))
    assert authentication(headers, ("x@bank.example",), None) == NOT_VERIFIED
    assert authentication(headers, ("x@bank.example",), "mx.example.net") == NOT_VERIFIED
    assert NOT_VERIFIED.verified is False
    assert (NOT_VERIFIED.spf, NOT_VERIFIED.dkim, NOT_VERIFIED.dmarc) == (None, None, None)


def test_a_forged_topmost_field_of_another_server_is_passed_over() -> None:
    headers = parse_headers(
        block(
            "From: x@bank.example",
            "Authentication-Results: mx.evil.example; spf=pass; dkim=pass header.d=bank.example;",
            " dmarc=pass header.from=bank.example",
            "Authentication-Results: mx.google.com; spf=fail; dkim=none; dmarc=fail"
            " header.from=bank.example",
        )
    )
    verdict = authentication(headers, ("x@bank.example",), "mx.google.com")
    assert (verdict.verified, verdict.spf, verdict.dkim, verdict.dmarc) == (
        True,
        "fail",
        "none",
        "fail",
    )
    assert not verdict.dmarc_pass
    # Forged with the trusted id but below the server's own field: the topmost one counts.
    forged_below = parse_headers(
        block(
            "From: x@bank.example",
            "Authentication-Results: mx.google.com; dmarc=fail header.from=bank.example",
            "Authentication-Results: mx.google.com; dmarc=pass header.from=bank.example",
        )
    )
    assert not authentication(forged_below, ("x@bank.example",), "mx.google.com").dmarc_pass
    # Forged with the trusted id where the server wrote none: not believed either.
    alone = parse_headers(
        block(
            "From: x@bank.example",
            "Authentication-Results: mx.evil.example; dmarc=pass header.from=bank.example",
        )
    )
    assert authentication(alone, ("x@bank.example",), "mx.google.com") == NOT_VERIFIED


def test_dmarc_passes_only_for_exactly_one_from_of_its_domain() -> None:
    single = parse_headers(block("From: x@bank.example", TRUSTED, *TRUSTED_RESULTS))
    other = authentication(single, ("x@evil.example",), "mx.google.com")
    assert (other.dmarc, other.dmarc_pass, other.from_domain) == ("pass", False, None)
    two = authentication(single, ("x@bank.example", "y@evil.example"), "mx.google.com")
    assert not two.dmarc_pass
    assert not authentication(single, (), "mx.google.com").dmarc_pass
    doubled = parse_headers(
        block("From: x@bank.example", "From: y@evil.example", TRUSTED, *TRUSTED_RESULTS)
    )
    assert not authentication(doubled, ("x@bank.example",), "mx.google.com").dmarc_pass
    whole = block(TRUSTED, *TRUSTED_RESULTS, "From: x@bank.example", "Delivered-To: a@b.example")
    cut = parse_headers(whole, cap=len(whole))
    assert cut.truncated
    assert (cut.from_fields, trusted_results(cut, "mx.google.com") is not None) == (1, True)
    assert not authentication(cut, ("x@bank.example",), "mx.google.com").dmarc_pass


def test_dmarc_compares_an_internationalised_domain_in_its_idna_form() -> None:
    headers = parse_headers(
        block(
            "From: x@bücher.example",
            "Authentication-Results: mx.google.com; dmarc=pass header.from=XN--bcher-kva.example",
        )
    )
    verdict = authentication(headers, ("x@BÜCHER.example",), "mx.google.com")
    assert (verdict.dmarc_pass, verdict.from_domain) == (True, "bücher.example")
    assert authentication(headers, ("x@xn--bcher-kva.example",), "mx.google.com").dmarc_pass
    assert not authentication(headers, ("x@bucher.example",), "mx.google.com").dmarc_pass


def test_dkim_is_the_first_result_when_no_signature_passed() -> None:
    headers = parse_headers(
        block(
            "Authentication-Results: mx.google.com; dkim=fail header.d=a.example;",
            " dkim=neutral header.d=b.example",
        )
    )
    verdict = authentication(headers, ("x@a.example",), "mx.google.com")
    assert (verdict.spf, verdict.dkim, verdict.dmarc) == (None, "fail", None)


def test_dkim_signatures_give_d_a_b_prefix_and_the_signed_fields() -> None:
    headers = parse_headers(
        block(
            "DKIM-Signature: v=1; a=rsa-sha256; c=relaxed/relaxed; d=Example.ORG; s=s1;",
            "\th=From:To:Subject : List-Unsubscribe:List-Unsubscribe-Post;",
            "\tbh=abc=; b=AbCd1234 efgh+/ij",
            "\t klmn==",
        )
    )
    assert headers.dkim_signatures == (
        DkimSignature(
            d="example.org",
            b="AbCd1234efgh+/ijklmn==",
            h=("from", "to", "subject", "list-unsubscribe", "list-unsubscribe-post"),
        ),
    )


def test_malformed_dkim_signatures_are_left_out() -> None:
    good = "v=1; d=example.org; h=from; b=AAAA"
    bad = [
        "v=1; d=example.org; d=evil.example; h=from; b=AAAA",  # duplicate tag
        "v=1; h=from; b=AAAA",  # no d=
        "v=1; d=example.org; b=AAAA",  # no h=
        "v=1; d=example.org; h=from; b=",  # empty b=
        "v=1; d=example.org; h=from; b=not base64!",
        "v=1; d=example.org; h=fr om; b=AAAA",
        "v=1; d=example.org; =x; h=from; b=AAAA",
        f"v=1; d=example.org; h={':'.join(['from'] * (mh.MAX_SIGNED_FIELDS + 1))}; b=AAAA",
        f"v=1; d=example.org; h=from; b={'A' * mh.MAX_SIGNATURE_BYTES}",
    ]
    headers = parse_headers(block(*(f"DKIM-Signature: {v}" for v in [*bad, good])))
    assert headers.dkim_signatures == (DkimSignature("example.org", "AAAA", ("from",)),)
    long_b = parse_headers(block(f"DKIM-Signature: d=a.example; h=from; b={'B' * 600}"))
    assert long_b.dkim_signatures[0].b == "B" * mh.MAX_SIGNATURE_PREFIX
    many = parse_headers(block(*[f"DKIM-Signature: {good}"] * 40))
    assert len(many.dkim_signatures) == mh.MAX_SIGNATURES


def test_from_fields_and_delivery_addresses_are_counted_in_order() -> None:
    headers = parse_headers(
        block(
            "Delivered-To: me@example.test",
            "From: a@example.org",
            "X-Original-To: alias@example.test",
            "from: b@evil.example",
            "Delivered-To: <outer@example.test>",
        )
    )
    assert headers.from_fields == 2
    assert headers.delivered_to == ("alias@example.test", "me@example.test", "outer@example.test")
    many = parse_headers(block(*[f"Delivered-To: u{i}@example.test" for i in range(50)]))
    assert len(many.delivered_to) == mh.MAX_DELIVERED


def test_a_block_at_the_cap_drops_its_last_field() -> None:
    fields = [f"Authentication-Results: mx{i}.example.net; spf=pass" for i in range(3)]
    raw = block(*fields)
    cut = raw[: raw.index(b"mx2") + 5]
    headers = parse_headers(cut, cap=len(cut))
    assert headers.truncated
    assert [r.authserv_id for r in headers.authentication] == ["mx0.example.net", "mx1.example.net"]
    # A folded field cut in its continuation is dropped whole.
    folded = b"From: a@example.org\r\nList-Unsubscribe: <https://example.org/a>,\r\n <https://ex"
    assert parse_headers(folded, cap=len(folded)) == MessageHeaders(from_fields=1, truncated=True)
    # Cut right after the line end that precedes a continuation: dropped whole as well.
    at_line_end = b"From: a@example.org\r\nList-Unsubscribe: <https://example.org/a>,\r\n"
    assert parse_headers(at_line_end, cap=len(at_line_end)) == MessageHeaders(
        from_fields=1, truncated=True
    )
    assert parse_headers(b"From: a@exa", cap=11) == MessageHeaders(truncated=True)
    assert not parse_headers(raw, cap=len(raw) + 1).truncated


def test_huge_and_hostile_blocks_parse_quickly_to_little() -> None:
    noise = b"".join(b"From: x\r\n" for _ in range(10_000))
    headers = parse_headers(noise)
    assert headers.truncated
    assert headers.from_fields < 10_000
    nested = "Authentication-Results: mx.example.net (" + "(" * 5000 + "; dkim=pass"
    assert parse_headers(block(nested)).authentication == (AuthResults(None),)
    quotes = 'Authentication-Results: mx.example.net; dkim=pass header.d="' + "\\" * 7000 + '"'
    # A domain that long is no domain.
    assert parse_headers(block(quotes)).authentication == (
        AuthResults("mx.example.net", (AuthResult("dkim", "pass"),)),
    )
    binary = b"List-Id: \xff\xfe <\xff.example>\r\nList-Unsubscribe: <https://\xc3.example/>\r\n"
    info = parse_headers(binary).mailing_list
    assert info == ListInfo(unsubscribe_fields=1)
