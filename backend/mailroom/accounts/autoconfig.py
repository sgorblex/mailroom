"""Suggested IMAP and SMTP settings for an address, looked up in this order: the domain's own
autoconfig documents (without the address), its DNS SRV records (RFC 6186), its MX matched
against known providers, Thunderbird's ISPDB (which learns only the domain), then the built-in
presets. Every fetch goes through ``safe_fetch``; nothing is stored but a short cache per domain.
Spec: ``docs/requirements/mail-client.md`` — *Account autoconfiguration*."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

import anyio
import dns.asyncresolver
import dns.exception
import dns.rdata
from dns.rdtypes.ANY.MX import MX
from dns.rdtypes.IN.SRV import SRV
from lxml import etree
from pydantic import BaseModel

from mailroom.auth.throttle import RateLimit
from mailroom.net.safe_fetch import FetchError, safe_fetch

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mailroom.net.safe_fetch import Fetched

TlsMode = Literal["implicit", "starttls"]
LoginForm = Literal["address", "local_part"]
"""What the login is: the whole address, or the part before the ``@``."""
Source = Literal["autoconfig", "srv", "mx", "ispdb", "preset"]
Note = Literal["app_password", "oauth_required"]
"""``app_password``: the provider wants a password made for the app, not the account's own.
``oauth_required``: it only lets in an OAuth2 sign-in, which Mailroom does not do yet."""

LOOKUPS_PER_MINUTE = 20
"""Per user."""
CACHE_SECONDS = 10 * 60
_CACHE_MAX = 1024
_LOOKUP_DEADLINE = 20.0
"""For every network step together; past it the presets answer, and nothing is cached."""
_DNS_LIFETIME = 3.0
_DOCUMENT_MAX_BYTES = 64 * 1024
ISPDB_URL = "https://autoconfig.thunderbird.net/v1.1/"

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_PASSWORD_AUTH = frozenset({"password-cleartext", "password-encrypted", "plain", "secure"})
_SOCKET_TYPES: dict[str, TlsMode] = {"SSL": "implicit", "TLS": "implicit", "STARTTLS": "starttls"}
_USERNAMES: dict[str, LoginForm] = {"%EMAILADDRESS%": "address", "%EMAILLOCALPART%": "local_part"}


class SuggestedEndpoint(BaseModel):
    host: str
    port: int
    tls: TlsMode
    outside_domain: bool = False
    """The host came from the domain's own autoconfig document or SRV records but is neither the
    domain nor under it, nor a known provider's host the suggestion is matched to: kept, but worth
    a look before it gets the password."""


class Suggestion(BaseModel):
    source: Source
    origin: str | None
    """Where it was read: the document's URL, the DNS names; none for a preset."""
    provider: str | None
    """A known provider the servers belong to."""
    supported: bool
    """False for a provider Mailroom cannot sign in to yet (``note`` says why)."""
    note: Note | None
    login: LoginForm
    imap: SuggestedEndpoint
    smtp: SuggestedEndpoint


class AutoconfigAnswer(BaseModel):
    domain: str
    suggestion: Suggestion | None
    """None when nothing answered."""


@dataclass(frozen=True, slots=True)
class Provider:
    name: str
    imap: SuggestedEndpoint
    smtp: SuggestedEndpoint
    note: Note | None
    domains: frozenset[str]
    """The domains its presets answer for."""
    imap_hosts: frozenset[str]
    """The IMAP hosts that name it in a suggestion from elsewhere."""
    smtp_hosts: frozenset[str]
    """Its SMTP hosts, which are not outside the domain in a suggestion named after it."""
    mx_suffixes: tuple[str, ...] = ()
    """MX hosts that say a domain's mail is with it."""
    supported: bool = True


