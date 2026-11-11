"""Addresses from tool input and from IMAP (`src/mail/recipients.ts`): every address that reaches
a header or an SMTP envelope has passed `deliverable_address`, or comes from the account's own
identities."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mailroom_core.mail._mime import addresses
from mailroom_core.mail.errors import MailError
from mailroom_core.render import clean_header, clean_text
from mailroom_core.render._js import WHITESPACE

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class Mailbox:
    """An address with an optional display name."""

    address: str
    name: str | None = None


_ATOM: Final = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_LOCAL: Final = re.compile(rf"{_ATOM}(?:\.{_ATOM})*")
"""An ASCII local part (RFC 5322 atoms joined by dots), no quoting: what every server takes."""
_LABEL: Final = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN: Final = re.compile(rf"(?:{_LABEL}\.)+[a-z][a-z0-9-]{{0,61}}[a-z0-9]")
"""An ASCII domain of dotted labels, after punycoding."""
_SPACE: Final = f"[{re.escape(WHITESPACE)}]"
_NAMED: Final = re.compile(rf"(.*?){_SPACE}*<([^<>]*)>")
"""`Name <address>`: the name may not hold anything that reads as an address or a second entry."""
_QUOTED: Final = re.compile(r'\A"(.*)"\Z')
_LINE_BREAK: Final = re.compile("[\r\n\u2028\u2029]")
_NOT_IN_A_NAME: Final = re.compile(r'[@<>"]')
_MAX_ENTRY: Final = 1024
"""Longer than any address with a name; `_NAMED` would take quadratic time over a very long one."""
_MAX_ADDRESS: Final = 254
"""The longest address SMTP can carry. A header word is never split, so a longer one would pass
the 998 characters of a line, and fail only at SMTP, after the send was claimed."""
_REMAPPED: Final = re.compile("[ßς]")


def _ascii_domain(domain: str) -> str:
    """`domain` lowercased and punycoded, `""` when it has no such form. Node keeps ß and ς; the
    standard library's IDNA 2003 turns them into other letters, so into another domain, and such
    a domain is refused rather than sent to."""
    lower = domain.lower()
    if _REMAPPED.search(lower):
        return ""
    try:
        return lower.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def _not_an_address(text: str) -> MailError:
    return MailError("invalid_argument", f'Not an email address: "{clean_header(text, 100)}".')


def deliverable_address(text: str) -> str:
    """One address in the form every hop agrees on: exactly one `@`, a plain local part, the
    domain lowercased and punycoded (so header and envelope say the same). `invalid_argument` on
    anything else, including invisible characters, which could make two addresses look alike."""
    if clean_text(text).removed > 0 or text.count("@") != 1:
        raise _not_an_address(text)
    local, _, domain = text.partition("@")
    ascii_domain = _ascii_domain(domain)
    address = f"{local.lower()}@{ascii_domain}"
    if (
        not _LOCAL.fullmatch(local)
        or not _DOMAIN.fullmatch(ascii_domain)
        or len(address) > _MAX_ADDRESS
    ):
        raise _not_an_address(text)
    return address


def _recipient(value: str) -> Mailbox:
    if len(value) > _MAX_ENTRY or _LINE_BREAK.search(value):
        raise _not_an_address(value)
    trimmed = value.strip(WHITESPACE)
    named = _NAMED.fullmatch(trimmed)
    raw_name = "" if named is None else _QUOTED.sub(r"\1", named.group(1)).strip(WHITESPACE)
    if _NOT_IN_A_NAME.search(raw_name):
        raise _not_an_address(value)
    address = deliverable_address(trimmed if named is None else named.group(2).strip(WHITESPACE))
    return Mailbox(address, clean_header(raw_name, 100) or None)


def recipient_list(values: Iterable[str] = ()) -> tuple[Mailbox, ...]:
    """Recipients from tool input, one per entry: `ana@example.org` or `Ana <ana@example.org>`.
    Refuses the whole call, naming the entry, on a line break, several addresses in one entry, or
    a display name holding `@` (`"boss@corp.com" <x@evil.com>` shows as the boss in mail
    clients)."""
    return tuple(_recipient(value) for value in values)


def check_recipient_count(recipients: Sequence[Mailbox], limit: int) -> None:
    """Refuses a message addressed to more than `limit` recipients in all, or to none."""
    if not recipients:
        raise MailError("invalid_argument", "Name at least one recipient.")
    if len(recipients) > limit:
        raise MailError(
            "invalid_argument",
            f"{len(recipients)} recipients is over the limit of {limit} per message.",
        )


def matches_rule(address: str, rules: Iterable[str]) -> bool:
    """The address matches one of `auto_approve`'s rules: an exact address, or `@domain` for
    exactly that domain (not its subdomains). The address must already be deliverable."""
    lower = address.lower()
    domain = lower.rpartition("@")[2]
    wanted = [rule.lower() for rule in rules]
    return any(
        domain == _ascii_domain(rule[1:]) if rule.startswith("@") else lower == rule
        for rule in wanted
    )


def envelope_mailboxes(raw: object) -> tuple[Mailbox, ...]:
    """An ENVELOPE address list (imapclient `Address`es) lowercased, not validated, entries
    without a local part or a domain skipped."""
    mailboxes = []
    for entry in addresses(raw):
        address = (entry.address or "").lower()
        local, _, domain = address.rpartition("@")
        if local and domain:
            mailboxes.append(Mailbox(address, entry.name))
    return tuple(mailboxes)


def mailbox_text(mailbox: Mailbox) -> str:
    """`Name <addr>` or `addr`, as results and quotes show a mailbox."""
    return mailbox.address if mailbox.name is None else f"{mailbox.name} <{mailbox.address}>"
