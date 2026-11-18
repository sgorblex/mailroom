"""``mailroom.net.safe_fetch``: the address rules, and redirects, size and time limits answered on
an ``httpx.MockTransport`` with a fake resolver, so nothing reaches the network."""

from __future__ import annotations

import gzip
import ipaddress
import socket
from typing import TYPE_CHECKING, Any

import anyio
import httpx
import pytest

from mailroom.net.safe_fetch import FetchError, IpAddress, is_public_address, safe_fetch
from tests.support.http import streamed

if TYPE_CHECKING:
    from collections.abc import Callable

PUBLIC_V4 = "93.184.215.14"
PUBLIC_V6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"

ZONE: dict[str, list[str]] = {
    "example.org": [PUBLIC_V4],
    "v6.example.org": [PUBLIC_V6],
    "inside.example.org": ["10.0.0.5"],
    "mixed.example.org": [PUBLIC_V4, "127.0.0.1"],
    "mapped.example.org": ["::ffff:192.168.1.1"],
}


async def fake_resolver(host: str) -> list[IpAddress]:
    if host not in ZONE:
        raise socket.gaierror(socket.EAI_NONAME, "unknown")
    return [ipaddress.ip_address(address) for address in ZONE[host]]


def fetch(handler: Callable[[httpx.Request], Any], url: str, **kwargs: Any):
    async def run():
        return await safe_fetch(
            url,
            max_bytes=kwargs.pop("max_bytes", 1000),
            resolver=fake_resolver,
            transport=streamed(handler),
            **kwargs,
        )

    return anyio.run(run)


def refused(handler: Callable[[httpx.Request], Any], url: str, **kwargs: Any) -> FetchError:
    with pytest.raises(FetchError) as caught:
        fetch(handler, url, **kwargs)
    return caught.value


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"Content-Type": "Text/XML; charset=utf-8"}, text="<x/>")


def unreachable(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"fetched {request.url}")


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "198.18.0.1",
        "192.0.2.1",
        "::",
        "::1",
        "fe80::1",
        "fec0::1",
        "fc00::1",
        "fd12:3456::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::127.0.0.1",
        "2002:7f00:1::1",
        "2002:c0a8:101::1",
        "2001::1",
        "2001:db8::1",
        "64:ff9b::a00:1",
    ],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    assert not is_public_address(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", [PUBLIC_V4, "8.8.8.8", PUBLIC_V6, "::ffff:8.8.8.8"])
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_public_address(ipaddress.ip_address(address))


def test_connects_to_the_vetted_address_keeping_the_host_name() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(request)

    fetched = fetch(handler, "https://example.org/config?x=1", headers={"Accept": "text/xml"})
    assert (fetched.status_code, fetched.content_type, fetched.content) == (
        200,
        "text/xml",
        b"<x/>",
    )
    assert fetched.url == "https://example.org/config?x=1"
    (request,) = seen
    assert str(request.url) == f"https://{PUBLIC_V4}/config?x=1"
    assert request.headers["Host"] == "example.org"
    assert request.headers["Accept"] == "text/xml"
    assert request.extensions["sni_hostname"] == "example.org"
    assert "Cookie" not in request.headers


def test_an_ipv6_address_is_connected_to_in_brackets() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(request)

    fetch(handler, "https://v6.example.org/")
    assert seen[0].url.netloc.decode() == f"[{PUBLIC_V6}]"
    assert seen[0].headers["Host"] == "v6.example.org"


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        ("http://example.org/", "url"),
        ("ftp://example.org/", "url"),
        ("https://example.org:8443/", "url"),
        ("https://user:pass@example.org/", "url"),
        ("https:///path", "url"),
        ("https://inside.example.org/", "address"),
        ("https://mixed.example.org/", "address"),
        ("https://mapped.example.org/", "address"),
        ("https://127.0.0.1/", "address"),
        ("https://[::1]/", "address"),
        ("https://[::ffff:169.254.169.254]/", "address"),
        ("https://nowhere.example.org/", "unresolved"),
    ],
)
def test_refused_before_any_request(url: str, problem: str) -> None:
    assert refused(unreachable, url).problem == problem


def test_a_public_address_literal_is_fetched() -> None:
    assert fetch(ok, f"https://{PUBLIC_V4}/").status_code == 200


def test_redirects_are_not_followed_by_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://v6.example.org/"})

    assert refused(handler, "https://example.org/").problem == "redirect"


def test_a_redirect_is_followed_and_vetted_again() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Host"] == "example.org":
            return httpx.Response(301, headers={"Location": "https://v6.example.org/next"})
        return ok(request)

    fetched = fetch(handler, "https://example.org/", max_redirects=1)
    assert fetched.url == "https://v6.example.org/next"

    def to_inside(request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"Location": "https://inside.example.org/"})

    assert refused(to_inside, "https://example.org/", max_redirects=3).problem == "address"

    def to_http(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://example.org/"})

    assert refused(to_http, "https://example.org/", max_redirects=3).problem == "url"


def test_too_many_redirects_are_refused() -> None:
    def loop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/again"})

    error = refused(loop, "https://example.org/", max_redirects=2)
    assert (error.problem, str(error)) == ("redirect", "more than 2 redirects")


def test_a_redirect_without_location_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302)

    assert refused(handler, "https://example.org/", max_redirects=1).problem == "redirect"


def test_cookies_are_never_sent_back() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={"Location": "/next", "Set-Cookie": "id=1; Path=/"})
        return ok(request)

    fetch(handler, "https://example.org/", max_redirects=1)
    assert "Cookie" not in seen[1].headers


def test_an_answer_over_the_cap_is_refused() -> None:
    def big(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1001)

    assert refused(big, "https://example.org/", max_bytes=1000).problem == "too_large"
    assert fetch(big, "https://example.org/", max_bytes=1001).content == b"x" * 1001


def test_only_an_unencoded_answer_is_asked_for_and_taken() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(request)

    fetch(handler, "https://example.org/", headers={"accept-encoding": "gzip"})
    assert seen[0].headers.get_list("Accept-Encoding") == ["identity"]

    def bomb(request: httpx.Request) -> httpx.Response:
        body = gzip.compress(b"x" * 100_000)
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=body)

    assert len(gzip.compress(b"x" * 100_000)) < 1000
    assert refused(bomb, "https://example.org/", max_bytes=1000).problem == "failed"


def test_credentials_stay_with_the_host_they_were_given_for() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/same"})
        if request.url.path == "/same":
            return httpx.Response(302, headers={"Location": "https://v6.example.org/other"})
        return ok(request)

    credentials = {"Authorization": "Bearer t", "Cookie": "id=1", "Accept": "text/xml"}
    fetch(handler, "https://example.org/start", max_redirects=2, headers=credentials)
    assert [request.headers.get("Authorization") for request in seen] == ["Bearer t"] * 2 + [None]
    assert "Cookie" not in seen[2].headers
    assert seen[2].headers["Accept"] == "text/xml"


def test_an_error_status_is_the_callers_to_judge() -> None:
    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    fetched = fetch(missing, "https://example.org/")
    assert (fetched.status_code, fetched.content_type) == (404, None)


def test_a_transport_timeout_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    assert refused(handler, "https://example.org/").problem == "timeout"


def test_a_slow_answer_runs_into_the_deadline() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(1)
        return ok(request)

    error = refused(slow, "https://example.org/", deadline=0.05)
    assert error.problem == "timeout"


def test_a_connection_failure_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert refused(handler, "https://example.org/").problem == "failed"