def _endpoint(host: str, port: int, tls: TlsMode) -> SuggestedEndpoint:
    return SuggestedEndpoint(host=host, port=port, tls=tls)


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        "Gmail",
        _endpoint("imap.gmail.com", 993, "implicit"),
        _endpoint("smtp.gmail.com", 465, "implicit"),
        "app_password",
        frozenset({"gmail.com", "googlemail.com"}),
        frozenset({"imap.gmail.com", "imap.googlemail.com"}),
        frozenset({"smtp.gmail.com", "smtp.googlemail.com"}),
        ("google.com", "googlemail.com"),
    ),
    Provider(
        "iCloud",
        _endpoint("imap.mail.me.com", 993, "implicit"),
        _endpoint("smtp.mail.me.com", 587, "starttls"),
        "app_password",
        frozenset({"icloud.com", "me.com", "mac.com"}),
        frozenset({"imap.mail.me.com"}),
        frozenset({"smtp.mail.me.com"}),
    ),
    Provider(
        "Fastmail",
        _endpoint("imap.fastmail.com", 993, "implicit"),
        _endpoint("smtp.fastmail.com", 465, "implicit"),
        "app_password",
        frozenset({"fastmail.com", "fastmail.fm"}),
        frozenset({"imap.fastmail.com"}),
        frozenset({"smtp.fastmail.com"}),
    ),
    Provider(
        "Yahoo",
        _endpoint("imap.mail.yahoo.com", 993, "implicit"),
        _endpoint("smtp.mail.yahoo.com", 465, "implicit"),
        "app_password",
        frozenset(
            {"yahoo.com", "ymail.com", "rocketmail.com", "yahoo.co.uk", "yahoo.it", "yahoo.fr"}
        ),
        frozenset({"imap.mail.yahoo.com"}),
        frozenset({"smtp.mail.yahoo.com"}),
    ),
    Provider(
        "Microsoft",
        _endpoint("outlook.office365.com", 993, "implicit"),
        _endpoint("smtp.office365.com", 587, "starttls"),
        "oauth_required",
        frozenset({"outlook.com", "hotmail.com", "live.com", "msn.com", "hotmail.it"}),
        frozenset({"outlook.office365.com", "imap-mail.outlook.com"}),
        frozenset({"smtp.office365.com", "smtp-mail.outlook.com"}),
        ("protection.outlook.com",),
        supported=False,
    ),
)


@dataclass(frozen=True, slots=True)
class SrvRecord:
    priority: int
    weight: int
    port: int
    target: str
    """Without the final dot; empty when the service is not offered (RFC 2782's ``.``)."""


@dataclass(frozen=True, slots=True)
class MxRecord:
    preference: int
    host: str


class Dns(Protocol):
    """The DNS records a lookup reads; none when the name has none or does not answer."""

    async def srv(self, name: str) -> list[SrvRecord]: ...

    async def mx(self, domain: str) -> list[MxRecord]: ...


class SystemDns:
    """The system's resolvers, through dnspython, each query within ``lifetime`` seconds."""

    def __init__(self, lifetime: float = _DNS_LIFETIME) -> None:
        self._lifetime = lifetime

    async def srv(self, name: str) -> list[SrvRecord]:
        return [
            SrvRecord(r.priority, r.weight, r.port, r.target.to_text(omit_final_dot=True))
            for r in await self._query(name, "SRV")
            if isinstance(r, SRV)
        ]

    async def mx(self, domain: str) -> list[MxRecord]:
        return [
            MxRecord(r.preference, r.exchange.to_text(omit_final_dot=True))
            for r in await self._query(domain, "MX")
            if isinstance(r, MX)
        ]

    async def _query(self, name: str, rdtype: Literal["SRV", "MX"]) -> list[dns.rdata.Rdata]:
        try:
            resolver = dns.asyncresolver.Resolver()
            answer = await resolver.resolve(name, rdtype, lifetime=self._lifetime, search=False)
        except dns.exception.DNSException:
            return []
        return list(answer)


type Fetch = Callable[[str], Awaitable[Fetched]]


async def fetch_document(url: str) -> Fetched:
    """An autoconfig document at ``url``, a couple of redirects allowed."""
    return await safe_fetch(
        url,
        max_bytes=_DOCUMENT_MAX_BYTES,
        max_redirects=2,
        headers={"Accept": "application/xml, text/xml"},
        connect_timeout=3.0,
        read_timeout=5.0,
        deadline=8.0,
    )


def address_domain(address: str) -> str | None:
    """The domain of ``address``, lower case and IDNA-encoded; None when it is no address."""
    local, at, domain = address.strip().rpartition("@")
    if not at or not local:
        return None
    return hostname(domain)


