"""``mailroom.auth.oidc`` against the fake provider, on ``httpx.MockTransport``."""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING

import httpx
import jwt
import pytest

from mailroom.auth.oidc import OidcError, OidcProvider
from mailroom.dev.fake_idp import FakeIdp

if TYPE_CHECKING:
    from collections.abc import Callable

ISSUER = "https://idp.example.test"
CLIENT = "mailroom-test"
DISCOVERY = "/.well-known/openid-configuration"


@pytest.fixture(scope="module")
def _keys() -> FakeIdp:
    return FakeIdp(issuer=ISSUER, client_id=CLIENT)


@pytest.fixture
def idp(_keys: FakeIdp) -> FakeIdp:
    """A fresh provider reusing the module's RSA key (generating one costs tens of ms)."""
    return FakeIdp(issuer=ISSUER, client_id=CLIENT, keys=list(_keys.keys))


class Counting:
    """``idp.handle``, counting the requests by path."""

    def __init__(self, idp: FakeIdp) -> None:
        self.idp = idp
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        return self.idp.handle(request)


def provider_for(
    handler: Callable[[httpx.Request], httpx.Response], *, jwks_min_refresh_seconds: float = 30
) -> OidcProvider:
    return OidcProvider(
        ISSUER,
        transport=httpx.MockTransport(handler),
        jwks_min_refresh_seconds=jwks_min_refresh_seconds,
    )


@pytest.fixture
def provider(idp: FakeIdp) -> OidcProvider:
    return provider_for(idp.handle)


def test_a_token_from_the_issuer_holds(idp: FakeIdp, provider: OidcProvider) -> None:
    claims = provider.verify_jwt(idp.mint({"scope": "mailroom"}), audiences=["other", CLIENT])
    assert claims["sub"] == idp.subject
    assert claims["scope"] == "mailroom"


@pytest.mark.parametrize("algorithm", ["ES256", "PS256", "EdDSA"])
def test_every_allowed_algorithm_holds(idp: FakeIdp, algorithm: str) -> None:
    key = idp.rotate_key(algorithm)
    provider = provider_for(idp.handle)
    assert provider.verify_jwt(idp.mint(key=key), audiences=[CLIENT])["iss"] == ISSUER


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_shared_secret_algorithms_are_refused(
    idp: FakeIdp, provider: OidcProvider, algorithm: str
) -> None:
    with pytest.raises(OidcError, match="algorithm") as refused:
        provider.verify_jwt(idp.mint(algorithm=algorithm), audiences=[CLIENT])
    assert refused.value.code == "invalid_token"


def test_an_unsigned_token_is_refused(idp: FakeIdp, provider: OidcProvider) -> None:
    unsigned = jwt.encode({"iss": ISSUER, "sub": "x", "aud": CLIENT}, None, algorithm="none")
    with pytest.raises(OidcError, match="algorithm"):
        provider.verify_jwt(unsigned, audiences=[CLIENT])


