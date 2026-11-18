"""``mailroom.accounts.autoconfig`` and ``GET /accounts/autoconfig``: each source in its order, on
an ``httpx.MockTransport`` behind ``safe_fetch`` and a fake DNS, so nothing reaches the network."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Any

import anyio
import httpx
import pytest

from mailroom.accounts.autoconfig import (
    CACHE_SECONDS,
    LOOKUPS_PER_MINUTE,
    Autoconfig,
    MxRecord,
    SrvRecord,
    Suggestion,
    address_domain,
    parse_config,
)
from mailroom.net.safe_fetch import IpAddress, safe_fetch
from tests.support.http import streamed

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from mailroom.net.safe_fetch import Fetched

OWN = "https://autoconfig.example.org/mail/config-v1.1.xml"
WELL_KNOWN = "https://example.org/.well-known/autoconfig/mail/config-v1.1.xml"
ISPDB = "https://autoconfig.thunderbird.net/v1.1/example.org"


def config(*servers: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<clientConfig version="1.1"><emailProvider id="example.org">
<domain>example.org</domain>{"".join(servers)}
</emailProvider></clientConfig>"""


def server(
    kind: str = "imap",
    host: str = "imap.example.org",
    port: int = 993,
    socket_type: str = "SSL",
    username: str = "%EMAILADDRESS%",
    auth: tuple[str, ...] = ("password-cleartext",),
) -> str:
    tag = "incomingServer" if kind == "imap" else "outgoingServer"
    methods = "".join(f"<authentication>{method}</authentication>" for method in auth)
    return (
        f'<{tag} type="{kind}"><hostname>{host}</hostname><port>{port}</port>'
        f"<socketType>{socket_type}</socketType><username>{username}</username>{methods}</{tag}>"
    )


SMTP = server("smtp", "smtp.example.org", 465)
DOCUMENT = config(server(), SMTP)


class FakeDns:
    def __init__(
        self,
        srv: dict[str, list[SrvRecord]] | None = None,
        mx: dict[str, list[MxRecord]] | None = None,
    ) -> None:
        self.records = srv or {}
        self.exchanges = mx or {}
        self.asked: list[str] = []

    async def srv(self, name: str) -> list[SrvRecord]:
        self.asked.append(name)
        return self.records.get(name, [])

    async def mx(self, domain: str) -> list[MxRecord]:
        self.asked.append(f"MX {domain}")
        return self.exchanges.get(domain, [])


class Web:
    """Documents by URL (404 otherwise), served from a public address unless a host is in
    ``private``; the URLs asked for, in order."""

    def __init__(
        self, documents: dict[str, str] | None = None, private: frozenset[str] = frozenset()
    ) -> None:
        self.documents = documents or {}
        self.private = private
        self.asked: list[str] = []

    async def resolve(self, host: str) -> list[IpAddress]:
        return [ipaddress.ip_address("10.0.0.7" if host in self.private else "93.184.215.14")]

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = f"https://{request.headers['Host']}{request.url.raw_path.decode()}"
        self.asked.append(url)
        if url not in self.documents:
            return httpx.Response(404)
        return httpx.Response(200, headers={"Content-Type": "text/xml"}, text=self.documents[url])

    async def fetch(self, url: str) -> Fetched:
        return await safe_fetch(
            url,
            max_bytes=64 * 1024,
            resolver=self.resolve,
            transport=streamed(self.handle),
        )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def look_up(web: Web, dns: FakeDns | None = None, domain: str = "example.org") -> Suggestion | None:
    lookups = Autoconfig(fetch=web.fetch, dns_records=dns or FakeDns())
    return anyio.run(lookups.lookup, domain)


def endpoints(suggestion: Suggestion | None) -> tuple[Any, ...]:
    assert suggestion is not None
    return (
        suggestion.source,
        (suggestion.imap.host, suggestion.imap.port, suggestion.imap.tls),
        (suggestion.smtp.host, suggestion.smtp.port, suggestion.smtp.tls),
        suggestion.login,
    )


@pytest.mark.parametrize(
    ("address", "domain"),
    [
        ("ana@Example.ORG", "example.org"),
        (" ana@mail.example.org. ", "mail.example.org"),
        ("a@b@example.org", "example.org"),
        ("ana@bücher.example", "xn--bcher-kva.example"),
        ("ana@example", None),
        ("example.org", None),
        ("@example.org", None),
        ("ana@[192.0.2.1]", None),
        ("ana@192.0.2.1", None),
        ("ana@-x.example.org", None),
        ("ana@x_y.example.org", None),
        ("ana@", None),
    ],
)
def test_address_domain(address: str, domain: str | None) -> None:
    assert address_domain(address) == domain


