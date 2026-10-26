"""A tiny OpenID Connect provider for development and tests: ``mise run dev-idp``.

It approves every authorization request at once, as one configurable user (``login_hint`` picks
another), and serves discovery, a JWKS, the token endpoint (authorization code with PKCE) and
``/mint``, which hands out a signed access token for trying an external-issuer setup. Keys are made
at start and live in memory. Loopback only; never for a deployment.

Tests use ``FakeIdp.handle`` as an ``httpx.MockTransport`` handler, so nothing listens.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qsl, unquote_plus, urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from jwt.algorithms import ECAlgorithm, OKPAlgorithm, RSAAlgorithm

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_PORT: Final = 8190
PORT_ENV: Final = "MAILROOM_DEV_IDP_PORT"
DEFAULT_SUBJECT: Final = "fake-alice"
DEFAULT_USERNAME: Final = "alice"
DEFAULT_CLIENT_ID: Final = "mailroom-dev"
DEFAULT_CLIENT_SECRET: Final = "mailroom-dev-secret-for-the-fake-provider-only-never-deployed-64"

PrivateKey = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey


@dataclass
class SigningKey:
    kid: str
    algorithm: str
    private_key: PrivateKey

    def public_jwk(self) -> dict[str, Any]:
        if isinstance(self.private_key, rsa.RSAPrivateKey):
            jwk: dict[str, Any] = RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        elif isinstance(self.private_key, ec.EllipticCurvePrivateKey):
            jwk = ECAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        else:
            jwk = OKPAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        return {**jwk, "kid": self.kid, "alg": self.algorithm, "use": "sig"}


def new_signing_key(algorithm: str = "RS256") -> SigningKey:
    private_key: PrivateKey
    if algorithm in {"RS256", "PS256"}:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    elif algorithm == "ES256":
        private_key = ec.generate_private_key(ec.SECP256R1())
    elif algorithm == "EdDSA":
        private_key = ed25519.Ed25519PrivateKey.generate()
    else:
        msg = f"no key for {algorithm}"
        raise ValueError(msg)
    return SigningKey(kid=secrets.token_hex(8), algorithm=algorithm, private_key=private_key)


@dataclass
class _Grant:
    client_id: str
    redirect_uri: str
    code_challenge: str
    nonce: str | None
    subject: str
    username: str
    scope: str


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass
class FakeIdp:
    """The provider's state. Its attributes are the knobs: who signs in, what the ID token says
    (``id_token_claims`` overrides any claim), whether the next authorization is denied."""

    issuer: str = f"http://127.0.0.1:{DEFAULT_PORT}"
    client_id: str = DEFAULT_CLIENT_ID
    client_secret: str = DEFAULT_CLIENT_SECRET
    subject: str = DEFAULT_SUBJECT
    username: str = DEFAULT_USERNAME
    deny: bool = False
    id_token_claims: dict[str, Any] = field(default_factory=dict)
    id_token_algorithm: str | None = None
    """Sign ID tokens with this algorithm instead of the key's (``HS256``: the client secret)."""
    keys: list[SigningKey] = field(default_factory=lambda: [new_signing_key()])
    _grants: dict[str, _Grant] = field(default_factory=dict)

    @property
    def signing_key(self) -> SigningKey:
        return self.keys[-1]

    def rotate_key(self, algorithm: str = "RS256", *, keep_old: bool = True) -> SigningKey:
        """Sign with a new key from now on; the old ones stay in the JWKS unless dropped."""
        key = new_signing_key(algorithm)
        self.keys = [*self.keys, key] if keep_old else [key]
        return key

    def discovery(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": sorted({k.algorithm for k in self.keys}),
            "scopes_supported": ["openid", "profile", "email"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "code_challenge_methods_supported": ["S256"],
        }

    def jwks(self) -> dict[str, Any]:
        return {"keys": [key.public_jwk() for key in self.keys]}

    def mint(
        self,
        claims: Mapping[str, Any] | None = None,
        *,
        subject: str | None = None,
        audience: str | list[str] | None = None,
        lifetime_seconds: int = 3600,
        algorithm: str | None = None,
        key: SigningKey | None = None,
        headers: Mapping[str, Any] | None = None,
    ) -> str:
        """A JWT from this issuer: ``iss``, ``sub``, ``aud`` (the client by default), ``iat`` and
        ``exp``, then ``claims`` on top. ``algorithm="HS256"`` signs with the client secret, as a
        provider without a signing key would."""
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": self.issuer,
            "sub": subject or self.subject,
            "aud": audience if audience is not None else self.client_id,
            "iat": now,
            "exp": now + lifetime_seconds,
            **(claims or {}),
        }
        payload = {name: value for name, value in payload.items() if value is not None}
        if algorithm is not None and algorithm.startswith("HS"):
            return jwt.encode(
                payload, self.client_secret, algorithm=algorithm, headers=dict(headers or {})
            )
        signer = key or self.signing_key
        return jwt.encode(
            payload,
            signer.private_key,
            algorithm=algorithm or signer.algorithm,
            headers={"kid": signer.kid, **(headers or {})},
        )

    def authorize(self, params: Mapping[str, str]) -> tuple[int, str]:
        """``(status, Location or error text)``: approves at once, as ``login_hint`` if given."""
        redirect_uri = params.get("redirect_uri", "")
        if params.get("client_id") != self.client_id or not redirect_uri.startswith("http"):
            return 400, "unknown client or redirect_uri"
        back: dict[str, str] = {}
        if "state" in params:
            back["state"] = params["state"]
        if self.deny:
            self.deny = False
            back["error"] = "access_denied"
        elif params.get("response_type") != "code" or params.get("code_challenge_method") != "S256":
            back["error"] = "invalid_request"
        else:
            code = secrets.token_urlsafe(24)
            hint = params.get("login_hint")
            self._grants[code] = _Grant(
                client_id=self.client_id,
                redirect_uri=redirect_uri,
                code_challenge=params.get("code_challenge", ""),
                nonce=params.get("nonce"),
                subject=f"fake-{hint}" if hint else self.subject,
                username=hint or self.username,
                scope=params.get("scope", "openid"),
            )
            back["code"] = code
        separator = "&" if "?" in redirect_uri else "?"
        return 302, f"{redirect_uri}{separator}{urlencode(back)}"

    def _client_authenticated(self, form: Mapping[str, str], authorization: str | None) -> bool:
        if authorization and authorization.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(authorization[6:]).decode()
            except ValueError:
                return False
            # RFC 6749 2.3.1: each half was form-encoded before HTTP Basic.
            client_id, _, secret = (unquote_plus(part) for part in decoded.partition(":"))
        else:
            client_id, secret = form.get("client_id", ""), form.get("client_secret", "")
        return secrets.compare_digest(
            f"{client_id}:{secret}", f"{self.client_id}:{self.client_secret}"
        )

    def token(
        self, form: Mapping[str, str], authorization: str | None
    ) -> tuple[int, dict[str, Any]]:
        if not self._client_authenticated(form, authorization):
            return 401, {"error": "invalid_client"}
        if form.get("grant_type") != "authorization_code":
            return 400, {"error": "unsupported_grant_type"}
        grant = self._grants.pop(form.get("code", ""), None)
        if (
            grant is None
            or grant.redirect_uri != form.get("redirect_uri")
            or _challenge(form.get("code_verifier", "")) != grant.code_challenge
        ):
            return 400, {"error": "invalid_grant"}
        claims: dict[str, Any] = {
            "nonce": grant.nonce,
            "preferred_username": grant.username,
            **self.id_token_claims,
        }
        return 200, {
            "access_token": self.mint({"scope": grant.scope}, subject=grant.subject),
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": grant.scope,
            "id_token": self.mint(claims, subject=grant.subject, algorithm=self.id_token_algorithm),
        }

    def respond(
        self, method: str, url: str, body: bytes, authorization: str | None
    ) -> tuple[int, dict[str, str], bytes]:
        """One HTTP exchange: ``(status, headers, body)``."""
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        path = parts.path
        status: int
        document: Any
        if method == "GET" and path == "/.well-known/openid-configuration":
            status, document = 200, self.discovery()
        elif method == "GET" and path == "/jwks":
            status, document = 200, self.jwks()
        elif method == "GET" and path == "/authorize":
            status, target = self.authorize(query)
            if status == 302:
                return 302, {"Location": target}, b""
            document = {"error": target}
        elif method == "POST" and path == "/token":
            status, document = self.token(dict(parse_qsl(body.decode())), authorization)
        elif method == "GET" and path == "/mint":
            lifetime = int(query.get("lifetime", "3600"))
            extra = {"scope": query["scope"]} if "scope" in query else {}
            status, document = (
                200,
                {
                    "access_token": self.mint(
                        extra,
                        subject=query.get("sub"),
                        audience=query.get("aud"),
                        lifetime_seconds=lifetime,
                        algorithm=query.get("alg"),
                    )
                },
            )
        else:
            status, document = 404, {"error": "not_found"}
        return status, {"Content-Type": "application/json"}, json.dumps(document).encode()

    def handle(self, request: httpx.Request) -> httpx.Response:
        """An ``httpx.MockTransport`` handler."""
        status, headers, body = self.respond(
            request.method, str(request.url), request.read(), request.headers.get("authorization")
        )
        return httpx.Response(status, headers=headers, content=body)


