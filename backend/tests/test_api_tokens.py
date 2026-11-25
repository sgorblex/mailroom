"""Personal API tokens: made, listed and revoked with the session cookie, and what each may do on
the REST API and on ``/mcp``. The mail cases run against the Dovecot ``IMAP_TEST_HOST`` names
(skipped without it)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import delete, select, update
from starlette.testclient import TestClient

from mailroom.accounts import app_data
from mailroom.accounts import service as accounts_service
from mailroom.api.main import create_application
from mailroom.api.token_access import ACCOUNT_GROUPS, groups_of
from mailroom.audit import Origin
from mailroom.auth import api_tokens
from mailroom.auth.api_tokens import create_token, token_access, token_reach
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.users import create_user
from mailroom.config.features import ALL_FEATURES, Features
from mailroom.context import app_context
from mailroom.mail import service as mail_service
from mailroom.mcp.groups import GROUPS, offered_groups
from mailroom.models import ApiToken, AuditRecord, RuleSet, ScheduledSend, User
from mailroom.oauth.grants import GrantRefusal
from mailroom.reach import WHOLE, Reach
from mailroom.worker.sends import send_scheduled
from mailroom_core.mail import MessageRef, format_ref
from mailroom_core.testing import SmtpTestServer, create_folders, mime
from tests.conftest import PASSWORD
from tests.test_api_accounts import MAILBOX_PASSWORD, new_account
from tests.test_api_compose import MAIL, PDF, PNG, Composer, detail, post_import
from tests.test_api_filters import SHOP_SPEC
from tests.test_mcp import _mcp_application, ctx, mcp  # noqa: F401
from tests.test_scheduled_sends import composer, draft_to, stranger  # noqa: F401

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from fastapi import FastAPI
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.context import AppContext
    from mailroom.worker.context import Mailboxes
    from tests.test_mcp import Mcp

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("numbered_accounts")]

PATH = "/api/v1/me/api-tokens"
V1 = "/api/v1"
EVERY_GROUP = [group.name for group in GROUPS]
SOON = (datetime.now(UTC) + timedelta(days=2)).isoformat()
HOME = format_ref(MessageRef(2, "INBOX", 1, 1))
"""A ref of a message in the account a token narrowed to account 1 does not have."""


@pytest.fixture(autouse=True)
def _every_group(
    application: FastAPI, api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The routes' database is the test's (``api_client``), and tokens may hold every group."""
    _ = api_client
    monkeypatch.setattr(application.state, "tool_groups", GROUPS)


def made(
    db: Session,
    user: User,
    groups: list[str] | None = None,
    reach: Reach = WHOLE,
    expires_in: timedelta | None = None,
) -> tuple[ApiToken, str]:
    return create_token(
        db,
        user.id,
        name="Backup script",
        tool_groups=EVERY_GROUP if groups is None else groups,
        reach=reach,
        expires_in=expires_in,
    )


def script(application: FastAPI, raw: str) -> TestClient:
    """A client with the token and no cookie."""
    return TestClient(
        application, base_url="https://testserver", headers={"Authorization": f"Bearer {raw}"}
    )


def new_token(**fields: Any) -> dict[str, Any]:
    return {"name": "Backup", "groups": ["mail_read"], "expires_in_days": None, **fields}


def add_accounts(client: TestClient, *labels: str) -> None:
    for label in labels:
        assert client.post(f"{V1}/accounts", json=new_account(label)).status_code == 201


# Making, listing and revoking them


@pytest.mark.parametrize(
    ("method", "path"), [("GET", PATH), ("POST", PATH), ("DELETE", f"{PATH}/1")]
)
def test_signed_out_browsers_are_refused(api_client: TestClient, method: str, path: str) -> None:
    assert api_client.request(method, path).status_code == 401


def test_a_token_is_shown_once_and_kept_as_its_hash(
    user_client: TestClient, db_session: Session, user: User, caplog: pytest.LogCaptureFixture
) -> None:
    add_accounts(user_client, "work", "home")
    with caplog.at_level(logging.INFO):
        response = user_client.post(
            PATH,
            json=new_token(
                name="  Backup ",
                groups=["mail_send", "mail_read"],
                account_ids=[2],
                send_ceiling="drafts",
                expires_in_days=30,
            ),
        )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "private, no-store"
    created = response.json()
    raw = created.pop("token")
    assert raw.startswith(created["prefix"] + "_")
    assert (created["name"], created["groups"]) == ("Backup", ["mail_read", "mail_send"])
    assert (created["account_ids"], created["send_ceiling"]) == ([2], "drafts")
    expires = datetime.fromisoformat(created["expires_at"]) - datetime.now(UTC)
    assert timedelta(days=29, hours=23) < expires <= timedelta(days=30)

    listing = user_client.get(PATH).json()
    assert listing["tokens"] == [created]
    assert [group["name"] for group in listing["groups"]] == EVERY_GROUP
    assert [account["id"] for account in listing["accounts"]] == [1, 2]
    [row] = db_session.scalars(select(ApiToken).where(ApiToken.user_id == user.id))
    assert raw not in str(vars(row))
    assert raw.split("_", 2)[2] not in user_client.get(PATH).text
    assert raw.split("_", 2)[2] not in caplog.text
    assert "api_token_created" in caplog.messages


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"name": "  "}, 422),
        ({"name": "x" * 101}, 422),
        ({"expires_in_days": 7}, 422),
        ({"groups": []}, 400),
        ({"groups": ["mail_send"]}, 400),
        ({"groups": ["nothing"]}, 400),
        ({"account_ids": []}, 400),
        ({"account_ids": [99]}, 400),
        ({"send_ceiling": "everything"}, 422),
        ({"user_id": 1}, 422),
    ],
)
def test_what_cannot_be_made_is_refused(
    user_client: TestClient, fields: dict[str, Any], status: int
) -> None:
    assert user_client.post(PATH, json=new_token(**fields)).status_code == status
    assert user_client.get(PATH).json()["tokens"] == []