def test_own_autoconfig_first_and_without_the_address() -> None:
    web = Web({OWN: DOCUMENT, ISPDB: config(server(host="other.example.net"), SMTP)})
    dns = FakeDns()
    found = look_up(web, dns)
    assert endpoints(found) == (
        "autoconfig",
        ("imap.example.org", 993, "implicit"),
        ("smtp.example.org", 465, "implicit"),
        "address",
    )
    assert found is not None
    assert found.origin == OWN
    assert (found.provider, found.supported, found.note) == (None, True, None)
    assert web.asked == [OWN]
    assert dns.asked == []


def test_well_known_when_the_autoconfig_host_has_none() -> None:
    web = Web({WELL_KNOWN: DOCUMENT})
    found = look_up(web)
    assert found is not None
    assert (found.source, found.origin) == ("autoconfig", WELL_KNOWN)
    assert web.asked == [OWN, WELL_KNOWN]


def test_a_private_autoconfig_host_is_never_fetched() -> None:
    web = Web({OWN: DOCUMENT, WELL_KNOWN: DOCUMENT}, private=frozenset({"autoconfig.example.org"}))
    found = look_up(web)
    assert found is not None
    assert found.origin == WELL_KNOWN
    assert web.asked == [WELL_KNOWN]


def test_srv_records_after_autoconfig() -> None:
    dns = FakeDns(
        srv={
            "_imaps._tcp.example.org": [
                SrvRecord(20, 0, 993, "backup.example.org"),
                SrvRecord(10, 5, 993, "imap.example.org"),
                SrvRecord(10, 50, 993, "imap2.example.org"),
            ],
            "_submissions._tcp.example.org": [SrvRecord(0, 0, 0, "")],
            "_submission._tcp.example.org": [SrvRecord(0, 0, 587, "smtp.example.org")],
        }
    )
    web = Web()
    found = look_up(web, dns)
    assert endpoints(found) == (
        "srv",
        ("imap2.example.org", 993, "implicit"),
        ("smtp.example.org", 587, "starttls"),
        "address",
    )
    assert found is not None
    assert found.origin == "_imaps._tcp.example.org, _submission._tcp.example.org"
    assert web.asked == [OWN, WELL_KNOWN]


def test_srv_needs_both_services() -> None:
    dns = FakeDns(srv={"_imap._tcp.example.org": [SrvRecord(0, 0, 143, "imap.example.org")]})
    web = Web({ISPDB: DOCUMENT})
    found = look_up(web, dns)
    assert found is not None
    assert found.source == "ispdb"
    assert "_imaps._tcp.example.org" in dns.asked
    assert "MX example.org" in dns.asked


@pytest.mark.parametrize(
    ("exchange", "provider", "supported", "note", "imap"),
    [
        ("aspmx.l.google.com", "Gmail", True, "app_password", "imap.gmail.com"),
        (
            "example-org.mail.protection.outlook.com",
            "Microsoft",
            False,
            "oauth_required",
            "outlook.office365.com",
        ),
    ],
)
def test_mx_of_a_known_provider(
    exchange: str, provider: str, supported: bool, note: str, imap: str
) -> None:
    dns = FakeDns(mx={"example.org": [MxRecord(20, "mx.example.org"), MxRecord(10, exchange)]})
    web = Web({ISPDB: DOCUMENT})
    found = look_up(web, dns)
    assert found is not None
    assert (found.source, found.origin, found.provider) == ("mx", exchange, provider)
    assert (found.supported, found.note, found.imap.host) == (supported, note, imap)
    assert ISPDB not in web.asked


def test_unknown_mx_goes_on_to_the_ispdb() -> None:
    dns = FakeDns(mx={"example.org": [MxRecord(10, "mx.example.org")]})
    web = Web({ISPDB: DOCUMENT})
    found = look_up(web, dns)
    assert found is not None
    assert (found.source, found.origin) == ("ispdb", ISPDB)
    assert web.asked == [OWN, WELL_KNOWN, ISPDB]


def test_a_known_provider_s_servers_are_named() -> None:
    gmail = config(server(host="imap.gmail.com"), server("smtp", "smtp.gmail.com", 465))
    web = Web({"https://autoconfig.thunderbird.net/v1.1/gmail.com": gmail})
    found = look_up(web, domain="gmail.com")
    assert found is not None
    assert (found.source, found.provider, found.note) == ("ispdb", "Gmail", "app_password")


