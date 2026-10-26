"""Helpers for the built-in authorization server's tests: an MCP client's requests, and the
client metadata documents a MockTransport serves."""

from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx

from mailroom.oauth import consent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User
    from mailroom.oauth.config import OAuthSettings
    from mailroom.oauth.server import OAuthServer


ORIGIN = "https://mail.example.test"
RESOURCE = f"{ORIGIN}/mcp"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
CIMD_URL = "https://claude.ai/oauth/mcp-client-metadata"


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class Documents:
    """What the MockTransport serves for client metadata documents, and what it was asked."""

    def __init__(self) -> None:
        self.replies: dict[str, httpx.Response | Exception] = {}
        self.fetched: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.fetched.append(url)
        reply = self.replies.get(url, httpx.Response(404))
        if isinstance(reply, Exception):
            raise reply
        return reply

    def serve(self, url: str, document: dict[str, Any]) -> None:
        self.replies[url] = httpx.Response(200, json=document)


def register_client(client: TestClient, **fields: Any) -> httpx.Response:
    body = {"redirect_uris": [CLAUDE_CALLBACK], "client_name": "Claude", **fields}
    return client.post("/oauth/register", json=body)


def authorize(
    client: TestClient, client_id: str, verifier: str, **overrides: str | None
) -> httpx.Response:
    params: dict[str, str | None] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": CLAUDE_CALLBACK,
        "code_challenge": challenge(verifier),
        "code_challenge_method": "S256",
        "state": "the-state",
        "resource": RESOURCE,
        **overrides,
    }
    return client.get("/oauth/authorize", params={k: v for k, v in params.items() if v is not None})


def consent_request_id(reply: httpx.Response) -> str:
    assert reply.status_code == 302, reply.text
    location = urlsplit(reply.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{ORIGIN}/connect"
    return parse_qs(location.query)["request"][0]


def query_of(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def approved_code(
    client: TestClient,
    db_session: Session,
    settings: OAuthSettings,
    client_id: str,
    user: User,
    verifier: str,
) -> str:
    request_id = consent_request_id(authorize(client, client_id, verifier))
    back = consent.approve(db_session, settings, request_id, user.id)
    assert back is not None
    assert back.startswith(f"{CLAUDE_CALLBACK}?")
    query = query_of(back)
    assert query["state"] == "the-state"
    assert query["iss"] == f"{ORIGIN}/oauth"
    return query["code"]


def exchange_code(
    client: TestClient, client_id: str, code: str, verifier: str, **overrides: str
) -> httpx.Response:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CLAUDE_CALLBACK,
        "client_id": client_id,
        "code_verifier": verifier,
        **overrides,
    }
    return client.post("/oauth/token", data=form)


def refresh(client: TestClient, client_id: str, refresh_token: str) -> httpx.Response:
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    return client.post("/oauth/token", data=form)


def verify(server: OAuthServer, token: str) -> Any:
    return anyio.run(server.verify_token, token)
