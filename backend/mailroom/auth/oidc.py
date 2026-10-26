"""An OpenID Connect provider as a relying party sees it: discovery, its signing keys, JWTs.

``OidcProvider`` fetches the discovery document and the JWKS over ``httpx`` (the transport is
injectable, so tests answer on ``httpx.MockTransport`` and never reach the network) and caches
both, failures too: a failed fetch is not tried again for a while, and the last good copy is kept
in use meanwhile (stale-if-error). ``verify_jwt`` checks any JWT the provider signed — the web
SSO's ID tokens here, an external issuer's access tokens for the MCP server — against an algorithm
allowlist that never includes a shared-secret (HS*) one.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote_plus, urlsplit

import httpx
import jwt

from mailroom.config.public_url import is_loopback_host

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

logger = logging.getLogger(__name__)

ALLOWED_ALGORITHMS: Final = frozenset({"RS256", "ES256", "PS256", "EdDSA"})
"""Asymmetric only: an HS* token is signed with a secret the verifier shares, so anyone holding
the client secret could mint one (and Authentik falls back to HS256 without a signing key)."""

DEFAULT_LEEWAY_SECONDS: Final = 60
_TIMEOUT_SECONDS: Final = 5.0
_MAX_DOCUMENT_BYTES: Final = 256 * 1024
_METADATA_TTL_SECONDS: Final = 3600.0
_JWKS_TTL_SECONDS: Final = 3600.0
_JWKS_MIN_REFRESH_SECONDS: Final = 30.0
"""An unknown ``kid`` refetches the JWKS (the provider rotated its key), at most this often; a
failed fetch (of the JWKS or of the discovery document) is not tried again sooner either."""
_STALE_MAX_SECONDS: Final = 24 * 3600.0
"""How old a last good copy may grow while its refresh keeps failing before it is dropped."""


class OidcError(Exception):
    """A provider exchange or a token that failed. ``code``: ``unavailable`` (the provider did not
    answer as it should) or ``invalid_token``. ``message`` is for the log, never for a client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def checked_provider_url(url: str, *, what: str) -> str:
    """``url`` when it is ``https://``, or ``http://`` on loopback (a dev provider); raises
    ``OidcError`` otherwise."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not host or parts.scheme not in {"https", "http"}:
        msg = f"{what} is not an absolute http(s) URL"
        raise OidcError("unavailable", msg)
    if parts.scheme == "http" and not is_loopback_host(host):
        msg = f"{what} is plain http:// off loopback"
        raise OidcError("unavailable", msg)
    return url


def _read_capped(response: httpx.Response) -> bytes:
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > _MAX_DOCUMENT_BYTES:
            msg = f"{response.request.url} answered more than {_MAX_DOCUMENT_BYTES} bytes"
            raise OidcError("unavailable", msg)
    return bytes(body)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        document = json.loads(_read_capped(response))
    except ValueError as exc:
        msg = f"{response.request.url} did not answer JSON"
        raise OidcError("unavailable", msg) from exc
    if not isinstance(document, dict):
        msg = f"{response.request.url} did not answer a JSON object"
        raise OidcError("unavailable", msg)
    return document


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    """What Mailroom uses of a provider's discovery document."""

    issuer: str
    """As the document states it: the value ``iss`` must carry."""
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    token_endpoint_auth_methods: tuple[str, ...]

    @classmethod
    def of(cls, document: Mapping[str, Any], *, configured_issuer: str) -> ProviderMetadata:
        issuer = document.get("issuer")
        # A trailing slash is the usual slip copying an issuer; anything else is another issuer.
        if not isinstance(issuer, str) or issuer.rstrip("/") != configured_issuer.rstrip("/"):
            msg = f"the discovery document names another issuer ({issuer!r})"
            raise OidcError("unavailable", msg)
        endpoints: dict[str, str] = {}
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = document.get(name)
            if not isinstance(value, str):
                msg = f"the discovery document has no {name}"
                raise OidcError("unavailable", msg)
            endpoints[name] = checked_provider_url(value, what=name)
        methods = document.get("token_endpoint_auth_methods_supported")
        return cls(
            issuer=issuer,
            authorization_endpoint=endpoints["authorization_endpoint"],
            token_endpoint=endpoints["token_endpoint"],
            jwks_uri=endpoints["jwks_uri"],
            token_endpoint_auth_methods=(
                tuple(m for m in methods if isinstance(m, str)) if isinstance(methods, list) else ()
            ),
        )


