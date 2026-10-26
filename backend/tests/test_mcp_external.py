"""External issuer mode: ``/mcp`` taking JWTs from an OIDC provider (the fake one, answering on
``httpx.MockTransport``), mapped to users through linked sign-ins."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import anyio
import httpx
import pytest
from starlette.testclient import TestClient

from mailroom.api.main import create_application
from mailroom.auth.oidc import OidcProvider
from mailroom.config.public_url import parse_public_url
from mailroom.dev.fake_idp import FakeIdp
from mailroom.mcp.groups import selected_groups
from mailroom.mcp.settings import ExternalIssuer, McpSettings, mcp_settings_from_env
from mailroom.mcp.verifier import ExternalJwtVerifier, token_verifier
from mailroom.models.sso import ExternalIdentity

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from fastapi import FastAPI
    from mcp.server.auth.provider import AccessToken, TokenVerifier
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.mcp.verifier import AuthMode
    from mailroom.models import User

PUBLIC_URL = "https://mailroom.example"
RESOURCE = f"{PUBLIC_URL}/mcp"
ISSUER = "https://idp.example.test"
OTHER_ISSUER = "https://mcp-idp.example.test"
CLIENT = "mailroom-client"
SUBJECT = "idp-alice"
LISTING = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


@pytest.fixture(scope="module")
def module_idp() -> FakeIdp:
    return FakeIdp(issuer=ISSUER, client_id=CLIENT, subject=SUBJECT)


@pytest.fixture
def idp(module_idp: FakeIdp) -> FakeIdp:
    """A fresh provider reusing the module's RSA key (generating one costs tens of ms)."""
    return FakeIdp(issuer=ISSUER, client_id=CLIENT, subject=SUBJECT, keys=list(module_idp.keys))


def link(db: Session, user: User, subject: str = SUBJECT, issuer: str = ISSUER) -> None:
    db.add(ExternalIdentity(user_id=user.id, issuer=issuer, subject=subject))
    db.commit()


class Counting:
    """``idp.handle``, counting the requests by path."""

    def __init__(self, idp: FakeIdp) -> None:
        self.idp = idp
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        return self.idp.handle(request)


def external_verifier(
    handler: Callable[[httpx.Request], httpx.Response],
    sessions: sessionmaker[Session] | None,
    *,
    required_scope: str | None = None,
    jwks_min_refresh_seconds: float = 30,
) -> ExternalJwtVerifier:
    provider = OidcProvider(
        ISSUER,
        transport=httpx.MockTransport(handler),
        jwks_min_refresh_seconds=jwks_min_refresh_seconds,
    )
    return ExternalJwtVerifier(
        provider,
        audiences=("other", CLIENT),
        required_scope=required_scope,
        resource=RESOURCE,
        session_maker=lambda: sessions,
    )


def verified(verifier: ExternalJwtVerifier, token: str) -> AccessToken | None:
    return anyio.run(verifier.verify_token, token)


# --- The verifier ------------------------------------------------------------------------------


def test_a_linked_subjects_token_speaks_for_its_user(
    idp: FakeIdp, sessions: sessionmaker[Session], db_session: Session, user: User
) -> None:
    link(db_session, user)
    verifier = external_verifier(idp.handle, sessions)
    access = verified(verifier, idp.mint({"azp": "claude", "scope": "openid mailroom"}))
    assert access is not None
    assert (access.subject, access.client_id, access.resource) == (str(user.id), "claude", RESOURCE)
    assert access.scopes == ["openid", "mailroom"]
    assert access.claims == {"iss": ISSUER}
    # Without azp or client_id the client is named generically.
    plain = verified(verifier, idp.mint())
    assert plain is not None
    assert plain.client_id == "external"


@pytest.mark.parametrize(
    ("what", "mint"),
    [
        ("HS256, signed with the client secret", lambda idp: idp.mint(algorithm="HS256")),
        ("another audience", lambda idp: idp.mint(audience="someone-else")),
        ("another issuer", lambda idp: idp.mint({"iss": "https://impostor.example.test"})),
        ("expired past the leeway", lambda idp: idp.mint(lifetime_seconds=-120)),
        ("issued in the future", lambda idp: idp.mint({"iat": 4_000_000_000})),
        ("no iat", lambda idp: idp.mint({"iat": None})),
        ("not a JWT", lambda module_idp: "opaque-token"),
    ],
)
def test_tokens_that_do_not_hold_are_refused(
    idp: FakeIdp,
    sessions: sessionmaker[Session],
    db_session: Session,
    user: User,
    what: str,
    mint: Callable[[FakeIdp], str],
) -> None:
    link(db_session, user)
    assert verified(external_verifier(idp.handle, sessions), mint(idp)) is None, what


