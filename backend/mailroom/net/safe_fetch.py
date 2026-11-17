"""An HTTPS GET that cannot be turned against the machine or its network (SSRF).

Only ``https://`` on the default port, without credentials. The host is resolved first and every
address it resolves to must be public: a private, loopback, link-local, multicast, reserved or
unspecified address, IPv4 or IPv6 (an IPv4 one mapped into IPv6 included), refuses the fetch. The
request then goes to the vetted address, the hostname kept for TLS (SNI and the certificate check)
and ``Host``, so a second lookup cannot answer differently. A redirect is followed only up to
``max_redirects`` (none by default), each target vetted the same way; one to another host gets
no ``Authorization`` or ``Cookie`` the caller passed. No proxy from the environment, no cookies,
a connect and a read timeout, a deadline for the whole fetch and a cap on the body, which is asked
for and taken only unencoded (no compressed answer can grow past the cap). The caller checks the
status and the content type."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import anyio
import httpx

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

type IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type Resolver = Callable[[str], Awaitable[list[IpAddress]]]

FetchProblem = Literal["url", "address", "unresolved", "redirect", "timeout", "too_large", "failed"]

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_CREDENTIALS = frozenset({"authorization", "cookie"})


class FetchError(Exception):
    """A fetch refused or failed; ``problem`` says which way."""

    def __init__(self, problem: FetchProblem, message: str) -> None:
        super().__init__(message)
        self.problem: FetchProblem = problem


@dataclass(frozen=True, slots=True)
class Fetched:
    """What the server answered: ``url`` is the last one fetched, after any redirect;
    ``content_type`` the media type alone, lower case (``None`` when absent)."""

    url: str
    status_code: int
    content_type: str | None
    content: bytes


def is_public_address(address: IpAddress) -> bool:
    """Whether ``address`` may be fetched from: a global one, no IPv4 address hiding inside an
    IPv6 one (mapped or 6to4) that is not."""
    if isinstance(address, ipaddress.IPv6Address):
        embedded = address.ipv4_mapped or address.sixtofour
        if embedded is not None and not is_public_address(embedded):
            return False
        if address.is_site_local:
            return False
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def resolve(host: str) -> list[IpAddress]:
    """Every address ``host`` resolves to, by the system's resolver."""
    infos = await anyio.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [ipaddress.ip_address(str(info[4][0]).partition("%")[0]) for info in infos]


async def safe_fetch(
    url: str,
    *,
    max_bytes: int,
    max_redirects: int = 0,
    headers: Mapping[str, str] | None = None,
    connect_timeout: float = 5.0,
    read_timeout: float = 10.0,
    deadline: float = 20.0,
    resolver: Resolver = resolve,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Fetched:
    """GET ``url`` within these limits. Raises ``FetchError``."""
    timeout = httpx.Timeout(connect_timeout, read=read_timeout)
    try:
        target = httpx.URL(url)
    except httpx.InvalidURL as exc:
        msg = "not a URL"
        raise FetchError("url", msg) from exc
    try:
        with anyio.fail_after(deadline):
            for _ in range(max_redirects + 1):
                reply = await _fetch_once(target, max_bytes, headers, timeout, resolver, transport)
                if isinstance(reply, Fetched):
                    return reply
                if reply.host != target.host:
                    headers = _without_credentials(headers)
                target = reply
    except TimeoutError as exc:
        msg = f"no answer within {deadline} s"
        raise FetchError("timeout", msg) from exc
    msg = f"more than {max_redirects} redirects"
    raise FetchError("redirect", msg)


async def _fetch_once(
    url: httpx.URL,
    max_bytes: int,
    headers: Mapping[str, str] | None,
    timeout: httpx.Timeout,
    resolver: Resolver,
    transport: httpx.AsyncBaseTransport | None,
) -> Fetched | httpx.URL:
    """The answer at ``url``, or where it redirects to."""
    host = _checked_host(url)
    address = await _vetted_address(host, resolver)
    async with httpx.AsyncClient(
        transport=transport, follow_redirects=False, trust_env=False, timeout=timeout
    ) as client:
        request = client.build_request(
            "GET",
            url.copy_with(host=str(address)),
            # ``Host`` and TLS (SNI, the certificate) name the host, not the address.
            headers={
                **{k: v for k, v in (headers or {}).items() if k.lower() != "accept-encoding"},
                "Accept-Encoding": "identity",
                "Host": url.netloc.decode("ascii"),
            },
            extensions={"sni_hostname": host},
        )
        try:
            reply = await client.send(request, stream=True)
            try:
                if reply.status_code in _REDIRECTS:
                    return _redirect_target(url, reply)
                content = await _read_capped(reply, max_bytes)
            finally:
                await reply.aclose()
        except httpx.TimeoutException as exc:
            msg = f"{host} timed out"
            raise FetchError("timeout", msg) from exc
        except httpx.HTTPError as exc:
            msg = f"{host} failed: {type(exc).__name__}"
            raise FetchError("failed", msg) from exc
    media_type = reply.headers.get("Content-Type", "").partition(";")[0].strip().lower()
    return Fetched(str(url), reply.status_code, media_type or None, content)


def _without_credentials(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {k: v for k, v in (headers or {}).items() if k.lower() not in _CREDENTIALS}


def _redirect_target(url: httpx.URL, reply: httpx.Response) -> httpx.URL:
    location = reply.headers.get("Location")
    if not location:
        msg = f"redirect {reply.status_code} without a Location"
        raise FetchError("redirect", msg)
    try:
        return url.join(location)
    except httpx.InvalidURL as exc:
        msg = "redirect to an invalid URL"
        raise FetchError("redirect", msg) from exc


def _checked_host(url: httpx.URL) -> str:
    """``url``'s host (ASCII, IDNA-encoded), or why it may not be fetched."""
    if url.scheme != "https":
        msg = f"not https: {url.scheme or 'no scheme'}"
        raise FetchError("url", msg)
    if url.port not in {None, 443}:
        msg = f"port {url.port} is not the default"
        raise FetchError("url", msg)
    if url.userinfo:
        msg = "credentials in the URL"
        raise FetchError("url", msg)
    host = url.raw_host.decode("ascii")
    if not host:
        msg = "no host"
        raise FetchError("url", msg)
    return host


async def _vetted_address(host: str, resolver: Resolver) -> IpAddress:
    """The address to connect to: ``host`` itself when it is one; else what it resolves to,
    refused if any address it resolves to is not public."""
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = await resolver(host)
        except OSError as exc:
            msg = f"{host} does not resolve"
            raise FetchError("unresolved", msg) from exc
    if not addresses:
        msg = f"{host} does not resolve"
        raise FetchError("unresolved", msg)
    refused = [str(address) for address in addresses if not is_public_address(address)]
    if refused:
        msg = f"{host} resolves to a non-public address: {', '.join(refused)}"
        raise FetchError("address", msg)
    return addresses[0]


async def _read_capped(reply: httpx.Response, max_bytes: int) -> bytes:
    encoding = reply.headers.get("Content-Encoding", "").strip().lower()
    if encoding not in {"", "identity"}:
        msg = f"answer encoded as {encoding}"
        raise FetchError("failed", msg)
    body = bytearray()
    async for chunk in reply.aiter_raw():
        body.extend(chunk)
        if len(body) > max_bytes:
            msg = f"answer larger than {max_bytes} bytes"
            raise FetchError("too_large", msg)
    return bytes(body)
