"""``MAILROOM_FEATURES``: what each edition mounts, and the base, a filters-only and an MCP-only
instance at work."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest
from starlette.testclient import TestClient

from mailroom.api.deps import get_db_session
from mailroom.api.main import create_application
from mailroom.api.routes.mail import NotStored
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.users import create_user
from mailroom.config.features import ALL_FEATURES, Features, parsed_features
from mailroom.mcp.app import METADATA_PATH
from mailroom.mcp.groups import selected_groups
from mailroom.mcp.settings import McpSettings
from tests.conftest import PASSWORD
from tests.test_api_accounts import edited, new_account
from tests.test_mcp import PUBLIC_URL, FakeVerifier, Mcp

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.models import User

BASE = Features(mail=False, mcp=False, filters=False)
MAIL = Features(mail=True, mcp=False, filters=False)
MCP = Features(mail=False, mcp=True, filters=False)
FILTERS = Features(mail=False, mcp=False, filters=True)

BASE_PATHS = {"/api/v1/auth/me", "/api/v1/accounts", "/api/v1/users", "/api/v1/me/identities"}
# The user's limits come with the mail layer, which mail and MCP both bring.
LIMITS_PATH = "/api/v1/me/limits"
MAIL_PATHS = {
    "/api/v1/mail/accounts/{account_id}/messages",
    "/api/v1/mail/messages/{ref}",
    LIMITS_PATH,
}
MCP_PATHS = {
    LIMITS_PATH,
    "/mcp",
    METADATA_PATH,
    "/oauth/authorize",
    "/oauth/token",
    "/api/v1/oauth/grants",
    "/api/v1/oauth/requests/{request_id}",
}
FILTERS_PATHS = {
    "/api/v1/filters/schema",
    "/api/v1/accounts/{account_id}/folders",
    "/api/v1/accounts/{account_id}/filter-settings",
    "/api/v1/accounts/{account_id}/rule-sets",
    "/api/v1/rule-sets/{rule_set_id}/run",
}


@pytest.mark.parametrize(
    ("raw", "features"),
    [
        (None, ALL_FEATURES),
        ("", BASE),
        (" , ", BASE),
        ("mail", MAIL),
        (" MCP ", MCP),
        ("filters,mail,mcp", ALL_FEATURES),
        ("mail,mail", MAIL),
    ],
)
def test_the_features_are_read_from_their_list(raw: str | None, features: Features) -> None:
    assert parsed_features(raw) == features


def test_an_unknown_feature_stops_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="MAILROOM_FEATURES") as raised:
        parsed_features("mail,calendar")
    assert "calendar" in str(raised.value)
    assert "mail, mcp, filters" in str(raised.value)
    monkeypatch.setenv("MAILROOM_FEATURES", "mail,bogus")
    with pytest.raises(ValueError, match="bogus"):
        create_application()


def test_the_names_keep_their_order() -> None:
    assert parsed_features("filters,mcp,mail").names == ["mail", "mcp", "filters"]
    assert (MAIL.mail_layer, MCP.mail_layer, FILTERS.mail_layer) == (True, True, False)


def _application(features: Features) -> FastAPI:
    groups = selected_groups(None, features)
    settings = McpSettings(public_url=PUBLIC_URL, auth="builtin", groups=groups)
    return create_application(features=features, mcp_settings=settings)


def _paths(application: FastAPI) -> set[str]:
    """The REST routes as the OpenAPI document lists them (an included router is one opaque
    route), and the routes added outside it (``/mcp``, the authorization server's)."""
    mounted = {path for route in application.routes if (path := getattr(route, "path", None))}
    return mounted | set(application.openapi()["paths"])


@pytest.mark.parametrize(
    ("features", "mounted"),
    [
        (BASE, set()),
        (MAIL, MAIL_PATHS),
        (MCP, MCP_PATHS),
        (FILTERS, FILTERS_PATHS),
        (ALL_FEATURES, MAIL_PATHS | MCP_PATHS | FILTERS_PATHS),
    ],
)
def test_each_feature_mounts_only_its_routes(features: Features, mounted: set[str]) -> None:
    application = _application(features)
    paths = _paths(application)
    assert paths >= BASE_PATHS | mounted
    assert not paths & (MAIL_PATHS | MCP_PATHS | FILTERS_PATHS) - mounted
    assert (application.state.oauth_server is not None) is features.mcp
    stored = any(middleware.cls is NotStored for middleware in application.user_middleware)
    assert stored is features.mail


def test_without_mcp_its_settings_are_not_read(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MAILROOM_PUBLIC_URL", "")
    monkeypatch.setenv("MAILROOM_MCP_AUTH", "bogus")
    caplog.set_level(logging.INFO)
    application = create_application(features=MAIL)
    assert application.state.mcp_settings is None
    assert not [record for record in caplog.records if "MAILROOM_PUBLIC_URL" in record.getMessage()]
    monkeypatch.setenv("MAILROOM_MCP_AUTH", "builtin")
    create_application(features=ALL_FEATURES)
    assert [record for record in caplog.records if "MAILROOM_PUBLIC_URL" in record.getMessage()]


class Instance:
    """An edition's app through its lifespan, its routes' database work inside the test's
    transaction."""

    def __init__(self, client: TestClient, application: FastAPI, db: Session) -> None:
        self.client = client
        self.application = application
        self.db = db

    def sign_in(self, user: User) -> TestClient:
        self.client.cookies.set(SESSION_COOKIE_NAME, create_session(self.db, user))
        return self.client


@pytest.fixture(scope="module")
def _base_application(_worker_engine: Engine) -> Iterator[tuple[TestClient, FastAPI]]:
    application = create_application(features=BASE)
    with TestClient(application, base_url="https://testserver") as client:
        yield client, application


@pytest.fixture(scope="module")
def _filters_application(_worker_engine: Engine) -> Iterator[tuple[TestClient, FastAPI]]:
    application = create_application(features=FILTERS)
    with TestClient(application, base_url="https://testserver") as client:
        yield client, application


@pytest.fixture(scope="module")
def _mcp_application(_worker_engine: Engine) -> Iterator[tuple[TestClient, FastAPI, FakeVerifier]]:
    verifier = FakeVerifier()
    settings = McpSettings(public_url=PUBLIC_URL, auth="builtin", groups=selected_groups(None, MCP))
    application = create_application(features=MCP, mcp_settings=settings, mcp_verifier=verifier)
    with TestClient(application, base_url=PUBLIC_URL) as client:
        yield client, application, verifier


def _bound(
    client: TestClient,
    application: FastAPI,
    db_session: Session,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Instance]:
    monkeypatch.setitem(application.dependency_overrides, get_db_session, lambda: db_session)
    monkeypatch.setattr(application.state, "session_maker", sessions)
    client.cookies.clear()
    yield Instance(client, application, db_session)
    client.cookies.clear()


@pytest.fixture
def base(
    _base_application: tuple[TestClient, FastAPI],
    db_session: Session,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Instance]:
    yield from _bound(*_base_application, db_session, sessions, monkeypatch)


@pytest.fixture
def filters_only(
    _filters_application: tuple[TestClient, FastAPI],
    db_session: Session,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Instance]:
    yield from _bound(*_filters_application, db_session, sessions, monkeypatch)


@pytest.fixture
def mcp_only(
    _mcp_application: tuple[TestClient, FastAPI, FakeVerifier],
    db_session: Session,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Instance, Mcp]]:
    client, application, verifier = _mcp_application
    verifier.tokens.clear()
    for instance in _bound(client, application, db_session, sessions, monkeypatch):
        yield instance, Mcp(client, verifier, application)


def test_the_base_has_no_mail_layer_and_none_of_the_features(base: Instance, user: User) -> None:
    assert base.application.state.mail_pool is None
    assert base.application.state.send_runner is None
    client = base.sign_in(user)
    assert client.get("/api/v1/auth/me").json()["features"] == []
    for path in (
        "/api/v1/mail/accounts/work/messages",
        "/api/v1/filters/schema",
        "/api/v1/accounts/work/folders",
        "/api/v1/accounts/work/filter-settings",
        "/api/v1/accounts/work/rule-sets",
        "/api/v1/oauth/grants",
        LIMITS_PATH,
        "/oauth/authorize",
        METADATA_PATH,
    ):
        assert client.get(path).status_code == 404, path
    assert client.post("/mcp", json={}).status_code == 404


def test_the_base_keeps_accounts_and_users(base: Instance, user: User, admin: User) -> None:
    client = base.sign_in(user)
    added = client.post("/api/v1/accounts", json=new_account())
    assert added.status_code == 201
    assert "run_filters" not in added.json()
    [listed] = client.get("/api/v1/accounts").json()
    assert "run_filters" not in listed
    changed = client.put("/api/v1/accounts/work", json=edited(new_account(login="w@example.org")))
    assert changed.status_code == 200
    assert "run_filters" not in changed.json()
    assert client.delete("/api/v1/accounts/work").status_code == 204

    bob = create_user(base.db, "bob", PASSWORD)
    client = base.sign_in(bob)
    assert client.post("/api/v1/accounts", json=new_account()).status_code == 201
    client = base.sign_in(admin)
    assert client.delete(f"/api/v1/users/{bob.id}").status_code == 204


def test_a_filters_only_instance_needs_no_mail_layer(filters_only: Instance, user: User) -> None:
    assert filters_only.application.state.mail_pool is None
    assert filters_only.application.state.send_runner is None
    client = filters_only.sign_in(user)
    assert client.get("/api/v1/auth/me").json()["features"] == ["filters"]
    assert client.get("/api/v1/filters/schema").status_code == 200
    added = client.post("/api/v1/accounts", json=new_account())
    assert added.json()["run_filters"] is False
    settings = client.put("/api/v1/accounts/work/filter-settings", json={"run_filters": True})
    assert settings.status_code == 200
    assert client.get("/api/v1/accounts/work/filter-settings").json() == {"run_filters": True}
    assert client.get("/api/v1/accounts").json()[0]["run_filters"] is True
    assert client.get("/api/v1/mail/accounts/work/messages").status_code == 404


def test_an_mcp_only_instance_serves_the_mail_tools(
    mcp_only: tuple[Instance, Mcp], user: User
) -> None:
    instance, mcp = mcp_only
    assert instance.application.state.mail_pool is not None
    assert instance.application.state.send_runner is not None
    client = instance.sign_in(user)
    assert client.get("/api/v1/auth/me").json()["features"] == ["mcp"]
    assert client.get("/api/v1/oauth/grants").status_code == 200
    assert client.get("/api/v1/mail/accounts/work/messages").status_code == 404
    assert client.get("/api/v1/accounts/work/rule-sets").status_code == 404
    assert client.post("/api/v1/accounts", json=new_account()).status_code == 201

    token = mcp.verifier.issue(user.id)
    tools = {tool["name"] for tool in mcp.tools(token)}
    assert {"accounts_list", "folders_list", "messages_list", "message_send"} <= tools
    assert not {name for name in tools if name.startswith(("rule_set", "filter", "run"))}
    assert not tools & {"account_run_filters", "dry_run"}
    accounts: list[dict[str, Any]] = mcp.call(token, "accounts_list")["accounts"]
    assert [account["id"] for account in accounts] == ["work"]
    assert "run_filters" not in accounts[0]
