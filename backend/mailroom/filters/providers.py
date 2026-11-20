"""Public mail providers: domains whose addresses belong to anyone who signs up, so that a filter
on the whole domain (``from_domain``, or ``from_contains`` / ``from_matches`` on it) would catch
strangers and friends alike. Drafts never offer
one and a saved spec is refused one (``mailroom.filters.service.checked_spec``)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from mailroom_core.render import ascii_host

if TYPE_CHECKING:
    from collections.abc import Iterator

PUBLIC_DOMAINS: Final = frozenset(
    {
        "126.com",
        "163.com",
        "aim.com",
        "alice.it",
        "aol.com",
        "email.it",
        "fastmail.com",
        "fastmail.fm",
        "free.fr",
        "gmail.com",
        "googlemail.com",
        "hey.com",
        "icloud.com",
        "inwind.it",
        "laposte.net",
        "libero.it",
        "mac.com",
        "mail.com",
        "mail.ru",
        "me.com",
        "msn.com",
        "naver.com",
        "orange.fr",
        "pm.me",
        "proton.me",
        "protonmail.ch",
        "protonmail.com",
        "qq.com",
        "rambler.ru",
        "seznam.cz",
        "t-online.de",
        "tim.it",
        "tiscali.it",
        "tuta.io",
        "tutanota.com",
        "tutanota.de",
        "virgilio.it",
        "wanadoo.fr",
        "web.de",
        "ymail.com",
        "zoho.com",
        "zohomail.com",
    }
)
"""Providers known by one domain (or a few), each matched with its subdomains."""

PUBLIC_NAMES: Final = frozenset({"gmx", "hotmail", "live", "outlook", "yahoo", "yandex"})
"""Providers with a domain per country (``hotmail.it``, ``yahoo.co.uk``): the name under any
country or generic suffix of one or two short labels."""

_SUFFIX: Final = re.compile(r"[a-z]{2,3}(?:\.[a-z]{2,3})?")
"""What follows a ``PUBLIC_NAMES`` name: ``com``, ``it``, ``co.uk``, ``com.br``."""

_PATTERN: Final = re.compile(r"(?:\^|\\A)?(?:\.[*+])?(.*?)(?:\$|\\[zZ])?", re.DOTALL)
"""A ``from_matches`` pattern around what it matches: anchors and a leading ``.*`` left out."""

_ESCAPED: Final = re.compile(r"\\([.@-])")
"""A backslash before punctuation a domain holds."""

_SHARED_SUFFIX: Final = re.compile(r"(?:ac|co|com|edu|gob|gov|ne|net|or|org)\.[a-z]{2}")
"""A country's shared second level (``co.uk``, ``com.br``), under which anyone's domain sits."""


def _suffixes(domain: str) -> Iterator[str]:
    """``domain`` and each parent of it with at least two labels."""
    labels = domain.split(".")
    for start in range(len(labels) - 1):
        yield ".".join(labels[start:])


def public_provider(domain: str) -> bool:
    """Whether ``domain`` (any case, Unicode or ``xn--``) is a public mail provider's, or a
    subdomain of one. A domain that is no host name is not."""
    host = ascii_host(domain.strip())
    if host is None:
        return False
    for suffix in _suffixes(host):
        if suffix in PUBLIC_DOMAINS:
            return True
        name, _, rest = suffix.partition(".")
        if name in PUBLIC_NAMES and _SUFFIX.fullmatch(rest):
            return True
    return False


def covers_public_provider(domain: str, *, subdomains: bool) -> bool:
    """Whether a ``from_domain`` on ``domain`` catches a public mail provider's mail: the domain is
    one (``public_provider``) or, with ``subdomains``, a country's shared second level that holds
    some (``co.uk``, ``com.br``)."""
    if public_provider(domain):
        return True
    host = ascii_host(domain.strip()) if subdomains else None
    return host is not None and _SHARED_SUFFIX.fullmatch(host) is not None


def names_public_provider(value: str, *, pattern: bool) -> bool:
    """Whether a ``from_contains`` text or, with ``pattern``, a ``from_matches`` pattern names a
    public mail provider's whole domain: it is one (``public_provider``) or a subdomain of one,
    with or without a leading ``@``. A pattern is read as plain text, its anchors, a leading
    ``.*`` and the backslashes before ``.``, ``@`` and ``-`` left out."""
    if pattern:
        match = _PATTERN.fullmatch(value)
        value = _ESCAPED.sub(r"\1", match.group(1) if match else value)
    return public_provider(value.strip().removeprefix("@"))