def outside(suggestion: Suggestion | None) -> tuple[bool, bool]:
    assert suggestion is not None
    return suggestion.imap.outside_domain, suggestion.smtp.outside_domain


@pytest.mark.parametrize(
    ("imap", "smtp", "flags"),
    [
        ("imap.example.org", "example.org", (False, False)),
        ("mail.example.net", "smtp.example.org", (True, False)),
        # A name that merely ends like the domain is not under it.
        ("imap.example.org", "badexample.org", (False, True)),
        ("example.org.example.net", "mail.hoster.example", (True, True)),
    ],
)
def test_a_document_host_outside_the_domain_is_kept_but_flagged(
    imap: str, smtp: str, flags: tuple[bool, bool]
) -> None:
    found = look_up(Web({OWN: config(server(host=imap), server("smtp", smtp, 465))}))
    assert found is not None
    assert (found.source, found.imap.host, found.smtp.host) == ("autoconfig", imap, smtp)
    assert outside(found) == flags


def test_an_srv_target_outside_the_domain_is_kept_but_flagged() -> None:
    dns = FakeDns(
        srv={
            "_imaps._tcp.example.org": [SrvRecord(0, 0, 993, "imap.hoster.example")],
            "_submissions._tcp.example.org": [SrvRecord(0, 0, 465, "smtp.example.org")],
        }
    )
    found = look_up(Web(), dns)
    assert endpoints(found) == (
        "srv",
        ("imap.hoster.example", 993, "implicit"),
        ("smtp.example.org", 465, "implicit"),
        "address",
    )
    assert outside(found) == (True, False)


def test_a_known_provider_s_hosts_are_not_outside_the_domain() -> None:
    gmail = config(server(host="imap.gmail.com"), server("smtp", "smtp.googlemail.com", 465))
    found = look_up(Web({OWN: gmail}))
    assert found is not None
    assert (found.source, found.provider) == ("autoconfig", "Gmail")
    assert outside(found) == (False, False)
    # Matched by its IMAP host, the provider vouches for its own hosts only.
    mixed = config(server(host="imap.gmail.com"), server("smtp", "smtp.hoster.example", 465))
    found = look_up(Web({OWN: mixed}))
    assert found is not None
    assert found.provider == "Gmail"
    assert outside(found) == (False, True)


def test_the_ispdb_mx_and_presets_are_never_flagged() -> None:
    hosted = config(server(host="imap.hoster.example"), server("smtp", "smtp.hoster.example", 465))
    assert outside(look_up(Web({ISPDB: hosted}))) == (False, False)
    dns = FakeDns(mx={"example.org": [MxRecord(10, "aspmx.l.google.com")]})
    assert outside(look_up(Web(), dns)) == (False, False)
    assert outside(look_up(Web(), domain="gmail.com")) == (False, False)


@pytest.mark.parametrize(
    ("domain", "provider", "supported", "note"),
    [
        ("gmail.com", "Gmail", True, "app_password"),
        ("icloud.com", "iCloud", True, "app_password"),
        ("fastmail.com", "Fastmail", True, "app_password"),
        ("yahoo.com", "Yahoo", True, "app_password"),
        ("outlook.com", "Microsoft", False, "oauth_required"),
    ],
)
def test_presets_when_nothing_answers(
    domain: str, provider: str, supported: bool, note: str
) -> None:
    found = look_up(Web(), domain=domain)
    assert found is not None
    assert (found.source, found.origin, found.provider) == ("preset", None, provider)
    assert (found.supported, found.note) == (supported, note)


def test_nothing_found() -> None:
    assert look_up(Web()) is None


def test_results_are_cached_per_domain_for_a_while() -> None:
    web = Web({OWN: DOCUMENT})
    clock = Clock()
    lookups = Autoconfig(fetch=web.fetch, dns_records=FakeDns(), clock=clock)
    first = anyio.run(lookups.lookup, "example.org")
    clock.now += CACHE_SECONDS - 1
    assert anyio.run(lookups.lookup, "example.org") == first
    assert web.asked == [OWN]
    clock.now += 2
    anyio.run(lookups.lookup, "example.org")
    assert web.asked == [OWN, OWN]