def hostname(name: str) -> str | None:
    """``name`` as a DNS hostname of at least two labels (lower case, ASCII), or None."""
    try:
        ascii_name = name.removesuffix(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    labels = ascii_name.split(".")
    if len(ascii_name) > 253 or len(labels) < 2 or labels[-1].isdigit():
        return None
    if not all(_LABEL.fullmatch(label) for label in labels):
        return None
    return ascii_name


class Autoconfig:
    """The lookups, their cache per domain and the rate limit per user
    (``app.state.autoconfig``)."""

    def __init__(
        self,
        *,
        fetch: Fetch = fetch_document,
        dns_records: Dns | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._dns = SystemDns() if dns_records is None else dns_records
        self._clock = clock
        self._cache: dict[str, tuple[float, Suggestion | None]] = {}
        self.limit = RateLimit(LOOKUPS_PER_MINUTE, 60.0, clock)

    async def lookup(self, domain: str) -> Suggestion | None:
        """The suggestion for ``domain`` (as ``address_domain`` gives it), from the cache while
        it is fresh."""
        now = self._clock()
        cached = self._cache.get(domain)
        if cached is not None and cached[0] > now:
            return cached[1]
        with anyio.move_on_after(_LOOKUP_DEADLINE) as scope:
            found = await self._look_up(domain)
        if scope.cancelled_caught:
            return _preset(domain)
        self._remember(domain, found, now)
        return found

    def _remember(self, domain: str, suggestion: Suggestion | None, now: float) -> None:
        self._cache.pop(domain, None)
        if len(self._cache) >= _CACHE_MAX:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            while len(self._cache) >= _CACHE_MAX:
                del self._cache[next(iter(self._cache))]
        self._cache[domain] = (now + CACHE_SECONDS, suggestion)

    async def _look_up(self, domain: str) -> Suggestion | None:
        steps: tuple[Callable[[str], Awaitable[Suggestion | None]], ...] = (
            self._own_autoconfig,
            self._srv,
            self._mx,
            self._ispdb,
        )
        for step in steps:
            found = await step(domain)
            if found is not None:
                return _recognised(found)
        return _preset(domain)

    async def _own_autoconfig(self, domain: str) -> Suggestion | None:
        for url in (
            f"https://autoconfig.{domain}/mail/config-v1.1.xml",
            f"https://{domain}/.well-known/autoconfig/mail/config-v1.1.xml",
        ):
            found = await self._document(url, domain, "autoconfig")
            if found is not None:
                return found
        return None

    async def _ispdb(self, domain: str) -> Suggestion | None:
        return await self._document(f"{ISPDB_URL}{domain}", domain, "ispdb")

    async def _document(self, url: str, domain: str, source: Source) -> Suggestion | None:
        try:
            fetched = await self._fetch(url)
        except FetchError:
            return None
        if fetched.status_code != 200:
            return None
        servers = parse_config(fetched.content, domain)
        if servers is None:
            return None
        login, imap, smtp = servers
        if source == "autoconfig":
            imap, smtp = _flagged(imap, domain), _flagged(smtp, domain)
        return Suggestion(
            source=source,
            origin=url,
            provider=None,
            supported=True,
            note=None,
            login=login,
            imap=imap,
            smtp=smtp,
        )

    async def _srv(self, domain: str) -> Suggestion | None:
        imap = await self._service(domain, ("_imaps", "implicit"), ("_imap", "starttls"))
        if imap is None:
            return None
        smtp = await self._service(
            domain, ("_submissions", "implicit"), ("_submission", "starttls")
        )
        if smtp is None:
            return None
        return Suggestion(
            source="srv",
            origin=f"{imap[1]}, {smtp[1]}",
            provider=None,
            supported=True,
            note=None,
            login="address",
            imap=_flagged(imap[0], domain),
            smtp=_flagged(smtp[0], domain),
        )

    async def _service(
        self, domain: str, *services: tuple[str, TlsMode]
    ) -> tuple[SuggestedEndpoint, str] | None:
        """The first of ``services`` the domain offers, by RFC 6186's SRV records, and the name
        it was read at."""
        for service, tls in services:
            name = f"{service}._tcp.{domain}"
            offered = [
                record
                for record in await self._dns.srv(name)
                if record.target and 0 < record.port < 65536
            ]
            if not offered:
                continue
            best = min(offered, key=lambda record: (record.priority, -record.weight))
            host = hostname(best.target)
            if host is not None:
                return _endpoint(host, best.port, tls), name
        return None

    async def _mx(self, domain: str) -> Suggestion | None:
        records = sorted(await self._dns.mx(domain), key=lambda record: record.preference)
        for record in records:
            host = record.host.lower()
            for provider in PROVIDERS:
                if any(host == s or host.endswith(f".{s}") for s in provider.mx_suffixes):
                    return _from_provider(provider, "mx", host)
        return None


type Servers = tuple[LoginForm, SuggestedEndpoint, SuggestedEndpoint]


def parse_config(content: bytes, domain: str) -> Servers | None:
    """The IMAP and SMTP servers of an autoconfig document (``config-v1.1.xml``) that take a
    password and log in by the address or its local part, the first of each with the same login;
    None when it has none. No entity is expanded and nothing is fetched while parsing."""
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    try:
        root = etree.fromstring(content, parser)
    except etree.XMLSyntaxError:
        return None
    if root.tag != "clientConfig":
        return None
    imaps = [
        server
        for element in root.iterfind("emailProvider/incomingServer[@type='imap']")
        if (server := _server(element, domain)) is not None
    ]
    smtps = [
        server
        for element in root.iterfind("emailProvider/outgoingServer[@type='smtp']")
        if (server := _server(element, domain)) is not None
    ]
    for login, imap in imaps:
        for smtp_login, smtp in smtps:
            if smtp_login == login:
                return login, imap, smtp
    return None


def _server(element: etree._Element, domain: str) -> tuple[LoginForm, SuggestedEndpoint] | None:
    host = hostname(_text(element, "hostname").replace("%EMAILDOMAIN%", domain))
    tls = _SOCKET_TYPES.get(_text(element, "socketType").upper())
    login = _USERNAMES.get(_text(element, "username"))
    port = _text(element, "port")
    if host is None or tls is None or login is None or not port.isdigit():
        return None
    if not 0 < int(port) < 65536:
        return None
    methods = {(method.text or "").strip() for method in element.iterfind("authentication")}
    if methods and not methods & _PASSWORD_AUTH:
        return None
    return login, _endpoint(host, int(port), tls)


def _text(element: etree._Element, child: str) -> str:
    found = element.find(child)
    return "" if found is None or found.text is None else found.text.strip()


def _from_provider(provider: Provider, source: Source, origin: str | None) -> Suggestion:
    return Suggestion(
        source=source,
        origin=origin,
        provider=provider.name,
        supported=provider.supported,
        note=provider.note,
        login="address",
        imap=provider.imap,
        smtp=provider.smtp,
    )


def _within(host: str, domain: str) -> bool:
    """Whether ``host`` is ``domain`` or a name under it (both as ``hostname`` gives them)."""
    return host == domain or host.endswith(f".{domain}")


def _flagged(endpoint: SuggestedEndpoint, domain: str) -> SuggestedEndpoint:
    """``endpoint`` marked ``outside_domain`` when its host is not within ``domain``."""
    if _within(endpoint.host, domain):
        return endpoint
    return endpoint.model_copy(update={"outside_domain": True})


def _recognised(suggestion: Suggestion) -> Suggestion:
    """``suggestion`` with the provider its IMAP host belongs to, when it is a known one; that
    provider's own hosts are then not outside the domain."""
    for provider in PROVIDERS:
        if suggestion.imap.host in provider.imap_hosts:
            known = provider.imap_hosts | provider.smtp_hosts
            return suggestion.model_copy(
                update={
                    "provider": provider.name,
                    "supported": provider.supported,
                    "note": provider.note,
                    "imap": _known(suggestion.imap, known),
                    "smtp": _known(suggestion.smtp, known),
                }
            )
    return suggestion


def _known(endpoint: SuggestedEndpoint, hosts: frozenset[str]) -> SuggestedEndpoint:
    if endpoint.outside_domain and endpoint.host in hosts:
        return endpoint.model_copy(update={"outside_domain": False})
    return endpoint


def _preset(domain: str) -> Suggestion | None:
    for provider in PROVIDERS:
        if domain in provider.domains:
            return _from_provider(provider, "preset", None)
    return None