def test_an_id_token_is_refused(
    idp: FakeIdp,
    sessions: sessionmaker[Session],
    db_session: Session,
    user: User,
    caplog: pytest.LogCaptureFixture,
) -> None:
    link(db_session, user)
    caplog.set_level(logging.INFO, logger="mailroom.mcp.verifier")
    verifier = external_verifier(idp.handle, sessions)
    # Same iss, aud and sub as an access token: only the nonce gives it away.
    assert verified(verifier, idp.mint({"nonce": "n-0S6_WzA2Mj"})) is None
    assert any(getattr(r, "reason", None) == "id_token" for r in caplog.records)


@pytest.mark.parametrize(
    ("typ", "accepted"),
    [
        (None, True),
        ("JWT", True),
        ("jwt", True),
        ("at+jwt", True),
        ("application/at+JWT", True),
        ("id+jwt", False),
        ("logout+jwt", False),
        ("dpop+jwt", False),
        (7, False),
    ],
)
def test_the_typ_header(
    idp: FakeIdp,
    sessions: sessionmaker[Session],
    db_session: Session,
    user: User,
    typ: object,
    accepted: bool,
) -> None:
    link(db_session, user)
    counting = Counting(idp)
    verifier = external_verifier(counting, sessions)
    # PyJWT writes typ "JWT" unless told otherwise; None drops the header.
    token = idp.mint(headers={"typ": typ})
    assert (verified(verifier, token) is not None) is accepted, typ
    if not accepted:
        # Refused offline: the provider was not asked for anything.
        assert counting.paths == []


def test_a_refused_typ_is_logged_cut_short(
    idp: FakeIdp, sessions: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="mailroom.mcp.verifier")
    token = idp.mint(headers={"typ": "x" * 5000})
    assert verified(external_verifier(idp.handle, sessions), token) is None
    (record,) = [r for r in caplog.records if getattr(r, "reason", None) == "token_type"]
    assert "x" * 64 not in record.getMessage()
    assert "'" + "x" * 62 in record.getMessage()


def test_the_leeway_covers_a_token_just_expired(
    idp: FakeIdp, sessions: sessionmaker[Session], db_session: Session, user: User
) -> None:
    link(db_session, user)
    access = verified(external_verifier(idp.handle, sessions), idp.mint(lifetime_seconds=-30))
    assert access is not None
    # Kept by the bearer middleware too, which drops a token past expires_at.
    assert access.expires_at is not None
    assert access.expires_at > time.time()


def test_an_unlinked_subject_is_refused(
    idp: FakeIdp,
    sessions: sessionmaker[Session],
    db_session: Session,
    user: User,
    caplog: pytest.LogCaptureFixture,
) -> None:
    link(db_session, user, issuer="https://another-idp.example.test")
    caplog.set_level(logging.INFO, logger="mailroom.mcp.verifier")
    assert verified(external_verifier(idp.handle, sessions), idp.mint()) is None
    assert any(getattr(r, "reason", None) == "unlinked" for r in caplog.records)
    # Nobody was created or linked on the way.
    identities = db_session.query(ExternalIdentity).filter_by(subject=SUBJECT).all()
    assert [identity.issuer for identity in identities] == ["https://another-idp.example.test"]


def test_the_required_scope(
    idp: FakeIdp, sessions: sessionmaker[Session], db_session: Session, user: User
) -> None:
    link(db_session, user)
    verifier = external_verifier(idp.handle, sessions, required_scope="mailroom")
    assert verified(verifier, idp.mint({"scope": "openid profile"})) is None
    assert verified(verifier, idp.mint()) is None
    assert verified(verifier, idp.mint({"scope": "openid mailroom"})) is not None
    assert verified(verifier, idp.mint({"scp": ["mailroom"]})) is not None


def test_a_rotated_key_refetches_the_jwks(
    idp: FakeIdp, sessions: sessionmaker[Session], db_session: Session, user: User
) -> None:
    link(db_session, user)
    counting = Counting(idp)
    verifier = external_verifier(counting, sessions, jwks_min_refresh_seconds=0)
    assert verified(verifier, idp.mint()) is not None
    assert verified(verifier, idp.mint()) is not None
    assert counting.paths.count("/jwks") == 1
    idp.rotate_key("ES256", keep_old=False)
    assert verified(verifier, idp.mint()) is not None
    assert counting.paths.count("/jwks") == 2