def serve(idp: FakeIdp, host: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            status, headers, body = idp.respond(
                self.command,
                self.path,
                self.rfile.read(length),
                self.headers.get("Authorization"),
            )
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _answer  # noqa: N815 -- BaseHTTPRequestHandler's naming
        do_POST = _answer  # noqa: N815

    with ThreadingHTTPServer((host, port), Handler) as server:
        server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev-idp", description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=int(os.environ.get(PORT_ENV) or DEFAULT_PORT))
    parser.add_argument("--subject", default=DEFAULT_SUBJECT, help="the sub every sign-in gets")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="its preferred_username")
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--client-secret", default=DEFAULT_CLIENT_SECRET)
    args = parser.parse_args(argv)
    host = "127.0.0.1"
    idp = FakeIdp(
        issuer=f"http://{host}:{args.port}",
        client_id=args.client_id,
        client_secret=args.client_secret,
        subject=args.subject,
        username=args.username,
    )
    print(
        f"Fake OIDC provider on {idp.issuer} (sub {idp.subject!r}, username {idp.username!r}).\n"
        f"  MAILROOM_OIDC_ISSUER={idp.issuer}\n"
        f"  MAILROOM_OIDC_CLIENT_ID={idp.client_id}\n"
        f"  MAILROOM_OIDC_CLIENT_SECRET={idp.client_secret}\n"
        f"An access token: {idp.issuer}/mint?aud=<audience>[&sub=…&scope=…&lifetime=…]",
        flush=True,
    )
    with contextlib.suppress(KeyboardInterrupt):
        serve(idp, host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
