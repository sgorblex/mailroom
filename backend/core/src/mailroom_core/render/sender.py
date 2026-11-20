"""Whether a From display name names someone else (`sender_mismatch`): "PayPal <x@evil.example>"
is fine, "service@paypal.com <x@evil.example>" is not.

Limits: names are compared after NFKC, then case folding for local parts and a browser's IDNA
mapping for domains (`render.hosts`), which folds fullwidth and compatibility forms but not
confusables across scripts (a Cyrillic a for a Latin one). A lookalike in a name still differs from
the real address, so it warns; a lookalike real address under a name with no address or domain in it
("PayPal <x@paypa1.example>") does not. A dotted word is a domain only when its last label is a
real top-level domain (`tlds.TLDS`): "Node.js Weekly" names none, but "Hello.World" does, a false
warning. A domain is related to the real one only as its parent or child, with no public-suffix
list: "Amazon.co.uk" from amazon.com warns, being another registrable domain."""

import re
import unicodedata
from typing import Final

from mailroom_core.render.hosts import DOMAIN_LIKE, ascii_host, related
from mailroom_core.render.sanitize import clean_header
from mailroom_core.render.tlds import TLDS

MAX_NAME: Final = 320
"""Of the display name, in UTF-16 units: a longer one is cut there before it is looked at."""

_ADDRESS_LIKE: Final = re.compile(r"[^\s@<>()\[\]\"',;:]+@[^\s@<>()\[\]\"',;:]+")


def sender_mismatch(name: str | None, address: str | None) -> bool:
    """Whether the display `name` holds an address other than `address`, or a domain neither
    `address`'s domain nor a parent or child of it. Both are the sender's words: the caller
    shows the real address, never the name."""
    if not name or not address or "@" not in address:
        return False
    real = _address(unicodedata.normalize("NFKC", address).strip())
    text = unicodedata.normalize("NFKC", clean_header(name, MAX_NAME))
    if any(_address(found) != real for found in _ADDRESS_LIKE.findall(text)):
        return True
    real_domain = real[1]
    return any(
        real_domain is None
        or (named := ascii_host(found)) is None
        or not related(named, real_domain)
        for found in DOMAIN_LIKE.findall(_ADDRESS_LIKE.sub(" ", text))
        if (ascii_host(found.rpartition(".")[2]) or "") in TLDS
    )


def _address(text: str) -> tuple[str, str | None]:
    """An address's local part, case folded, and its domain's IDNA form."""
    local, _, domain = text.rpartition("@")
    return local.casefold(), ascii_host(domain)