def test_an_unavailable_provider_or_database_refuses(
    idp: FakeIdp,
    sessions: sessionmaker[Session],
    db_session: Session,
    user: User,
    caplog: pytest.LogCaptureFixture,
) -> None:
    link(db_session, user)
    token = idp.mint()
    caplog.set_level(logging.WARNING, logger="mailroom.mcp.verifier")
    down = external_verifier(lambda _request: httpx.Response(503), sessions)
    assert verified(down, token) is None
    assert verified(external_verifier(idp.handle, None), token) is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


# --- Settings ----------------------------------------------------------------------------------


@pytest.fixture
def external_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        "MAILROOM_MCP_OIDC_ISSUER",
        "MAILROOM_MCP_OIDC_AUDIENCES",
        "MAILROOM_MCP_OIDC_REQUIRED_SCOPE",
        "MAILROOM_OIDC_ISSUER",
        "MAILROOM_MCP_TOOL_GROUPS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAILROOM_PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setenv("MAILROOM_MCP_AUTH", "external")
    return monkeypatch


def test_external_settings_come_from_the_environment(external_env: pytest.MonkeyPatch) -> None:
    external_env.setenv("MAILROOM_OIDC_ISSUER", ISSUER)
    external_env.setenv("MAILROOM_MCP_OIDC_AUDIENCES", f" {CLIENT}, ,other,{CLIENT}")
    settings = mcp_settings_from_env(parse_public_url(PUBLIC_URL))
    assert settings is not None
    assert settings.external == ExternalIssuer(ISSUER, (CLIENT, "other"), None)
    assert settings.authorization_server == ISSUER

    external_env.setenv("MAILROOM_MCP_OIDC_ISSUER", "https://mcp-idp.example.test")
    external_env.setenv("MAILROOM_MCP_OIDC_REQUIRED_SCOPE", "mailroom")
    settings = mcp_settings_from_env(parse_public_url(PUBLIC_URL))
    assert settings is not None
    assert settings.external == ExternalIssuer(
        "https://mcp-idp.example.test", (CLIENT, "other"), "mailroom"
    )


@pytest.mark.parametrize(
    "env",
    [
        {"MAILROOM_MCP_OIDC_AUDIENCES": CLIENT},
        {"MAILROOM_MCP_OIDC_ISSUER": ISSUER},
        {"MAILROOM_MCP_OIDC_ISSUER": ISSUER, "MAILROOM_MCP_OIDC_AUDIENCES": " , "},
        {"MAILROOM_MCP_OIDC_ISSUER": "http://idp.example.test", "MAILROOM_MCP_OIDC_AUDIENCES": "a"},
        {
            "MAILROOM_MCP_OIDC_ISSUER": ISSUER,
            "MAILROOM_MCP_OIDC_AUDIENCES": "a",
            "MAILROOM_MCP_OIDC_REQUIRED_SCOPE": "two scopes",
        },
    ],
)
def test_external_mode_without_a_usable_provider_is_off(
    external_env: pytest.MonkeyPatch, env: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    for name, value in env.items():
        external_env.setenv(name, value)
    caplog.set_level(logging.WARNING)
    assert mcp_settings_from_env(parse_public_url(PUBLIC_URL)) is None
    assert "the MCP server is off" in caplog.text


def test_builtin_settings_send_clients_to_the_builtin_issuer(
    external_env: pytest.MonkeyPatch,
) -> None:
    external_env.setenv("MAILROOM_MCP_AUTH", "builtin")
    external_env.setenv("MAILROOM_OIDC_ISSUER", ISSUER)
    settings = mcp_settings_from_env(parse_public_url(PUBLIC_URL))
    assert settings is not None
    assert settings.external is None
    assert settings.authorization_server == f"{PUBLIC_URL}/oauth"


def test_scopes_supported_follow_the_mode() -> None:
    def scopes(auth: str, external: ExternalIssuer | None) -> list[str]:
        settings = McpSettings(
            public_url=PUBLIC_URL, auth=auth, groups=selected_groups(None), external=external
        )
        return settings.scopes_supported

    assert scopes("builtin", None) == ["mailroom"]
    assert scopes("external", ExternalIssuer(ISSUER, (CLIENT,))) == ["openid", "offline_access"]
    assert scopes("external", ExternalIssuer(ISSUER, (CLIENT,), "mailroom")) == [
        "openid",
        "offline_access",
        "mailroom",
    ]
    assert scopes("external", ExternalIssuer(ISSUER, (CLIENT,), "openid")) == [
        "openid",
        "offline_access",
    ]


def test_external_mode_verifies_with_the_external_verifier(idp: FakeIdp) -> None:
    verifier = external_verifier(idp.handle, None)
    assert token_verifier("external", external=verifier) is verifier
    assert token_verifier("builtin", external=verifier) is not verifier


@pytest.mark.usefixtures("_worker_engine")
@pytest.mark.parametrize(("mcp_issuer", "shared"), [(ISSUER, True), (OTHER_ISSUER, False)])
def test_the_sso_issuers_provider_is_shared_and_only_mcps_own_is_closed(
    external_env: pytest.MonkeyPatch,
    mcp_issuer: str,
    shared: bool,
) -> None:
    external_env.setenv("MAILROOM_OIDC_ISSUER", ISSUER)
    external_env.setenv("MAILROOM_OIDC_CLIENT_ID", CLIENT)
    external_env.setenv("MAILROOM_OIDC_CLIENT_SECRET", "secret")
    made: list[ExternalJwtVerifier] = []

    def recording(mode: AuthMode, **verifiers: Any) -> TokenVerifier | None:
        made.append(verifiers["external"])
        return token_verifier(mode, **verifiers)

    external_env.setattr("mailroom.api.main.token_verifier", recording)
    settings = McpSettings(
        public_url=PUBLIC_URL,
        auth="external",
        groups=selected_groups(None),
        external=ExternalIssuer(issuer=mcp_issuer, audiences=(CLIENT,)),
    )
    application = create_application(mcp_settings=settings)
    with TestClient(application, base_url=PUBLIC_URL):
        (verifier,) = made
        sso_provider = application.state.sso.provider
        assert (verifier.provider is sso_provider) is shared
        assert verifier.provider.issuer == mcp_issuer
    assert verifier.provider._client.is_closed  # noqa: SLF001 - closed by the lifespan
    assert sso_provider._client.is_closed  # noqa: SLF001


# --- /mcp over HTTP ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _external_application(
    _worker_engine: Engine, module_idp: FakeIdp
) -> Iterator[tuple[TestClient, FastAPI]]:
    settings = McpSettings(
        public_url=PUBLIC_URL,
        auth="external",
        groups=selected_groups(None),
        external=ExternalIssuer(issuer=ISSUER, audiences=(CLIENT,)),
    )
    application = create_application(
        mcp_settings=settings, mcp_oidc_transport=httpx.MockTransport(module_idp.handle)
    )
    # One client for the module: the MCP session manager runs in its event loop.
    with TestClient(application, base_url=PUBLIC_URL) as client:
        yield client, application


