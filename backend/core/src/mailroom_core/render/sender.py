"""Whether a From display name names someone else (`sender_mismatch`): "PayPal <x@evil.example>"
is fine, "service@paypal.com <x@evil.example>" is not.

Limits: names are compared after NFKC and case folding, which folds fullwidth and compatibility
forms but not confusables across scripts (a Cyrillic a for a Latin one). A lookalike in a name
still differs from the real address, so it warns; a lookalike real address under a name with
no address or domain in it ("PayPal <x@paypa1.example>") does not. A dotted word is a domain only
when its last label is a real top-level domain (`tlds.TLDS`): "Node.js Weekly" names none, but
"Hello.World" does, a false warning. A domain is related to the real one only as its parent or
child, with no public-suffix list: "Amazon.co.uk" from amazon.com warns, being another registrable
domain."""

import re
import unicodedata
from typing import Final

from mailroom_core.render.sanitize import clean_header
from mailroom_core.render.tlds import TLDS

MAX_NAME: Final = 320
"""Of the display name, in UTF-16 units: a longer one is cut there before it is looked at."""

_ADDRESS_LIKE: Final = re.compile(r"[^\s@<>()\[\]\"',;:]+@[^\s@<>()\[\]\"',;:]+")
_LABEL: Final = r"[^\W_](?:[\w-]{0,61}[^\W_])?"
_TOP: Final = r"(?:[^\W\d_]{2,63}|xn--[\w-]+)"
_DOMAIN_LIKE: Final = re.compile(rf"(?<![\w.-])(?:{_LABEL}\.)+{_TOP}(?![\w-])")


def _ascii(domain: str) -> str:
    """A domain's IDNA form (`xn--…`), so that a name in Unicode matches a punycode address; the
    text itself when it has none."""
    domain = domain.strip(".")
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return domain


def _related(named: str, real: str) -> bool:
    """`named` is `real`, or a parent or child of it: `paypal.com` for `mail.paypal.com`."""
    return named == real or real.endswith(f".{named}") or named.endswith(f".{real}")


def sender_mismatch(name: str | None, address: str | None) -> bool:
    """Whether the display `name` holds an address other than `address`, or a domain neither
    `address`'s domain nor a parent or child of it. Both are the sender's words: the caller
    shows the real address, never the name."""
    if not name or not address or "@" not in address:
        return False
    real = unicodedata.normalize("NFKC", address).casefold().strip()
    real_domain = _ascii(real.rsplit("@", 1)[1])
    text = unicodedata.normalize("NFKC", clean_header(name, MAX_NAME)).casefold()
    for found in _ADDRESS_LIKE.findall(text):
        local, _, domain = found.rpartition("@")
        if f"{local}@{_ascii(domain)}" != f"{real.rsplit('@', 1)[0]}@{real_domain}":
            return True
    rest = _ADDRESS_LIKE.sub(" ", text)
    return any(
        not _related(_ascii(found), real_domain)
        for found in _DOMAIN_LIKE.findall(rest)
        if _ascii(found.rpartition(".")[2]) in TLDS
    )