def test_a_group_the_instance_does_not_offer_cannot_be_chosen(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    offered = tuple(group for group in GROUPS if group.name != "mail_read")
    monkeypatch.setattr(application.state, "tool_groups", offered)
    assert detail(user_client.post(PATH, json=new_token())) == (400, "groups_not_approvable")


def test_a_user_holds_only_so_many(
    user_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_tokens, "MAX_TOKENS", 1)
    assert user_client.post(PATH, json=new_token()).status_code == 201
    assert detail(user_client.post(PATH, json=new_token())) == (409, "too_many_tokens")


def test_a_revoked_token_stops_at_once_and_only_its_user_revokes_it(
    application: FastAPI, user_client: TestClient, db_session: Session, user: User
) -> None:
    other = create_user(db_session, "bob", PASSWORD)
    theirs, their_raw = made(db_session, other)
    assert user_client.delete(f"{PATH}/{theirs.id}").status_code == 404
    assert [t["id"] for t in user_client.get(PATH).json()["tokens"]] == []

    mine, raw = made(db_session, user)
    client = script(application, raw)
    assert client.get(f"{V1}/accounts").status_code == 200
    assert user_client.delete(f"{PATH}/{mine.id}").status_code == 204
    refused = client.get(f"{V1}/accounts")
    assert (refused.status_code, refused.headers["www-authenticate"]) == (401, "Bearer")
    assert user_client.delete(f"{PATH}/{mine.id}").status_code == 404
    assert script(application, their_raw).get(f"{V1}/accounts").status_code == 200


def test_a_users_tokens_go_with_them(db_session: Session, user: User) -> None:
    _, raw = made(db_session, user)
    db_session.execute(delete(User).where(User.id == user.id))
    assert token_access(db_session, raw) is None


@pytest.mark.usefixtures("user_client")
def test_an_expired_token_is_refused_and_stays_listed(
    application: FastAPI, user_client: TestClient, db_session: Session, user: User
) -> None:
    token, raw = made(db_session, user, expires_in=timedelta(days=30))
    client = script(application, raw)
    assert client.get(f"{V1}/accounts").status_code == 200
    db_session.execute(
        update(ApiToken)
        .where(ApiToken.id == token.id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert client.get(f"{V1}/accounts").status_code == 401
    [listed] = user_client.get(PATH).json()["tokens"]
    assert (listed["id"], listed["expired"]) == (token.id, True)


def test_its_last_use_is_kept_to_the_minute(
    application: FastAPI, db_session: Session, user: User
) -> None:
    token, raw = made(db_session, user)

    def used() -> datetime | None:
        assert script(application, raw).get(f"{V1}/rule-sets/1").status_code == 404
        db_session.refresh(token)
        return token.last_used_at

    first = used()
    assert first is not None
    assert used() == first
    token.last_used_at = first - timedelta(minutes=2)
    db_session.flush()
    again = used()
    assert again is not None
    assert again >= first


# How a request says who it is


@pytest.mark.parametrize(
    "header", ["Bearer mrt_00000000_" + "a" * 43, "Bearer nope", "Bearer ", "Basic YTpi", "{raw}"]
)
def test_what_is_no_live_token_is_refused(
    application: FastAPI, api_client: TestClient, db_session: Session, user: User, header: str
) -> None:
    _, raw = made(db_session, user)
    headers = {"Authorization": header.format(raw=raw)}
    assert api_client.get(f"{V1}/accounts", headers=headers).status_code == 401


@pytest.mark.parametrize("live", [True, False])
def test_a_token_beside_the_session_cookie_is_refused(
    user_client: TestClient, db_session: Session, user: User, live: bool
) -> None:
    raw = made(db_session, user)[1] if live else "nope"
    headers = {"Authorization": f"bearer {raw}"}
    assert detail(user_client.get(f"{V1}/accounts", headers=headers)) == (400, "two_credentials")
    assert detail(user_client.post(PATH, json=new_token(), headers=headers)) == (
        400,
        "two_credentials",
    )
    # Another scheme's credentials, a proxy's say, are not ours to read.
    basic = {"Authorization": "Basic YTpi"}
    assert user_client.get(f"{V1}/accounts", headers=basic).status_code == 200


def test_a_session_is_no_token_and_a_token_is_no_session(
    api_client: TestClient, db_session: Session, user: User
) -> None:
    _, raw = made(db_session, user)
    api_client.cookies.set(SESSION_COOKIE_NAME, raw)
    assert api_client.get(f"{V1}/accounts").status_code == 401
    api_client.cookies.clear()
    session = {"Authorization": f"Bearer {create_session(db_session, user)}"}
    assert api_client.get(f"{V1}/accounts", headers=session).status_code == 401


def test_a_cross_site_request_is_refused_whoever_it_speaks_for(
    application: FastAPI, db_session: Session, user: User
) -> None:
    client = script(application, made(db_session, user)[1])
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"}):
        refused = client.post(f"{MAIL}/search", json={}, headers=headers)
        assert (refused.status_code, refused.json()["detail"]) == (
            403,
            "Cross-site request refused",
        )


def test_each_request_leaves_a_line_without_the_token(
    application: FastAPI, db_session: Session, user: User, caplog: pytest.LogCaptureFixture
) -> None:
    token, raw = made(db_session, user, ["mail_read"])
    client = script(application, raw)
    with caplog.at_level(logging.INFO):
        client.get(f"{V1}/accounts")
        client.get(f"{V1}/me/audit")
        client.get(f"{V1}/auth/sessions")
    lines = [r for r in caplog.records if r.message == "api_token_request"]
    assert [(r.user_id, r.token_id, r.outcome) for r in lines] == [  # type: ignore[attr-defined]
        (user.id, token.id, "ok"),
        (user.id, token.id, "token_not_approved"),
        (user.id, token.id, "token_not_accepted"),
    ]
    assert raw not in caplog.text


# What a token reaches

ACCOUNTS_OF = "|".join(sorted(ACCOUNT_GROUPS))
OPEN: dict[tuple[str, str], str] = {
    ("GET", "/accounts"): ACCOUNTS_OF,
    ("GET", "/accounts/keywords"): ACCOUNTS_OF,
    ("GET", "/filters/schema"): "filters_read",
    ("GET", "/accounts/{account_id}/folders"): ACCOUNTS_OF,
    ("GET", "/accounts/{account_id}/filter-settings"): "filters_read",
    ("PUT", "/accounts/{account_id}/filter-settings"): "filters_run",
    ("GET", "/accounts/{account_id}/rule-sets"): "filters_read",
    ("POST", "/accounts/{account_id}/rule-sets"): "filters_write",
    ("GET", "/rule-sets/{rule_set_id}"): "filters_read",
    ("PATCH", "/rule-sets/{rule_set_id}"): "filters_run|filters_write",
    ("DELETE", "/rule-sets/{rule_set_id}"): "filters_write",
    ("POST", "/rule-sets/{rule_set_id}/filters"): "filters_write",
    ("PATCH", "/filters/{filter_id}"): "filters_write",
    ("DELETE", "/filters/{filter_id}"): "filters_write",
    ("PUT", "/rule-sets/{rule_set_id}/order"): "filters_write",
    ("POST", "/rule-sets/{rule_set_id}/dry-run"): "filters_run",
    ("POST", "/rule-sets/{rule_set_id}/run"): "filters_run",
    ("GET", "/rule-sets/{rule_set_id}/runs"): "filters_read",
    ("GET", "/me/audit"): "audit_read",
    ("GET", "/me/export/rule-sets"): "filters_read",
    ("GET", "/me/export/audit.csv"): "audit_read",
    ("GET", "/mail/accounts/{account_id}/folders"): ACCOUNTS_OF,
    ("POST", "/mail/accounts/{account_id}/folders"): "mail_folders",
    ("POST", "/mail/accounts/{account_id}/folders/rename"): "mail_folders",
    ("POST", "/mail/accounts/{account_id}/folders/delete"): "mail_folders",
    ("POST", "/mail/accounts/{account_id}/folders/mark-read"): "mail_organize",
    ("GET", "/mail/accounts/{account_id}/messages"): "mail_read",
    ("POST", "/mail/search"): "mail_read",
    ("GET", "/mail/messages/{ref}/thread"): "mail_read",
    ("GET", "/mail/messages/{ref}"): "mail_read",
    ("GET", "/mail/messages/{ref}/html"): "mail_read",
    ("GET", "/mail/messages/{ref}/html/check"): "mail_read",
    ("GET", "/mail/messages/{ref}/parts/{part}"): "mail_read",
    ("GET", "/mail/messages/{ref}/source"): "mail_read",
    ("POST", "/mail/messages/move"): "mail_organize",
    ("POST", "/mail/messages/archive"): "mail_organize",
    ("POST", "/mail/messages/junk"): "mail_organize",
    ("POST", "/mail/messages/trash"): "mail_organize",
    ("POST", "/mail/messages/flag"): "mail_organize",
    ("POST", "/mail/messages/count"): "mail_organize",
    ("POST", "/mail/messages/bulk"): "mail_organize",
    ("POST", "/mail/messages/{ref}/snooze"): "mail_organize",
    ("GET", "/mail/snoozes"): "mail_organize",
    ("POST", "/mail/snoozes/{snooze_id}/wake"): "mail_organize",
    ("POST", "/mail/messages/{ref}/mute"): "mail_organize",
    ("GET", "/mail/muted"): "mail_organize",
    ("DELETE", "/mail/muted/{muted_thread_id}"): "mail_organize",
    ("POST", "/mail/messages/move-to-account"): "mail_transfer",
    ("POST", "/mail/drafts/prefill"): "mail_draft",
    ("POST", "/mail/drafts"): "mail_draft",
    ("GET", "/mail/drafts/find"): "mail_draft",
    ("GET", "/mail/drafts/{ref}"): "mail_draft",
    ("GET", "/mail/drafts/{ref}/findings"): "mail_draft",
    ("DELETE", "/mail/drafts/{ref}"): "mail_draft",
    ("POST", "/mail/drafts/{ref}/send"): "mail_send",
    ("POST", "/mail/drafts/{ref}/schedule"): "mail_send",
    ("GET", "/mail/sends"): "mail_send",
    ("GET", "/mail/sends/{send_id}"): "mail_send",
    ("GET", "/mail/scheduled"): "mail_send",
    ("GET", "/mail/scheduled/{scheduled_id}"): "mail_send",
    # Only what the same token scheduled: test_a_token_cancels_only_the_held_sends_it_scheduled.
    ("DELETE", "/mail/scheduled/{scheduled_id}"): "mail_send",
}
"""Every route a token reaches, by the groups that open it. A route not here refuses tokens: one
that joins says why in ``docs/requirements/nonfunctional-requirements.md``."""


def api_routes(application: FastAPI) -> list[tuple[str, str, APIRoute]]:
    """Every route under ``/api/v1``: method, path below it, route."""

    def within(routes: list[Any]) -> Iterator[APIRoute]:
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
            elif (included := getattr(route, "original_router", None)) is not None:
                yield from within(included.routes)

    found = [
        (method, route.path.removeprefix(V1), route)
        for route in within(application.routes)
        if route.include_in_schema
        for method in sorted(route.methods)
    ]
    assert {f"{V1}{path}" for _, path, _ in found} == set(application.openapi()["paths"])
    return found


PUBLIC = {
    "/health",
    "/version",
    "/ready",
    "/auth/login",
    "/auth/logout",
    "/auth/me",
    "/auth/oidc/config",
    "/auth/oidc/start",
    "/auth/oidc/callback",
}
"""Read without ``require_caller``: a token is nothing to them, and no cookie is no session."""


def test_exactly_these_routes_are_open_to_tokens(application: FastAPI) -> None:
    routes = api_routes(application)
    assert len(routes) > len(OPEN) + len(PUBLIC)
    opened = {
        (method, path): "|".join(sorted(groups_of(route.endpoint)))
        for method, path, route in routes
        if groups_of(route.endpoint)
    }
    assert opened == OPEN


def filled(path: str) -> str:
    for name, value in (("{ref}", HOME), ("{part}", "2"), ("{view_id}", "1")):
        path = path.replace(name, value)
    while "{" in path:
        start, end = path.index("{"), path.index("}") + 1
        path = f"{path[:start]}1{path[end:]}"
    return f"{V1}{path}"


def test_every_other_route_refuses_the_widest_token(
    application: FastAPI, db_session: Session, admin: User
) -> None:
    client = script(application, made(db_session, admin)[1])
    tried = 0
    for method, path, _route in api_routes(application):
        if (method, path) in OPEN or (path in PUBLIC and method != "PUT"):
            continue
        refused = client.request(method, filled(path), json={} if method != "GET" else None)
        assert detail(refused) == (403, "token_not_accepted"), (method, path)
        tried += 1
    assert tried > 40


def test_an_open_route_refuses_a_token_without_its_group(
    application: FastAPI, db_session: Session, user: User
) -> None:
    clients = {
        group: script(application, made(db_session, user, [group])[1]) for group in EVERY_GROUP
    }
    for (method, path), groups in OPEN.items():
        for group, client in clients.items():
            if group in groups.split("|"):
                continue
            refused = client.request(method, filled(path), json={} if method != "GET" else None)
            assert detail(refused) == (403, "token_not_approved"), (method, path, group)


def test_a_group_no_longer_offered_is_not_held(
    application: FastAPI, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = script(application, made(db_session, user, ["audit_read"])[1])
    assert client.get(f"{V1}/me/audit").status_code == 200
    monkeypatch.setattr(
        application.state, "tool_groups", tuple(g for g in GROUPS if g.name != "audit_read")
    )
    assert detail(client.get(f"{V1}/me/audit")) == (403, "token_not_approved")


BEYOND: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/accounts/2/folders", None),
    ("GET", "/accounts/2/filter-settings", None),
    ("PUT", "/accounts/2/filter-settings", {"run_filters": True}),
    ("GET", "/accounts/2/rule-sets", None),
    ("POST", "/accounts/2/rule-sets", {"name": "x", "folder": ["INBOX"], "trigger": "manual"}),
    ("GET", "/mail/accounts/2/folders", None),
    ("POST", "/mail/accounts/2/folders", {"parent": None, "name": "x"}),
    ("POST", "/mail/accounts/2/folders/rename", {"folder": "a", "parent": None, "name": "x"}),
    ("POST", "/mail/accounts/2/folders/delete", {"folder": "a", "messages": 0}),
    (
        "POST",
        "/mail/accounts/2/folders/mark-read",
        {"folder": "INBOX", "uid_validity": 1, "uid_next": 2},
    ),
    ("GET", "/mail/accounts/2/messages", None),
    ("POST", "/mail/search", {"account": 2, "folder": "INBOX", "subject": "x"}),
    ("POST", "/mail/messages/count", {"account": 2, "folder": "INBOX", "subject": "x"}),
    (
        "POST",
        "/mail/messages/bulk",
        {
            "account": 2,
            "folder": "INBOX",
            "subject": "x",
            "expected": 0,
            "action": {"kind": "trash"},
        },
    ),
    ("GET", f"/mail/messages/{HOME}", None),
    ("GET", f"/mail/messages/{HOME}/thread", None),
    ("GET", f"/mail/messages/{HOME}/html", None),
    ("GET", f"/mail/messages/{HOME}/html?remote=sender", None),
    ("GET", f"/mail/messages/{HOME}/html/check", None),
    ("GET", f"/mail/messages/{HOME}/parts/2", None),
    ("GET", f"/mail/messages/{HOME}/source", None),
    ("POST", "/mail/messages/move", {"refs": [HOME], "folder": "INBOX"}),
    ("POST", "/mail/messages/archive", {"refs": [HOME]}),
    ("POST", "/mail/messages/junk", {"refs": [HOME]}),
    ("POST", "/mail/messages/trash", {"refs": [HOME]}),
    ("POST", "/mail/messages/flag", {"refs": [HOME], "seen": True}),
    (
        "POST",
        "/mail/messages/move-to-account",
        {"refs": [HOME], "account": 1, "folder": "INBOX", "removal": "trash"},
    ),
    ("POST", f"/mail/messages/{HOME}/snooze", {"until": SOON}),
    ("POST", f"/mail/messages/{HOME}/mute", None),
    ("POST", "/mail/drafts/prefill", {"ref": HOME, "mode": "reply"}),
    ("GET", "/mail/drafts/find?account=2&compose_id=abcdefgh", None),
    ("GET", f"/mail/drafts/{HOME}", None),
    ("GET", f"/mail/drafts/{HOME}/findings", None),
    ("DELETE", f"/mail/drafts/{HOME}", None),
    ("POST", f"/mail/drafts/{HOME}/send", None),
    ("POST", f"/mail/drafts/{HOME}/schedule", {"send_at": SOON}),
    ("GET", "/mail/sends?account=2&compose_id=abcdefgh", None),
]


def test_an_account_beyond_is_as_one_that_is_not_there(
    application: FastAPI,
    user_client: TestClient,
    db_session: Session,
    user: User,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    add_accounts(user_client, "work", "home")
    narrow = script(application, made(db_session, user, reach=Reach(frozenset({1})))[1])
    assert [a["id"] for a in narrow.get(f"{V1}/accounts").json()] == [1]
    for method, path, body in BEYOND:
        # Whatever else is wrong with it, what it names is not found: nothing reaches its server.
        response = narrow.request(method, f"{V1}{path}", json=body)
        assert response.status_code == 404, (method, path, response.text)
    assert [record for record in audited(user.id) if record.account_id == 2] == []


def test_a_draft_on_an_account_beyond_is_refused(
    application: FastAPI, user_client: TestClient, db_session: Session, user: User
) -> None:
    add_accounts(user_client, "work", "home")
    narrow = script(application, made(db_session, user, reach=Reach(frozenset({1})))[1])
    fields = '{"account": 2, "compose_id": "0123456789abcdef"}'
    assert narrow.post(f"{MAIL}/drafts", files=[("draft", (None, fields))]).status_code == 404


def test_the_accounts_are_listed_as_an_agent_gets_them(
    application: FastAPI, user_client: TestClient, db_session: Session, user: User
) -> None:
    add_accounts(user_client, "work")
    client = script(application, made(db_session, user, ["mail_read"])[1])
    [account] = client.get(f"{V1}/accounts").json()
    assert not {"imap", "smtp", "auto_approve"} & set(account)
    assert account["id"] == 1
    assert {"imap", "smtp"} <= set(user_client.get(f"{V1}/accounts").json()[0])


def test_a_send_ceiling_caps_the_send_mode_it_is_told(
    application: FastAPI, user_client: TestClient, db_session: Session, user: User
) -> None:
    add_accounts(user_client, "work")
    client = script(application, made(db_session, user, reach=Reach(None, "off"))[1])
    assert [a["send_mode"] for a in client.get(f"{V1}/accounts").json()] == ["off"]


def test_removing_an_account_takes_it_out_of_the_tokens(
    application: FastAPI, user_client: TestClient, db_session: Session, user: User
) -> None:
    add_accounts(user_client, "work", "home")
    context = app_context(application.state)
    assert context is not None
    tokens = [
        made(db_session, user, reach=Reach(None if ids is None else frozenset(ids)))[0]
        for ids in ([1, 2], [2], None)
    ]
    app_data.forget_account(context.store(user.id), db_session, user.id, 2)
    for token in tokens:
        db_session.refresh(token)
    assert [token.account_ids for token in tokens] == [[1], [], None]


# Filters, as an agent on MCP changes them


def rule_set(db: Session, user: User, *, enabled: bool) -> int:
    row = RuleSet(
        user_id=user.id,
        account_id=1,
        folder=["INBOX"],
        name="rules",
        trigger="manual",
        enabled=enabled,
    )
    db.add(row)
    db.flush()
    return row.id


def test_enabling_a_rule_set_takes_the_run_group(
    application: FastAPI, user_client: TestClient, db_session: Session, user: User
) -> None:
    add_accounts(user_client, "work")
    writes = script(application, made(db_session, user, ["filters_write"])[1])
    runs = script(application, made(db_session, user, ["filters_run"])[1])
    fields = {"name": "x", "folder": ["INBOX"], "trigger": "manual"}
    created = f"{V1}/accounts/1/rule-sets"
    assert detail(writes.post(created, json={**fields, "enabled": True})) == (
        403,
        "token_not_approved",
    )
    assert writes.post(created, json=fields).status_code == 201

    path = f"{V1}/rule-sets/{rule_set(db_session, user, enabled=False)}"
    not_approved = (403, "token_not_approved")
    assert detail(writes.patch(path, json={"enabled": True})) == not_approved
    assert detail(writes.patch(path, json={"enabled": True, "name": "y"})) == not_approved
    assert detail(runs.patch(path, json={"name": "y"})) == not_approved
    assert detail(runs.patch(path, json={"enabled": True, "name": "y"})) == not_approved
    assert writes.patch(path, json={"name": "y"}).status_code == 200
    assert runs.patch(path, json={"enabled": True}).status_code == 200
    assert detail(runs.patch(path, json={"enabled": False})) == not_approved
    assert writes.patch(path, json={"enabled": False}).status_code == 200


def test_an_enabled_rule_set_is_not_a_tokens_to_change(
    application: FastAPI, user_client: TestClient, db_session: Session, user: User
) -> None:
    add_accounts(user_client, "work")
    client = script(application, made(db_session, user)[1])
    path = f"{V1}/rule-sets/{rule_set(db_session, user, enabled=False)}"
    added = client.post(f"{path}/filters", json={"name": "f", "spec": SHOP_SPEC})
    assert added.status_code == 201, added.text
    filter_id = added.json()["id"]
    assert client.patch(path, json={"enabled": True}).status_code == 200
    for method, url, body in (
        ("PATCH", path, {"name": "y"}),
        ("PATCH", path, {"name": "y", "enabled": False}),
        ("DELETE", path, None),
        ("POST", f"{path}/filters", {"name": "g", "spec": SHOP_SPEC}),
        ("PATCH", f"{V1}/filters/{filter_id}", {"name": "g"}),
        ("DELETE", f"{V1}/filters/{filter_id}", None),
        ("PUT", f"{path}/order", {"filter_ids": [filter_id]}),
    ):
        refused = client.request(method, url, json=body)
        assert detail(refused) == (409, "rule_set_enabled"), (method, url)
    # The user on the web may.
    assert user_client.patch(path, json={"name": "y"}).status_code == 200


# The audit trail


def test_its_records_are_an_agents_named_after_the_token(
    application: FastAPI, db_session: Session, user: User
) -> None:
    other = create_user(db_session, "bob", PASSWORD)
    token, raw = made(db_session, user)
    theirs, _ = made(db_session, other)
    for token_id in (token.id, theirs.id):
        db_session.add(
            AuditRecord(
                user_id=user.id,
                actor="agent",
                token_id=token_id,
                action="move",
                account_id=1,
                result="ok",
                detail={},
            )
        )
    db_session.flush()
    client = script(application, raw)

    def named() -> list[tuple[str | None, int | None]]:
        records = client.get(f"{V1}/me/audit").json()["records"]
        return [(record["app"], record["token_id"]) for record in records]

    # Another user's token is never named, nor one revoked.
    assert named() == [(None, theirs.id), ("Backup script", token.id)]
    _, raw_after = made(db_session, user)
    api_tokens.revoke_token(db_session, user.id, token.id)
    client = script(application, raw_after)
    assert named() == [(None, theirs.id), (None, token.id)]


# On real mail


def by_token(composer: Composer, raw: str) -> Composer:  # noqa: F811
    return Composer(
        script(composer.application, raw), composer.server, composer.login, composer.application
    )


def test_a_tokens_writes_are_recorded_as_its_own(
    composer: Composer,  # noqa: F811
    db_session: Session,
    user: User,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    token, raw = made(db_session, user)
    create_folders(composer.server, composer.login, "Archive", "Work")
    client = script(composer.application, raw)
    refs = [composer.seed(mime(f"m{i}")) for i in range(6)]
    assert client.get(f"{MAIL}/messages/{refs[0]}").status_code == 200
    for path, body in (
        ("messages/flag", {"refs": refs, "seen": True}),
        ("messages/move", {"refs": refs[:1], "folder": "Work"}),
        ("messages/archive", {"refs": refs[1:2]}),
        ("messages/trash", {"refs": refs[2:3]}),
        (f"messages/{refs[3]}/mute", None),
        ("accounts/1/folders", {"parent": None, "name": "New"}),
        (
            "messages/bulk",
            {
                "account": 1,
                "folder": "INBOX",
                "subject": "m5",
                "expected": 1,
                "action": {"kind": "flag", "flagged": True},
            },
        ),
        (
            f"messages/{refs[4]}/snooze",
            {"until": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        ),
    ):
        response = client.post(f"{MAIL}/{path}", json=body)
        assert response.status_code in (200, 201), (path, response.text)
    saved = by_token(composer, raw).post_draft(subject="Draft", text="t")
    assert saved.status_code == 201
    assert client.delete(f"{MAIL}/drafts/{saved.json()['ref']}").status_code == 200

    records = audited(user.id)
    assert {record.action for record in records} >= {
        "flag",
        "move",
        "archive",
        "trash",
        "mute",
        "folder_create",
        "bulk_flag",
        "snooze",
        "draft_save",
        "draft_remove",
    }
    assert {(r.actor, r.token_id, r.client_id, r.grant_id) for r in records} == {
        ("agent", token.id, None, None)
    }


def test_the_gate_takes_a_tokens_send_as_an_agents(
    composer: Composer,  # noqa: F811
    db_session: Session,
    user: User,
    smtp_server: SmtpTestServer,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    to = stranger()
    ref = draft_to(composer, to)
    for ceiling in ("drafts", "approval", "off"):
        capped = by_token(composer, made(db_session, user, reach=Reach(None, ceiling))[1])
        code = "send_disabled" if ceiling == "off" else "invalid_argument"
        # Held for an Undo or not, scheduled or confirmed again: none of them is a way round.
        for body in ({}, {"undoable": True}, {"confirm_resend": True}):
            assert detail(capped.post_send(ref, **body))[1] == code, (ceiling, body)
        later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        scheduled = capped.client.post(f"{MAIL}/drafts/{ref}/schedule", json={"send_at": later})
        assert detail(scheduled)[1] == code
    assert smtp_server.delivered_to(to) == []

    token, raw = made(db_session, user)
    sent = by_token(composer, raw).sent(ref)
    assert sent["state"] == "sent"
    assert len(smtp_server.delivered_to(to)) == 1
    record = next(r for r in audited(user.id) if r.action == "send" and r.result == "ok")
    assert (record.actor, record.token_id, record.detail["by"]) == ("agent", token.id, "agent")

    # The account's own mode binds it too, as any agent; the user it does not.
    again = stranger()
    ref = draft_to(composer, again)
    context = app_context(composer.application.state)
    assert context is not None
    store = context.store(user.id)
    set_send_mode(context, store, user, "drafts")
    assert detail(by_token(composer, raw).post_send(ref))[1] == "invalid_argument"
    assert composer.sent(ref)["state"] == "sent"


def set_send_mode(context: AppContext, store: Any, user: User, mode: str) -> None:
    from mailroom.accounts.store import load_accounts

    current = load_accounts(store)
    doc = {
        **current.doc,
        "account": [{**account, "send_mode": mode} for account in current.doc["account"]],  # type: ignore[union-attr]
    }
    store.save_settings(doc, current.revision)
    context.drop_mail_connections(user.id)


def test_the_gate_takes_an_agents_context_as_an_agents_whatever_it_is_told(
    composer: Composer,  # noqa: F811
    user: User,
    smtp_server: SmtpTestServer,
) -> None:
    to = stranger()
    ref = draft_to(composer, to)
    context = app_context(composer.application.state)
    assert context is not None
    agent = context.within(Reach(None, "drafts"), Origin("api", token_id=1))
    with pytest.raises(Exception, match="send mode is drafts"):
        mail_service.send_draft(agent, user.id, ref, by="user")
    assert smtp_server.delivered_to(to) == []


def test_an_import_by_a_token_records_the_files_it_gave(
    composer: Composer,  # noqa: F811
    audited: Callable[[int], list[AuditRecord]],
    db_session: Session,
    user: User,
    application: FastAPI,
) -> None:
    _, raw = made(db_session, user, ["mail_draft"])
    agent = Composer(script(application, raw), composer.server, composer.login, application)
    response = post_import(
        agent,
        "html",
        {"content_ids": ["logo"]},
        ("html", (None, b'<p>Hi <img src="cid:logo" alt="Logo"></p>')),
        ("files[]", ("logo.png", PNG, "image/png")),
        ("files[]", ("terms.pdf", PDF, "application/pdf")),
    )
    assert response.status_code == 201, response.text
    [record] = [r for r in audited(user.id) if r.action == "draft_save"]
    assert record.detail["attachments"] == 1
    assert record.detail["files"] == [
        {"name": "logo.png", "size": len(PNG)},
        {"name": "terms.pdf", "size": len(PDF)},
    ]


def test_a_held_send_goes_only_while_its_token_still_allows_it(
    composer: Composer,  # noqa: F811
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    user: User,
    smtp_server: SmtpTestServer,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    token, raw = made(db_session, user)
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    def run(**changes: Any) -> tuple[str, int]:
        to = stranger()
        ref = draft_to(composer, to)
        client = script(composer.application, raw)
        held = client.post(f"{MAIL}/drafts/{ref}/schedule", json={"send_at": later})
        assert held.status_code == 201, held.text
        assert (held.json()["by"], held.json()["app"]) == ("agent", "Backup script")
        row = db_session.get(ScheduledSend, int(held.json()["scheduled_id"]))
        assert row is not None
        assert (row.via, row.token_id) == ("api", token.id)
        before = {key: getattr(token, key) for key in changes}
        for key, value in changes.items():
            setattr(token, key, value)
        db_session.flush()
        outcome = send_scheduled(sessions, mailboxes, row.id)
        for key, value in before.items():
            setattr(token, key, value)
        db_session.flush()
        last = [r for r in audited(user.id) if r.action == "send"][-1]
        assert (last.actor, last.token_id) == ("agent", token.id)
        return outcome, len(smtp_server.delivered_to(to))

    assert run() == ("ok", 1)
    assert run(account_ids=[2]) == ("not_approved", 0)
    assert run(account_ids=[]) == ("not_approved", 0)
    assert run(tool_groups=["mail_read"]) == ("not_approved", 0)
    assert run(send_ceiling="drafts") == ("invalid_argument", 0)
    assert run(send_ceiling="off") == ("send_disabled", 0)
    expired = datetime.now(UTC) - timedelta(seconds=1)
    assert run(expires_at=expired) == ("token_revoked", 0)

    to = stranger()
    ref = draft_to(composer, to)
    held = script(composer.application, raw).post(
        f"{MAIL}/drafts/{ref}/schedule", json={"send_at": later}
    )
    api_tokens.revoke_token(db_session, user.id, token.id)
    held_id = int(held.json()["scheduled_id"])
    assert send_scheduled(sessions, mailboxes, held_id) == "token_revoked"
    assert smtp_server.delivered_to(to) == []
    [listed] = [
        one
        for one in composer.client.get(f"{MAIL}/scheduled").json()["scheduled"]
        if one["scheduled_id"] == str(held_id)
    ]
    assert (listed["by"], listed["app"], listed["code"]) == ("agent", None, "token_revoked")


def test_a_token_cancels_only_the_held_sends_it_scheduled(
    composer: Composer,  # noqa: F811
    db_session: Session,
    user: User,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    token, raw = made(db_session, user)
    _, other_raw = made(db_session, user)
    mine, other = script(composer.application, raw), script(composer.application, other_raw)
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    def held(client: TestClient) -> str:
        ref = draft_to(composer, stranger())
        answer = client.post(f"{MAIL}/drafts/{ref}/schedule", json={"send_at": later})
        assert answer.status_code == 201, answer.text
        return f"{MAIL}/scheduled/{answer.json()['scheduled_id']}"

    def cancels() -> list[tuple[str, int | None, str]]:
        records = [r for r in audited(user.id) if r.action == "send_cancel"]
        return [(r.actor, r.token_id, r.result) for r in records]

    of_user, of_other = held(composer.client), held(other)
    for path in (of_user, of_other):
        assert detail(mine.delete(path)) == (404, "not_found")
        assert composer.client.get(path).json()["state"] == "held"
    assert cancels() == []

    own = held(mine)
    row = db_session.get(ScheduledSend, int(own.rsplit("/", 1)[1]))
    assert row is not None
    row.state = "sending"
    db_session.flush()
    assert detail(mine.delete(own)) == (409, "send_started")
    row.state = "held"
    db_session.flush()
    assert mine.delete(own).status_code == 200
    assert cancels() == [("agent", token.id, "send_started"), ("agent", token.id, "ok")]
    [done] = [r for r in audited(user.id) if r.result == "ok" and r.action == "send_cancel"]
    assert {"folder", "uid"} <= set(done.detail)

    # The user's own cancel, of any of them, is theirs alone to make and leaves no record.
    assert composer.client.delete(of_user).status_code == 200
    assert composer.client.delete(of_other).status_code == 200
    assert len(cancels()) == 2


def test_another_users_token_holds_no_send_of_this_ones(db_session: Session, user: User) -> None:
    other = create_user(db_session, "bob", PASSWORD)
    theirs, _ = made(db_session, other)
    with pytest.raises(GrantRefusal) as caught:
        token_reach(db_session, user.id, theirs.id, "mail_send", 1)
    assert caught.value.code == "token_revoked"


# On /mcp


def test_a_token_works_on_mcp_within_its_scope(
    mcp: Mcp,  # noqa: F811
    ctx: AppContext,  # noqa: F811
    db_session: Session,
    user: User,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for label in ("work", "home"):
        fields = {k: v for k, v in new_account(label).items() if k != "password"}
        accounts_service.add_account(ctx, user.id, fields, MAILBOX_PASSWORD)
    token, raw = made(db_session, user, ["mail_read"], Reach(frozenset({1}), "drafts"))
    assert {tool["name"] for tool in mcp.tools(raw)} == {
        "accounts_list",
        "folders_list",
        "messages_list",
        "messages_search",
        "message_read",
        "thread_read",
        "attachment_read",
    }
    with caplog.at_level(logging.INFO):
        accounts = mcp.call(raw, "accounts_list")["accounts"]
    assert [(a["id"], a["send_mode"]) for a in accounts] == [(1, "drafts")]
    [line] = [r for r in caplog.records if r.message == "mcp_tool_called"]
    assert line.token_id == token.id  # type: ignore[attr-defined]
    assert raw not in caplog.text
    assert mcp.error(raw, "folders_list", account_id=2) == "No such account."
    assert "This API token does not allow" in mcp.error(
        raw, "message_send", account_id=1, to=["a@example.org"], subject="s", text="t"
    )
    api_tokens.revoke_token(db_session, user.id, token.id)
    assert mcp.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, raw).status_code == 401


def test_an_expired_token_is_refused_on_mcp(
    mcp: Mcp,  # noqa: F811
    db_session: Session,
    user: User,
) -> None:
    token, raw = made(db_session, user, expires_in=timedelta(days=30))
    listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert mcp.post(listing, raw).status_code == 200
    token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.flush()
    assert mcp.post(listing, raw).status_code == 401


def test_the_modes_own_tokens_still_work_and_cannot_claim_to_be_one(
    mcp: Mcp,  # noqa: F811
    user: User,
) -> None:
    from mailroom.mcp.verifier import origin_of, token_id_of

    plain = mcp.verifier.issue(user.id)
    assert mcp.tools(plain)
    claiming = mcp.verifier.tokens[plain]
    claiming.claims = {"token_id": 5}
    assert token_id_of(claiming) is None
    assert origin_of(claiming).client_id == "client"
    # One of the mode's that only looks like ours is left to the mode.
    lookalike = "mrt_00000000_" + "a" * 43
    mcp.verifier.tokens[lookalike] = claiming.model_copy(update={"token": lookalike})
    assert mcp.tools(lookalike)


# The groups a token can hold


def test_the_groups_offered_follow_the_mcp_server_or_the_features() -> None:
    def names(features: Features, raw: str | None = None) -> set[str]:
        return {group.name for group in offered_groups(None, features, raw)}

    stable = {group.name for group in GROUPS if group.status == "stable"}
    assert names(ALL_FEATURES) == stable
    assert names(Features(mail=True, mcp=False, filters=False), "mail_read") == {"mail_read"}
    assert names(Features(mail=False, mcp=False, filters=True)) == {
        "filters_read",
        "filters_write",
        "filters_run",
        "audit_read",
    }
    assert names(Features(mail=False, mcp=False, filters=False)) == set()
    assert offered_groups(GROUPS[:2], ALL_FEATURES, "mail_read") == GROUPS[:2]


def test_the_routes_are_there_with_every_feature_off() -> None:
    application = create_application(features=Features(mail=False, mcp=False, filters=False))
    assert PATH in application.openapi()["paths"]
    assert application.state.tool_groups == ()
