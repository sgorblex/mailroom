"""The From display-name check (`render.sender_mismatch`): a name may say anything, but an
address or domain in it must be the real one's."""

import pytest

from mailroom_core.render import sender_mismatch


def fullwidth(text: str) -> str:
    """`text` in fullwidth forms, which NFKC folds back."""
    return "".join(chr(ord(c) + 0xFEE0) for c in text)


@pytest.mark.parametrize(
    ("name", "address"),
    [
        (None, "x@evil.example"),
        ("", "x@evil.example"),
        ("PayPal", "x@evil.example"),
        ("PayPal Support Team", "service@paypal.com"),
        ("john@x.org", "JOHN@X.ORG"),
        ("John Smith (john@x.org)", "john@x.org"),
        ("paypal.com", "no-reply@mail.paypal.com"),
        ("Mail.PayPal.com", "x@paypal.com"),
        ("J.R.R. Tolkien", "jrr@example.org"),
        ("münchen.de", "info@xn--mnchen-3ya.de"),
        (fullwidth("paypal.com"), "x@paypal.com"),
        # A dotted word names a domain only when it ends in a real top-level domain.
        ("Node.js Weekly", "news@cooperpress.com"),
        ("Vue.js News", "x@evil.example"),
        ("Mr.Robot", "x@evil.example"),
        ("St.Louis Office", "office@example.org"),
        ("PayPal", None),
        ("PayPal", "not-an-address"),
    ],
)
def test_a_name_naming_no_one_else_is_fine(name: str | None, address: str | None) -> None:
    assert not sender_mismatch(name, address)


@pytest.mark.parametrize(
    ("name", "address"),
    [
        ("service@paypal.com", "x@evil.example"),
        ("support@paypal.com", "noreply@paypal.com"),
        ("PayPal (paypal.com)", "x@evil.example"),
        ("paypal.com.evil.net", "x@paypal.com"),
        ("john@x.org <john@x.org>", "john@y.org"),
        # Fullwidth letters and an at sign fold to ASCII (NFKC).
        (fullwidth("paypal.com"), "x@evil.example"),
        (fullwidth("a@") + "paypal.com", "x@evil.example"),
        # An invisible character cannot hide a domain.
        ("pay\u200bpal.com", "x@evil.example"),
        # A cross-script lookalike is not folded: it differs from the real domain, so it warns.
        ("p" + chr(0x430) + "ypal.com", "x@paypal.com"),
        # Internationalised top-level domains count, in Unicode or in their IDNA form.
        ("例子.中国", "x@evil.example"),
        ("shop.xn--fiqs8s", "x@evil.example"),
        # Another registrable domain, however alike: no public-suffix list says otherwise.
        ("Amazon.co.uk", "x@amazon.com"),
        # A dotted word ending in a real top-level domain reads as one: a false warning.
        ("Hello.World", "office@example.org"),
    ],
)
def test_a_name_naming_another_address_or_domain_warns(name: str, address: str) -> None:
    assert sender_mismatch(name, address)


def test_a_lookalike_real_address_under_a_plain_name_is_not_caught() -> None:
    # The documented limit: only what the name says is compared.
    assert not sender_mismatch("PayPal", "x@paypa1.example")


def test_a_long_name_is_looked_at_only_up_to_the_cap() -> None:
    assert not sender_mismatch("a" * 400 + " paypal.com", "x@evil.example")