def test_an_oct_key_in_the_jwks_never_verifies_an_hs_token(idp: FakeIdp) -> None:
    def with_oct_key(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jwks":
            secret = jwt.utils.base64url_encode(idp.client_secret.encode()).decode()
            return httpx.Response(200, json={"keys": [{"kty": "oct", "k": secret}]})
        return idp.handle(request)

    with pytest.raises(OidcError, match="algorithm"):
        provider_for(with_oct_key).verify_jwt(idp.mint(algorithm="HS256"), audiences=[CLIENT])


@pytest.mark.parametrize(
    ("claims", "audiences"),
    [
        ({"aud": "someone-else"}, [CLIENT]),
        ({"iss": "https://elsewhere.example.test"}, [CLIENT]),
        ({"exp": int(time.time()) - 61}, [CLIENT]),
        ({"iat": int(time.time()) + 120}, [CLIENT]),
        ({"sub": ""}, [CLIENT]),
        ({}, []),
    ],
    ids=["audience", "issuer", "expired", "issued in the future", "empty subject", "no audience"],
)
def test_a_token_whose_claims_do_not_hold_is_refused(
    idp: FakeIdp, provider: OidcProvider, claims: dict[str, object], audiences: list[str]
) -> None:
    with pytest.raises(OidcError) as refused:
        provider.verify_jwt(idp.mint(claims), audiences=audiences)
    assert refused.value.code == "invalid_token"


@pytest.mark.parametrize("missing", ["exp", "iat", "sub"])
def test_a_token_without_a_required_claim_is_refused(
    idp: FakeIdp, provider: OidcProvider, missing: str
) -> None:
    with pytest.raises(OidcError):
        provider.verify_jwt(idp.mint({missing: None}), audiences=[CLIENT])


def test_a_minute_of_clock_skew_is_tolerated(idp: FakeIdp, provider: OidcProvider) -> None:
    now = int(time.time())
    skewed = idp.mint({"iat": now + 30, "exp": now - 30})
    assert provider.verify_jwt(skewed, audiences=[CLIENT])["sub"] == idp.subject


def test_discovery_and_keys_are_cached(idp: FakeIdp) -> None:
    counting = Counting(idp)
    provider = provider_for(counting)
    for _ in range(3):
        provider.verify_jwt(idp.mint(), audiences=[CLIENT])
    assert counting.paths == ["/.well-known/openid-configuration", "/jwks"]


def test_a_rotated_key_is_fetched_when_a_token_names_it(idp: FakeIdp) -> None:
    counting = Counting(idp)
    provider = provider_for(counting, jwks_min_refresh_seconds=0)
    provider.verify_jwt(idp.mint(), audiences=[CLIENT])
    idp.rotate_key("ES256", keep_old=False)
    assert provider.verify_jwt(idp.mint(), audiences=[CLIENT])["sub"] == idp.subject
    assert counting.paths.count("/jwks") == 2


def test_unknown_keys_refetch_the_jwks_at_most_so_often(idp: FakeIdp) -> None:
    counting = Counting(idp)
    provider = provider_for(counting, jwks_min_refresh_seconds=3600)
    provider.verify_jwt(idp.mint(), audiences=[CLIENT])
    idp.rotate_key("ES256", keep_old=False)
    for _ in range(3):
        with pytest.raises(OidcError, match="no signing key"):
            provider.verify_jwt(idp.mint(), audiences=[CLIENT])
    assert counting.paths.count("/jwks") == 1


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Flaky(Counting):
    """``Counting``, failing ``paths_down`` with a connection error while ``down``."""

    def __init__(self, idp: FakeIdp, *paths_down: str) -> None:
        super().__init__(idp)
        self.paths_down = paths_down
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down and request.url.path in self.paths_down:
            self.paths.append(request.url.path)
            raise httpx.ConnectError("refused", request=request)
        return super().__call__(request)


def clocked_provider(handler: Flaky, clock: Clock) -> OidcProvider:
    return OidcProvider(ISSUER, transport=httpx.MockTransport(handler), clock=clock)


@pytest.mark.parametrize("path", [DISCOVERY, "/jwks"])
def test_a_failed_fetch_is_not_retried_before_the_refresh_interval(idp: FakeIdp, path: str) -> None:
    flaky, clock = Flaky(idp, path), Clock()
    flaky.down = True
    provider = clocked_provider(flaky, clock)
    for _ in range(3):
        with pytest.raises(OidcError) as refused:
            provider.verify_jwt(idp.mint(), audiences=[CLIENT])
        assert refused.value.code == "unavailable"
    assert flaky.paths.count(path) == 1
    clock.now += 31
    flaky.down = False
    assert provider.verify_jwt(idp.mint(), audiences=[CLIENT])["sub"] == idp.subject
    assert flaky.paths.count(path) == 2


@pytest.mark.parametrize("path", [DISCOVERY, "/jwks"])
def test_the_last_good_copy_is_served_while_a_refresh_fails(
    idp: FakeIdp, path: str, caplog: pytest.LogCaptureFixture
) -> None:
    flaky, clock = Flaky(idp, path), Clock()
    provider = clocked_provider(flaky, clock)
    provider.verify_jwt(idp.mint(), audiences=[CLIENT])
    flaky.down = True
    clock.now += 3601  # both caches are due
    for _ in range(3):
        assert provider.verify_jwt(idp.mint(), audiences=[CLIENT])["sub"] == idp.subject
    assert flaky.paths.count(path) == 2  # the first fetch, then one failed refresh
    assert "last good copy stays in use" in caplog.text
    clock.now += 24 * 3600  # too stale to keep
    with pytest.raises(OidcError) as refused:
        provider.verify_jwt(idp.mint(), audiences=[CLIENT])
    assert refused.value.code == "unavailable"


@pytest.mark.parametrize("token", ["not a jwt", "HS256", "none"])
def test_a_token_whose_header_does_not_hold_never_reaches_the_provider(
    idp: FakeIdp, token: str
) -> None:
    counting = Counting(idp)
    if token == "HS256":
        token = idp.mint(algorithm="HS256")
    elif token == "none":
        token = jwt.encode({"iss": ISSUER, "sub": "x"}, None, algorithm="none")
    with pytest.raises(OidcError) as refused:
        provider_for(counting).verify_jwt(token, audiences=[CLIENT])
    assert refused.value.code == "invalid_token"
    assert counting.paths == []


def test_a_discovery_document_for_another_issuer_is_refused(idp: FakeIdp) -> None:
    idp.issuer = "https://impostor.example.test"
    with pytest.raises(OidcError, match="another issuer") as refused:
        provider_for(idp.handle).metadata()
    assert refused.value.code == "unavailable"


def test_a_trailing_slash_on_the_issuer_is_the_same_issuer(idp: FakeIdp) -> None:
    provider = OidcProvider(f"{ISSUER}/", transport=httpx.MockTransport(idp.handle))
    assert provider.metadata().issuer == ISSUER  # as the provider states it, for ``iss``
    assert provider.verify_jwt(idp.mint(), audiences=[CLIENT])["iss"] == ISSUER


@pytest.mark.parametrize(
    "endpoint",
    ["http://idp.example.test/jwks", "ftp://idp.example.test/jwks", "/jwks"],
)
def test_endpoints_must_be_https(idp: FakeIdp, endpoint: str) -> None:
    def tampered(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={**idp.discovery(), "jwks_uri": endpoint})
        return idp.handle(request)

    with pytest.raises(OidcError, match="jwks_uri"):
        provider_for(tampered).metadata()


def test_plain_http_is_for_loopback_only() -> None:
    with pytest.raises(OidcError):
        OidcProvider("http://idp.example.test")
    OidcProvider("http://127.0.0.1:8190").close()


def test_oversized_and_unreachable_providers_are_unavailable(idp: FakeIdp) -> None:
    def huge(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b" " * (300 * 1024) + b"}")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def redirected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://elsewhere.example.test/"})

    for handler in (huge, down, redirected):
        with pytest.raises(OidcError) as refused:
            provider_for(handler).metadata()
        assert refused.value.code == "unavailable"


def test_an_id_token_needs_its_nonce_and_azp(idp: FakeIdp, provider: OidcProvider) -> None:
    good = idp.mint({"nonce": "n-1"})
    assert provider.verify_id_token(good, client_id=CLIENT, nonce="n-1")["nonce"] == "n-1"
    with pytest.raises(OidcError, match="nonce"):
        provider.verify_id_token(good, client_id=CLIENT, nonce="n-2")
    with pytest.raises(OidcError, match="nonce"):
        provider.verify_id_token(idp.mint(), client_id=CLIENT, nonce="n-1")
    shared = idp.mint({"nonce": "n-1"}, audience=[CLIENT, "another"])
    with pytest.raises(OidcError, match="azp"):
        provider.verify_id_token(shared, client_id=CLIENT, nonce="n-1")
    with pytest.raises(OidcError, match="azp"):
        provider.verify_id_token(
            idp.mint({"nonce": "n-1", "azp": "another"}), client_id=CLIENT, nonce="n-1"
        )


def test_the_code_exchange_uses_basic_auth_and_returns_the_id_token(idp: FakeIdp) -> None:
    sent: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return idp.handle(request)

    provider = provider_for(recording)
    status, location = idp.authorize(
        {
            "client_id": CLIENT,
            "redirect_uri": "https://app.example.test/cb",
            "response_type": "code",
            "code_challenge_method": "S256",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "nonce": "n",
        }
    )
    assert status == 302
    code = httpx.URL(location).params["code"]
    with pytest.raises(OidcError, match="refused the code"):
        provider.exchange_code(
            code=code,
            redirect_uri="https://app.example.test/cb",
            code_verifier="wrong",
            client_id=CLIENT,
            client_secret=idp.client_secret,
        )
    status, location = idp.authorize(
        {
            "client_id": CLIENT,
            "redirect_uri": "https://app.example.test/cb",
            "response_type": "code",
            "code_challenge_method": "S256",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "nonce": "n",
        }
    )
    id_token = provider.exchange_code(
        code=httpx.URL(location).params["code"],
        redirect_uri="https://app.example.test/cb",
        code_verifier="dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
        client_id=CLIENT,
        client_secret=idp.client_secret,
    )
    assert provider.verify_id_token(id_token, client_id=CLIENT, nonce="n")["sub"] == idp.subject
    token_request = next(r for r in reversed(sent) if r.url.path == "/token")
    assert token_request.headers["authorization"].startswith("Basic ")
    assert b"client_secret" not in token_request.read()


def test_the_code_exchange_form_encodes_the_basic_credentials() -> None:
    secret = "s3:cr%t+ shh"
    idp = FakeIdp(issuer=ISSUER, client_id=CLIENT, client_secret=secret)
    sent: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return idp.handle(request)

    status, location = idp.authorize(
        {
            "client_id": CLIENT,
            "redirect_uri": "https://app.example.test/cb",
            "response_type": "code",
            "code_challenge_method": "S256",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "nonce": "n",
        }
    )
    assert status == 302
    provider_for(recording).exchange_code(
        code=httpx.URL(location).params["code"],
        redirect_uri="https://app.example.test/cb",
        code_verifier="dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
        client_id=CLIENT,
        client_secret=secret,
    )
    token_request = next(r for r in sent if r.url.path == "/token")
    expected = base64.b64encode(f"{CLIENT}:s3%3Acr%25t%2B+shh".encode()).decode()
    assert token_request.headers["authorization"] == f"Basic {expected}"