class _Cached[T]:
    """A document fetched on first use and again once ``ttl`` old (or on ``get(refresh=True)``,
    once ``min_interval`` old). Fetches are single-flight and at most one per ``min_interval``,
    failed or not: a failure is remembered and raised again until then, without the network, and
    while there is a last good copy (no older than ``_STALE_MAX_SECONDS``) that copy is served
    instead. ``what`` names the document in the log."""

    def __init__(
        self,
        fetch: Callable[[], T],
        *,
        what: str,
        ttl: float,
        min_interval: float,
        clock: Callable[[], float],
    ) -> None:
        self._fetch = fetch
        self._what = what
        self._ttl = ttl
        self._min_interval = min_interval
        self._clock = clock
        self._state_lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._value: T | None = None
        self._fetched_at = 0.0
        self._attempted_at: float | None = None
        self._error: OidcError | None = None

    def _due(self, now: float, *, refresh: bool) -> bool:
        with self._state_lock:
            if self._attempted_at is not None and now - self._attempted_at < self._min_interval:
                return False
            age = now - self._fetched_at
            return (
                self._value is None or age >= self._ttl or (refresh and age >= self._min_interval)
            )

    def _held(self, now: float) -> T:
        with self._state_lock:
            if self._value is not None and now - self._fetched_at < _STALE_MAX_SECONDS:
                return self._value
            if self._error is not None:
                raise OidcError(self._error.code, self._error.message)
            msg = f"the {self._what} is not fetched"
            raise OidcError("unavailable", msg)

    def _refresh(self) -> None:
        try:
            value = self._fetch()
        except OidcError as exc:
            now = self._clock()
            with self._state_lock:
                self._attempted_at = now
                self._error = exc
                stale = self._value is not None
            if stale:
                logger.warning(
                    "The %s could not be refreshed (%s); the last good copy stays in use.",
                    self._what,
                    exc.message,
                    extra={"event": "oidc_refresh_failed"},
                )
            return
        now = self._clock()
        with self._state_lock:
            self._value = value
            self._fetched_at = now
            self._attempted_at = now
            self._error = None

    def get(self, *, refresh: bool = False) -> T:
        if not self._due(self._clock(), refresh=refresh):
            return self._held(self._clock())
        with self._state_lock:
            have = self._value is not None
        # With a copy in hand, a caller does not queue behind another's fetch, unless it asked
        # for a fresher one.
        if not self._fetch_lock.acquire(blocking=refresh or not have):
            return self._held(self._clock())
        try:
            if self._due(self._clock(), refresh=refresh):  # another caller may have just fetched
                self._refresh()
        finally:
            self._fetch_lock.release()
        return self._held(self._clock())