@pytest.mark.parametrize(("domain", "source"), [("gmail.com", "preset"), ("example.org", None)])
def test_past_the_deadline_only_a_preset_answers_and_nothing_is_cached(
    domain: str, source: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mailroom.accounts.autoconfig._LOOKUP_DEADLINE", 0.05)
    asked: list[str] = []

    async def never(url: str) -> Fetched:
        asked.append(url)
        await anyio.sleep_forever()
        raise AssertionError

    lookups = Autoconfig(fetch=never, dns_records=FakeDns())
    found = anyio.run(lookups.lookup, domain)
    assert (found.source if found else None) == source
    anyio.run(lookups.lookup, domain)
    assert len(asked) == 2


def test_parse_takes_the_first_password_servers_with_one_login() -> None:
    document = config(
        server(socket_type="plain", port=143),
        server(host="oauth.example.org", auth=("OAuth2",)),
        server(host="%EMAILDOMAIN%", port=143, socket_type="STARTTLS", username="%EMAILLOCALPART%"),
        server(host="imap.example.org", auth=("OAuth2", "password-cleartext")),
        server("smtp", "smtp.example.org", 587, "STARTTLS", "%EMAILLOCALPART%"),
        server("smtp", "fixed.example.org", 465, username="someone"),
    )
    servers = parse_config(document.encode(), "example.org")
    assert servers is not None
    login, imap, smtp = servers
    assert login == "local_part"
    assert (imap.host, imap.port, imap.tls) == ("example.org", 143, "starttls")
    assert (smtp.host, smtp.port, smtp.tls) == ("smtp.example.org", 587, "starttls")


@pytest.mark.parametrize(
    "document",
    [
        "not xml",
        "<html><body>autoconfig</body></html>",
        config(server()),
        config(server(port=0), SMTP),
        config(server(host="imap example.org"), SMTP),
        # An external entity is neither fetched nor read, an internal one never expanded.
        '<?xml version="1.0"?><!DOCTYPE c [<!ENTITY h SYSTEM "file:///etc/hostname">]>'
        + config(server(host="&h;"), SMTP).partition("?>")[2],
        '<?xml version="1.0"?><!DOCTYPE c [<!ENTITY h "imap.example.org">]>'
        + config(server(host="&h;"), SMTP).partition("?>")[2],
    ],
)
def test_parse_refuses(document: str) -> None:
    assert parse_config(document.encode(), "example.org") is None


@pytest.fixture
def fake_lookups(application: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Web:
    web = Web({OWN: DOCUMENT})
    monkeypatch.setattr(
        application.state, "autoconfig", Autoconfig(fetch=web.fetch, dns_records=FakeDns())
    )
    return web


def test_route_answers_a_suggestion(fake_lookups: Web, user_client: TestClient) -> None:
    response = user_client.get("/api/v1/accounts/autoconfig", params={"address": "ana@Example.org"})
    assert response.status_code == 200
    assert response.json() == {
        "domain": "example.org",
        "suggestion": {
            "source": "autoconfig",
            "origin": OWN,
            "provider": None,
            "supported": True,
            "note": None,
            "login": "address",
            "imap": {
                "host": "imap.example.org",
                "port": 993,
                "tls": "implicit",
                "outside_domain": False,
            },
            "smtp": {
                "host": "smtp.example.org",
                "port": 465,
                "tls": "implicit",
                "outside_domain": False,
            },
        },
    }


def test_route_answers_null_when_nothing_is_found(
    fake_lookups: Web, user_client: TestClient
) -> None:
    response = user_client.get("/api/v1/accounts/autoconfig", params={"address": "ana@example.net"})
    assert response.status_code == 200
    assert response.json() == {"domain": "example.net", "suggestion": None}


def test_route_refuses_no_address(fake_lookups: Web, user_client: TestClient) -> None:
    response = user_client.get("/api/v1/accounts/autoconfig", params={"address": "ana"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_address"
    assert fake_lookups.asked == []


def test_route_is_rate_limited(fake_lookups: Web, user_client: TestClient) -> None:
    for _ in range(LOOKUPS_PER_MINUTE):
        assert (
            user_client.get(
                "/api/v1/accounts/autoconfig", params={"address": "ana@example.org"}
            ).status_code
            == 200
        )
    response = user_client.get("/api/v1/accounts/autoconfig", params={"address": "ana@example.org"})
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "autoconfig_busy"


def test_route_needs_a_session(fake_lookups: Web, api_client: TestClient) -> None:
    response = api_client.get("/api/v1/accounts/autoconfig", params={"address": "ana@example.org"})
    assert response.status_code == 401
    assert fake_lookups.asked == []