@pytest.fixture
def external(
    _external_application: tuple[TestClient, FastAPI],
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    client, application = _external_application
    monkeypatch.setattr(application.state, "session_maker", sessions)
    return client


def post(client: TestClient, body: dict[str, Any], token: str | None) -> httpx.Response:
    headers = {"accept": "application/json, text/event-stream"}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return client.post("/mcp", json=body, headers=headers)


def test_the_metadata_names_the_external_issuer_and_no_builtin_server(
    external: TestClient,
) -> None:
    response = external.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    assert response.json()["resource"] == RESOURCE
    # (Pydantic writes a bare origin with a trailing slash; an issuer with a path stays as is.)
    assert [url.rstrip("/") for url in response.json()["authorization_servers"]] == [ISSUER]
    # What to ask the external issuer for, not the built-in server's "mailroom" scope.
    assert response.json()["scopes_supported"] == ["openid", "offline_access"]
    assert external.get("/.well-known/oauth-authorization-server/oauth").status_code == 404
    assert external.post("/oauth/token").status_code in {404, 405}
    assert external.post("/oauth/register", json={}).status_code in {404, 405}


def test_a_linked_users_token_reaches_their_tools(
    external: TestClient, module_idp: FakeIdp, db_session: Session, user: User
) -> None:
    link(db_session, user)
    # No resource indicator in an external token: its aud is what binds it to Mailroom.
    response = post(external, LISTING, module_idp.mint())
    assert response.status_code == 200, response.text
    assert any(tool["name"] == "accounts_list" for tool in response.json()["result"]["tools"])
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "accounts_list", "arguments": {}},
    }
    answer = post(external, call, module_idp.mint()).json()["result"]
    assert not answer.get("isError"), answer


def test_refused_tokens_are_a_401(
    external: TestClient, module_idp: FakeIdp, db_session: Session, user: User
) -> None:
    link(db_session, user)
    for token in (
        None,
        module_idp.mint(algorithm="HS256"),
        module_idp.mint(audience="someone-else"),
        module_idp.mint(lifetime_seconds=-120),
        module_idp.mint(subject="nobody-linked"),
    ):
        response = post(external, LISTING, token)
        assert response.status_code == 401
        assert "resource_metadata=" in response.headers["www-authenticate"]