class JwksCache:
    """A provider's signing keys, fetched on first use and again when a token names a key it does
    not know (rotation), or once the cache is an hour old; a failed fetch is cached as ``_Cached``
    says."""

    def __init__(
        self,
        client: httpx.Client,
        jwks_uri: str,
        *,
        min_refresh_seconds: float = _JWKS_MIN_REFRESH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self.jwks_uri = jwks_uri
        self._keys = _Cached(
            self._fetch,
            what="JWKS",
            ttl=_JWKS_TTL_SECONDS,
            min_interval=min_refresh_seconds,
            clock=clock,
        )

    def _fetch(self) -> list[dict[str, Any]]:
        try:
            with self._client.stream("GET", self.jwks_uri) as response:
                if response.status_code != httpx.codes.OK:
                    msg = f"the JWKS answered {response.status_code}"
                    raise OidcError("unavailable", msg)
                document = _json_object(response)
        except httpx.HTTPError as exc:
            msg = f"the JWKS could not be fetched ({type(exc).__name__})"
            raise OidcError("unavailable", msg) from exc
        keys = document.get("keys")
        if not isinstance(keys, list):
            msg = "the JWKS has no keys"
            raise OidcError("unavailable", msg)
        return [key for key in keys if isinstance(key, dict)]

    def key(self, kid: str | None, algorithm: str) -> jwt.PyJWK:
        """The key a token whose header names ``kid`` and ``algorithm`` is checked with. Without a
        ``kid`` the JWKS must hold exactly one key fit for ``algorithm``."""
        found = _pick_key(self._keys.get(), kid, algorithm)
        if found is None:
            found = _pick_key(self._keys.get(refresh=True), kid, algorithm)
        if found is None:
            msg = f"no signing key fits kid {kid!r} and {algorithm}"
            raise OidcError("invalid_token", msg)
        return found


def _pick_key(keys: list[dict[str, Any]], kid: str | None, algorithm: str) -> jwt.PyJWK | None:
    fit: list[jwt.PyJWK] = []
    for data in keys:
        if data.get("use", "sig") != "sig":
            continue
        if kid is not None and data.get("kid") != kid:
            continue
        if data.get("alg", algorithm) != algorithm:
            continue
        try:
            fit.append(jwt.PyJWK(data, algorithm=algorithm))
        except jwt.PyJWTError, ValueError, TypeError, KeyError:
            continue
    return fit[0] if len(fit) == 1 else None


def token_header(token: str) -> tuple[str, str | None]:
    """The ``alg`` and ``kid`` of ``token``'s header, unverified, once ``alg`` is one of
    ``ALLOWED_ALGORITHMS`` and ``kid`` a string or absent; raises ``OidcError("invalid_token", …)``
    otherwise. Cheap and offline: checked before anything reaches the provider."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        msg = "the token is not a JWT"
        raise OidcError("invalid_token", msg) from exc
    algorithm = header.get("alg")
    if not isinstance(algorithm, str) or algorithm not in ALLOWED_ALGORITHMS:
        msg = f"the token's algorithm {algorithm!r} is not accepted"
        raise OidcError("invalid_token", msg)
    kid = header.get("kid")
    if kid is not None and not isinstance(kid, str):
        msg = "the token's kid is not a string"
        raise OidcError("invalid_token", msg)
    return algorithm, kid


def verify_jwt(
    token: str,
    *,
    issuer: str,
    audiences: Collection[str],
    keys: JwksCache,
    leeway_seconds: float = DEFAULT_LEEWAY_SECONDS,
    required_claims: Collection[str] = ("iss", "aud", "sub", "exp", "iat"),
) -> dict[str, Any]:
    """The claims of ``token`` once its signature (by one of ``keys``, with an algorithm in
    ``ALLOWED_ALGORITHMS``), ``iss``, ``aud`` (any of ``audiences``), ``exp`` and ``iat`` (with
    ``leeway_seconds``) hold; raises ``OidcError("invalid_token", …)`` otherwise."""
    if not audiences:
        msg = "no audience to check the token against"
        raise OidcError("invalid_token", msg)
    algorithm, kid = token_header(token)
    key = keys.key(kid, algorithm)
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            audience=list(audiences),
            issuer=issuer,
            leeway=leeway_seconds,
            options={"require": list(required_claims)},
        )
    except jwt.PyJWTError as exc:
        msg = f"the token does not hold ({type(exc).__name__}: {exc})"
        raise OidcError("invalid_token", msg) from exc
    subject = claims.get("sub")
    if "sub" in required_claims and (not isinstance(subject, str) or not subject):
        msg = "the token's sub is not a non-empty string"
        raise OidcError("invalid_token", msg)
    return claims


class OidcProvider:
    """One issuer: its discovery document (cached for an hour, failures as ``_Cached`` says) and
    keys, the code exchange, and the checks of the JWTs it signs. Thread-safe; sync, so an async
    caller runs it in a thread."""

    def __init__(
        self,
        issuer: str,
        *,
        transport: httpx.BaseTransport | None = None,
        jwks_min_refresh_seconds: float = _JWKS_MIN_REFRESH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.issuer = checked_provider_url(issuer, what="the issuer")
        self._client = httpx.Client(
            transport=transport, timeout=_TIMEOUT_SECONDS, follow_redirects=False
        )
        self._jwks_min_refresh = jwks_min_refresh_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._metadata = _Cached(
            self._discover,
            what="discovery document",
            ttl=_METADATA_TTL_SECONDS,
            min_interval=jwks_min_refresh_seconds,
            clock=clock,
        )
        self._jwks: JwksCache | None = None

    def close(self) -> None:
        self._client.close()

    def _discover(self) -> ProviderMetadata:
        url = f"{self.issuer.rstrip('/')}/.well-known/openid-configuration"
        try:
            with self._client.stream("GET", url) as response:
                if response.status_code != httpx.codes.OK:
                    msg = f"discovery answered {response.status_code}"
                    raise OidcError("unavailable", msg)
                document = _json_object(response)
        except httpx.HTTPError as exc:
            msg = f"discovery failed ({type(exc).__name__})"
            raise OidcError("unavailable", msg) from exc
        return ProviderMetadata.of(document, configured_issuer=self.issuer)

    def metadata(self) -> ProviderMetadata:
        return self._metadata.get()

    def keys(self) -> JwksCache:
        jwks_uri = self.metadata().jwks_uri
        with self._lock:
            if self._jwks is None or self._jwks.jwks_uri != jwks_uri:
                self._jwks = JwksCache(
                    self._client,
                    jwks_uri,
                    min_refresh_seconds=self._jwks_min_refresh,
                    clock=self._clock,
                )
            return self._jwks

    def verify_jwt(
        self,
        token: str,
        *,
        audiences: Collection[str],
        leeway_seconds: float = DEFAULT_LEEWAY_SECONDS,
        required_claims: Collection[str] = ("iss", "aud", "sub", "exp", "iat"),
    ) -> dict[str, Any]:
        """``verify_jwt`` against this provider's issuer and keys. A token whose header does not
        hold is refused before the provider is asked for anything."""
        token_header(token)
        return verify_jwt(
            token,
            issuer=self.metadata().issuer,
            audiences=audiences,
            keys=self.keys(),
            leeway_seconds=leeway_seconds,
            required_claims=required_claims,
        )

    def verify_id_token(self, token: str, *, client_id: str, nonce: str) -> dict[str, Any]:
        """An ID token's claims, once it holds for ``client_id`` and carries ``nonce``."""
        claims = self.verify_jwt(token, audiences=[client_id])
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not hmac.compare_digest(
            token_nonce.encode(), nonce.encode()
        ):
            msg = "the ID token's nonce does not match"
            raise OidcError("invalid_token", msg)
        audience = claims.get("aud")
        several = isinstance(audience, list) and len(audience) > 1
        authorized_party = claims.get("azp")
        if (several or authorized_party is not None) and authorized_party != client_id:
            msg = "the ID token's azp is not this client"
            raise OidcError("invalid_token", msg)
        return claims

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        client_id: str,
        client_secret: str,
    ) -> str:
        """The ID token the provider gives for ``code`` (authorization code grant with PKCE)."""
        metadata = self.metadata()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        auth: httpx.BasicAuth | None = None
        methods = metadata.token_endpoint_auth_methods
        if methods and "client_secret_basic" not in methods and "client_secret_post" in methods:
            form |= {"client_id": client_id, "client_secret": client_secret}
        else:
            # RFC 6749 2.3.1: each half is form-encoded before HTTP Basic, so a ``:`` survives.
            auth = httpx.BasicAuth(quote_plus(client_id), quote_plus(client_secret))
        try:
            with self._client.stream(
                "POST",
                metadata.token_endpoint,
                data=form,
                auth=auth,
                headers={"Accept": "application/json"},
            ) as response:
                status = response.status_code
                document = _json_object(response)
        except httpx.HTTPError as exc:
            msg = f"the token endpoint failed ({type(exc).__name__})"
            raise OidcError("unavailable", msg) from exc
        if status != httpx.codes.OK:
            error = document.get("error")
            msg = f"the token endpoint refused the code ({status}, {error!r})"
            raise OidcError("invalid_token", msg)
        id_token = document.get("id_token")
        if not isinstance(id_token, str):
            msg = "the token endpoint gave no ID token"
            raise OidcError("invalid_token", msg)
        return id_token
