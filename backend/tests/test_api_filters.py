from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from imapclient import IMAPClient
from sqlalchemy import select, text

from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.users import create_user
from mailroom.filters import runs as filter_runs
from mailroom.filters import service as filters_service
from mailroom.filters.runs import (
    MATCH_DAYS,
    RunRefused,
    count_matches,
    dry_run_slot,
    labelled_report,
    matches_counted,
    prune_runs,
)
from mailroom.filters.service import MAX_FILTERS
from mailroom.filters.spec import offered_schema
from mailroom.models import Filter, FilterMatchDay, FilterRun, RuleSet
from mailroom.worker.app import PROCRASTINATE_SCHEMA
from mailroom_core.filters import MatchTimeoutError, json_schema, load_filter
from mailroom_core.mail import CERTIFICATE_DETAIL, MESSAGES
from mailroom_core.testing import SmtpStub
from tests.conftest import PASSWORD
from tests.support.mail import mail_servers_ssl_context
from tests.test_api_accounts import added, dev_mailbox_account, new_account

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User
    from mailroom_core.testing import ImapTestServer

FROM_SHOP = {"type": "from_is", "args": {"addr": "news@shop.example"}}
TO_ARCHIVE = {"type": "move_to", "args": {"folder": ["Archive"]}}
SHOP_SPEC = {"criterion": FROM_SHOP, "action": TO_ARCHIVE}


def rule_set(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Newsletters", "folder": ["INBOX"], "trigger": "manual"}
    return body | changes


@pytest.fixture
def account(user_client: TestClient) -> int:
    return added(user_client)


@pytest.fixture
def rule_set_id(user_client: TestClient, account: int) -> int:
    response = user_client.post(f"/api/v1/accounts/{account}/rule-sets", json=rule_set())
    assert response.status_code == 201
    identifier: int = response.json()["id"]
    return identifier


def add_filter(client: TestClient, rule_set_id: int, name: str, spec: dict[str, Any]) -> int:
    response = client.post(
        f"/api/v1/rule-sets/{rule_set_id}/filters", json={"name": name, "spec": spec}
    )
    assert response.status_code == 201, response.json()
    identifier: int = response.json()["id"]
    return identifier


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/filters/schema").status_code == 401
    assert api_client.get("/api/v1/accounts/1/folders").status_code == 401
    assert api_client.get("/api/v1/accounts/1/filter-settings").status_code == 401
    assert api_client.get("/api/v1/accounts/1/rule-sets").status_code == 401
    assert api_client.get("/api/v1/rule-sets/1").status_code == 401
    assert api_client.post("/api/v1/rule-sets/1/dry-run").status_code == 401
    assert api_client.post("/api/v1/rule-sets/1/run").status_code == 401
    assert api_client.post("/api/v1/filters/draft-from-search", json={}).status_code == 401


def test_the_schema_is_the_engines_without_in_thread(user_client: TestClient) -> None:
    served = user_client.get("/api/v1/filters/schema").json()
    assert served == offered_schema()
    assert "in_thread" in str(json_schema())
    assert "in_thread" not in str(served)


def test_a_spec_naming_in_thread_is_refused(user_client: TestClient, rule_set_id: int) -> None:
    thread = {"type": "in_thread", "args": {"thread": "muted"}}
    spec = {"criterion": {"or": [{"type": "is_unread"}, {"not": thread}]}, "action": TO_ARCHIVE}
    response = user_client.post(
        f"/api/v1/rule-sets/{rule_set_id}/filters", json={"name": "Thread", "spec": spec}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "hidden_criterion"

    filter_id = add_filter(user_client, rule_set_id, "Good", SHOP_SPEC)
    response = user_client.patch(f"/api/v1/filters/{filter_id}", json={"spec": spec})
    assert response.json()["detail"]["code"] == "hidden_criterion"


def test_run_filters_is_off_until_turned_on(user_client: TestClient, account: int) -> None:
    settings = f"/api/v1/accounts/{account}/filter-settings"
    assert user_client.get(settings).json() == {"run_filters": False}

    assert user_client.put(settings, json={"run_filters": True}).json() == {"run_filters": True}
    assert user_client.get(settings).json() == {"run_filters": True}
    assert user_client.get("/api/v1/accounts").json()[0]["run_filters"] is True

    # Removing the account takes the switch with it.
    assert user_client.delete(f"/api/v1/accounts/{account}").status_code == 204
    assert user_client.get(settings).status_code == 404
    again = added(user_client)
    assert user_client.get(f"/api/v1/accounts/{again}/filter-settings").json() == {
        "run_filters": False
    }


def test_the_folder_picker_answers_mailbox_failures(
    user_client: TestClient,
    account: int,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mailroom_core.crypto import secrets_subkey

    # Port 9 on loopback is closed: a real connection, refused.
    response = user_client.get(f"/api/v1/accounts/{account}/folders")
    assert (response.status_code, response.json()["detail"]["code"]) == (502, "unreachable")

    # STARTTLS is tried too, not refused up front.
    starttls = new_account("home", imap={"host": "127.0.0.1", "port": 9, "tls": "starttls"})
    home = added(user_client, starttls)
    response = user_client.get(f"/api/v1/accounts/{home}/folders")
    assert (response.status_code, response.json()["detail"]["code"]) == (502, "unreachable")

    monkeypatch.setattr(application.state, "secrets_subkey", secrets_subkey(os.urandom(32)))
    response = user_client.get(f"/api/v1/accounts/{account}/folders")
    assert (response.status_code, response.json()["detail"]["code"]) == (409, "password_missing")


def test_the_folder_picker_says_a_certificate_did_not_verify(user_client: TestClient) -> None:
    """The stub's TLS certificate is self-signed: the handshake fails before any IMAP."""
    with SmtpStub() as stub:
        account = new_account(imap={"host": "127.0.0.1", "port": stub.port, "tls": "implicit"})
        work = added(user_client, account)
        response = user_client.get(f"/api/v1/accounts/{work}/folders")
    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "unreachable",
        "message": f"{MESSAGES['unreachable']} {CERTIFICATE_DETAIL}",
    }


def test_the_dev_mailbox_lists_folders(
    user_client: TestClient,
    imap_server: ImapTestServer,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = dev_mailbox_account(user_client, imap_server, application, monkeypatch, "folders")
    conn = IMAPClient(
        imap_server.host, imap_server.port, ssl=True, ssl_context=mail_servers_ssl_context()
    )
    try:
        conn.login(account["login"], imap_server.password)
        if not conn.folder_exists("Archive"):
            conn.create_folder("Archive")
        conn.create_folder("Archive/2026")
    finally:
        conn.logout()

    folders = user_client.get(f"/api/v1/accounts/{account['id']}/folders").json()
    assert {"path": ["INBOX"]} in folders
    assert {"path": ["Archive"]} in folders
    # Split on the server's delimiter ("/" here), not the filter engine's default.
    assert {"path": ["Archive", "2026"]} in folders


def test_a_user_adds_edits_and_removes_a_rule_set(
    user_client: TestClient, account: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    rule_sets = f"/api/v1/accounts/{account}/rule-sets"
    assert user_client.get(rule_sets).json() == []

    response = user_client.post(
        rule_sets,
        json=rule_set(folder=["Archive", "2026"], trigger="scheduled", interval_minutes=15),
    )
    assert response.status_code == 201
    added = response.json()
    assert {key: added[key] for key in added if not key.endswith("_at") and key != "id"} == {
        "account_id": account,
        "folder": ["Archive", "2026"],
        "name": "Newsletters",
        "trigger": "scheduled",
        "interval_minutes": 15,
        "marker": "none",
        "enabled": False,
        "filters": [],
    }
    listed = user_client.get(rule_sets).json()
    assert [entry["id"] for entry in listed] == [added["id"]]
    assert "filters" not in listed[0]

    path = f"/api/v1/rule-sets/{added['id']}"
    response = user_client.patch(path, json={"name": "News", "enabled": True, "marker": "star"})
    assert response.status_code == 200
    changed = response.json()
    assert (changed["name"], changed["enabled"], changed["marker"]) == ("News", True, "star")
    assert changed["interval_minutes"] == 15

    # Leaving the schedule drops its interval.
    changed = user_client.patch(path, json={"trigger": "new_mail"}).json()
    assert (changed["trigger"], changed["interval_minutes"]) == ("new_mail", None)
    assert user_client.get(path).json()["trigger"] == "new_mail"

    assert user_client.delete(path).status_code == 204
    assert user_client.get(path).status_code == 404
    assert user_client.get(rule_sets).json() == []
    events = [record.msg for record in caplog.records if str(record.msg).startswith("rule_set_")]
    assert events == ["rule_set_added", "rule_set_changed", "rule_set_changed", "rule_set_removed"]


def test_to_sort_is_taken_only_where_the_server_keeps_keywords(
    user_client: TestClient, account: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    keeps: list[bool] = [False]
    asked: list[int] = []

    def keywords(_ctx: object, _user_id: int, account_id: int, *, via: str) -> bool:
        asked.append(account_id)
        return keeps[0]

    monkeypatch.setattr(filters_service.mail_service, "keywords", keywords)
    rule_sets = f"/api/v1/accounts/{account}/rule-sets"
    response = user_client.post(rule_sets, json=rule_set(marker="to_sort"))
    assert (response.status_code, response.json()["detail"]["code"]) == (
        400,
        "to_sort_unsupported",
    )
    starred = user_client.post(rule_sets, json=rule_set(marker="star")).json()
    path = f"/api/v1/rule-sets/{starred['id']}"
    response = user_client.patch(path, json={"marker": "to_sort"})
    assert response.json()["detail"]["code"] == "to_sort_unsupported"
    assert user_client.get(path).json()["marker"] == "star"
    assert asked == [account, account]

    keeps[0] = True
    assert user_client.patch(path, json={"marker": "to_sort"}).json()["marker"] == "to_sort"
    added_to_sort = user_client.post(rule_sets, json=rule_set(marker="to_sort"))
    assert (added_to_sort.status_code, added_to_sort.json()["marker"]) == (201, "to_sort")
    assert len(asked) == 4

    # Kept as it is, To sort is not asked again, whatever the server now says.
    keeps[0] = False
    renamed = user_client.patch(path, json={"name": "Renamed", "marker": "to_sort"}).json()
    assert (renamed["name"], renamed["marker"]) == ("Renamed", "to_sort")
    assert len(asked) == 4


def test_a_filter_marks_to_sort_only_where_the_server_keeps_keywords(
    user_client: TestClient, rule_set_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    keeps: list[bool] = [False]
    asked: list[int] = []

    def keywords(_ctx: object, _user_id: int, account_id: int, *, via: str) -> bool:
        asked.append(account_id)
        return keeps[0]

    monkeypatch.setattr(filters_service.mail_service, "keywords", keywords)
    marks = {
        "criterion": FROM_SHOP,
        "action": {"seq": [{"type": "star"}, {"type": "mark_to_sort"}]},
    }
    filters = f"/api/v1/rule-sets/{rule_set_id}/filters"
    response = user_client.post(filters, json={"name": "Shop", "spec": marks})
    assert (response.status_code, response.json()["detail"]["code"]) == (
        400,
        "to_sort_unsupported",
    )
    # Its criteria only match nothing there: they are taken.
    looks = {"criterion": {"type": "is_to_sort"}, "action": TO_ARCHIVE}
    shop = add_filter(user_client, rule_set_id, "Shop", looks)
    path = f"/api/v1/filters/{shop}"
    response = user_client.patch(path, json={"spec": marks})
    assert response.json()["detail"]["code"] == "to_sort_unsupported"
    assert len(asked) == 2

    keeps[0] = True
    assert user_client.patch(path, json={"spec": marks}).status_code == 200
    assert len(asked) == 3
    # Kept as it is, it is not asked again.
    keeps[0] = False
    assert user_client.patch(path, json={"name": "Shop 2", "spec": marks}).status_code == 200
    assert len(asked) == 3


def test_to_sort_on_an_unreachable_server_is_refused_with_its_failure(
    user_client: TestClient, account: int
) -> None:
    response = user_client.post(
        f"/api/v1/accounts/{account}/rule-sets", json=rule_set(marker="to_sort")
    )
    assert (response.status_code, response.json()["detail"]["code"]) == (502, "unreachable")


@pytest.mark.parametrize(
    "body",
    [
        rule_set(trigger="scheduled"),
        rule_set(trigger="manual", interval_minutes=5),
    ],
    ids=["scheduled without interval", "interval without schedule"],
)
def test_only_a_scheduled_rule_set_has_an_interval(
    user_client: TestClient, account: int, body: dict[str, Any]
) -> None:
    response = user_client.post(f"/api/v1/accounts/{account}/rule-sets", json=body)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_rule_set"


@pytest.mark.parametrize(
    "change",
    [{"trigger": "scheduled"}, {"interval_minutes": 5}],
    ids=["scheduled without interval", "interval without schedule"],
)
def test_a_change_keeps_the_interval_with_the_schedule(
    user_client: TestClient, rule_set_id: int, change: dict[str, Any]
) -> None:
    response = user_client.patch(f"/api/v1/rule-sets/{rule_set_id}", json=change)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_rule_set"


def test_a_rule_set_needs_one_of_the_users_accounts(user_client: TestClient) -> None:
    response = user_client.post("/api/v1/accounts/1/rule-sets", json=rule_set())
    assert response.status_code == 404
    assert user_client.get("/api/v1/accounts/1/rule-sets").status_code == 404
    assert user_client.get("/api/v1/accounts/nope/rule-sets").status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        rule_set(folder=[]),
        rule_set(folder=[""]),
        rule_set(folder=["INBOX\r\nA1 LOGOUT"]),
        rule_set(trigger="hourly"),
        rule_set(name=""),
        rule_set(trigger="scheduled", interval_minutes=0),
        rule_set(trigger="scheduled", interval_minutes=7 * 24 * 60 + 1),
    ],
    ids=[
        "no folder",
        "empty segment",
        "control character",
        "trigger",
        "name",
        "no interval",
        "interval over a week",
    ],
)
def test_rule_set_fields_are_checked(
    user_client: TestClient, account: int, body: dict[str, Any]
) -> None:
    assert user_client.post(f"/api/v1/accounts/{account}/rule-sets", json=body).status_code == 422


def test_filters_are_kept_in_order(user_client: TestClient, rule_set_id: int) -> None:
    first = add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    unread = {"criterion": {"type": "is_unread"}, "action": {"type": "star"}}
    second = add_filter(user_client, rule_set_id, "Star unread", unread)
    third = add_filter(
        user_client,
        rule_set_id,
        "Bank",
        {"criterion": {"type": "from_contains", "args": {"addr": "bank"}}, "action": TO_ARCHIVE},
    )
    path = f"/api/v1/rule-sets/{rule_set_id}"

    def order() -> list[tuple[int, int]]:
        return [(f["id"], f["position"]) for f in user_client.get(path).json()["filters"]]

    assert order() == [(first, 0), (second, 1), (third, 2)]

    response = user_client.put(f"{path}/order", json={"filter_ids": [third, first, second]})
    assert response.status_code == 200
    assert [f["id"] for f in response.json()] == [third, first, second]
    assert order() == [(third, 0), (first, 1), (second, 2)]

    for wrong in ([third, first], [third, first, second, second], [third, first, 999]):
        response = user_client.put(f"{path}/order", json={"filter_ids": wrong})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid_order"

    response = user_client.patch(f"/api/v1/filters/{first}", json={"enabled": False, "name": "S"})
    assert (response.json()["enabled"], response.json()["name"]) == (False, "S")
    assert user_client.delete(f"/api/v1/filters/{first}").status_code == 204
    assert order() == [(third, 0), (second, 1)]
    assert user_client.delete(f"/api/v1/filters/{first}").status_code == 404
    assert add_filter(user_client, rule_set_id, "Again", SHOP_SPEC) not in {second, third}
    assert order()[-1][1] == 2


def test_specs_are_kept_as_the_engine_serialises_them(
    user_client: TestClient, rule_set_id: int
) -> None:
    nested = {
        "criterion": {"and": [{"and": [FROM_SHOP, {"type": "is_read"}]}, {"type": "is_unstarred"}]},
        "action": TO_ARCHIVE,
    }
    filter_id = add_filter(user_client, rule_set_id, "Nested", nested)
    stored = user_client.get(f"/api/v1/rule-sets/{rule_set_id}").json()["filters"][0]
    assert stored["id"] == filter_id
    assert stored["spec"] == load_filter(nested).to_spec()
    assert len(stored["spec"]["criterion"]["and"]) == 3


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"criterion": FROM_SHOP}, 'a filter spec has exactly "criterion" and "action"'),
        ({"criterion": {"type": "from_iz"}, "action": TO_ARCHIVE}, "unknown criterion type"),
        (
            {"criterion": {"type": "older_than_days", "args": {"days": -1}}, "action": TO_ARCHIVE},
            "invalid args for 'older_than_days'",
        ),
        (
            {
                "criterion": {"type": "subject_matches", "args": {"pattern": 5}},
                "action": TO_ARCHIVE,
            },
            "invalid args for 'subject_matches'",
        ),
        (
            {
                "criterion": {"type": "subject_matches", "args": {"pattern": "("}},
                "action": TO_ARCHIVE,
            },
            "invalid regular expression",
        ),
        (
            {
                "criterion": {"type": "anything"},
                "action": {
                    "seq": [
                        {"type": "move_to_account", "args": {"account": 2, "folder": ["X"]}},
                        {"type": "trash"},
                    ]
                },
            },
            "a seq cannot have trash after move_to_account",
        ),
    ],
    ids=[
        "no action",
        "unknown type",
        "bad argument",
        "argument type",
        "regular expression",
        "move after a move to another account",
    ],
)
def test_an_invalid_spec_is_refused_with_the_engines_words(
    user_client: TestClient, rule_set_id: int, spec: dict[str, Any], message: str
) -> None:
    response = user_client.post(
        f"/api/v1/rule-sets/{rule_set_id}/filters", json={"name": "Bad", "spec": spec}
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_spec"
    assert message in detail["message"]

    filter_id = add_filter(user_client, rule_set_id, "Good", SHOP_SPEC)
    response = user_client.patch(f"/api/v1/filters/{filter_id}", json={"spec": spec})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_spec"
    stored = user_client.get(f"/api/v1/rule-sets/{rule_set_id}").json()["filters"]
    assert [f["spec"] for f in stored] == [load_filter(SHOP_SPEC).to_spec()]


def test_a_domain_filter_on_a_public_provider_is_refused(
    user_client: TestClient, rule_set_id: int
) -> None:
    public = {
        "criterion": {"type": "from_domain", "args": {"domain": "GMail.com"}},
        "action": TO_ARCHIVE,
    }
    response = user_client.post(
        f"/api/v1/rule-sets/{rule_set_id}/filters", json={"name": "All of Gmail", "spec": public}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "public_provider_domain"

    filter_id = add_filter(user_client, rule_set_id, "Good", SHOP_SPEC)
    response = user_client.patch(f"/api/v1/filters/{filter_id}", json={"spec": public})
    assert response.json()["detail"]["code"] == "public_provider_domain"
    own = {"criterion": {"type": "from_domain", "args": {"domain": "shop.example"}}}
    add_filter(user_client, rule_set_id, "Shop", own | {"action": TO_ARCHIVE})


def test_an_old_filter_on_a_public_provider_can_still_be_edited(
    user_client: TestClient, db_session: Session, rule_set_id: int
) -> None:
    public = load_filter(
        {
            "criterion": {"type": "from_domain", "args": {"domain": "gmail.com"}},
            "action": TO_ARCHIVE,
        }
    ).to_spec()
    row = Filter(rule_set_id=rule_set_id, position=0, name="Old", enabled=True, spec=public)
    db_session.add(row)
    db_session.commit()

    renamed = user_client.patch(f"/api/v1/filters/{row.id}", json={"name": "New", "spec": public})
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["name"], renamed.json()["spec"]) == ("New", public)
    changed = {**public, "action": {"type": "star"}}
    response = user_client.patch(f"/api/v1/filters/{row.id}", json={"spec": changed})
    assert response.json()["detail"]["code"] == "public_provider_domain"


def test_a_search_drafts_a_filter(user_client: TestClient) -> None:
    body = {"account": "*", "folder": "*", "from": "shop.example", "text": "x", "unread": True}
    response = user_client.post("/api/v1/filters/draft-from-search", json=body)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "spec": {
            "criterion": {
                "and": [
                    {"type": "from_contains", "args": {"addr": "shop.example"}},
                    {"type": "is_unread"},
                ]
            },
            "action": {"type": "no_op"},
        },
        "dropped": ["text"],
    }

    response = user_client.post("/api/v1/filters/draft-from-search", json={"text": "x"})
    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "nothing_to_filter",
        "message": "Nothing here that a filter can match on.",
        "dropped": ["text"],
    }
    response = user_client.post("/api/v1/filters/draft-from-search", json={"sorted": True})
    assert response.status_code == 422


def test_a_rule_set_holds_a_hundred_filters_at_most(
    user_client: TestClient, db_session: Session, rule_set_id: int
) -> None:
    spec = load_filter(SHOP_SPEC).to_spec()
    db_session.add_all(
        Filter(rule_set_id=rule_set_id, position=n, name=f"F{n}", enabled=True, spec=spec)
        for n in range(MAX_FILTERS - 1)
    )
    db_session.commit()
    add_filter(user_client, rule_set_id, "Last", SHOP_SPEC)
    response = user_client.post(
        f"/api/v1/rule-sets/{rule_set_id}/filters", json={"name": "One more", "spec": SHOP_SPEC}
    )
    assert (response.status_code, response.json()["detail"]["code"]) == (400, "too_many_filters")
    assert len(user_client.get(f"/api/v1/rule-sets/{rule_set_id}").json()["filters"]) == 100


def test_an_order_lists_only_the_rule_sets_own_filters(
    user_client: TestClient, db_session: Session, account: int, rule_set_id: int
) -> None:
    mine = add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    other = user_client.post(f"/api/v1/accounts/{account}/rule-sets", json=rule_set()).json()["id"]
    elsewhere = add_filter(user_client, other, "Shop", SHOP_SPEC)
    bob = create_user(db_session, "bob", PASSWORD)
    bobs = RuleSet(user_id=bob.id, account_id=account, folder=["INBOX"], name="x", trigger="manual")
    db_session.add(bobs)
    db_session.flush()
    foreign = Filter(rule_set_id=bobs.id, position=0, name="x", spec=SHOP_SPEC)
    db_session.add(foreign)
    db_session.commit()

    # Another rule set's filter, or another user's, is refused like an unknown id.
    for stranger in (elsewhere, foreign.id):
        response = user_client.put(
            f"/api/v1/rule-sets/{rule_set_id}/order", json={"filter_ids": [stranger]}
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid_order"
    order = user_client.get(f"/api/v1/rule-sets/{rule_set_id}").json()["filters"]
    assert [f["id"] for f in order] == [mine]


def test_users_see_only_their_own_filters(
    user_client: TestClient, db_session: Session, account: int, rule_set_id: int
) -> None:
    filter_id = add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    bob = create_user(db_session, "bob", PASSWORD)
    user_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, bob))

    path = f"/api/v1/rule-sets/{rule_set_id}"
    theirs = f"/api/v1/accounts/{account}"
    assert user_client.get(f"{theirs}/folders").status_code == 404
    assert user_client.get(f"{theirs}/filter-settings").status_code == 404
    on = {"run_filters": True}
    assert user_client.put(f"{theirs}/filter-settings", json=on).status_code == 404
    assert user_client.get(f"{theirs}/rule-sets").status_code == 404
    assert user_client.post(f"{theirs}/rule-sets", json=rule_set()).status_code == 404
    assert user_client.get(path).status_code == 404
    assert user_client.patch(path, json={"name": "Mine"}).status_code == 404
    assert user_client.delete(path).status_code == 404
    assert user_client.post(
        f"{path}/filters", json={"name": "x", "spec": SHOP_SPEC}
    ).status_code == (404)
    assert user_client.put(f"{path}/order", json={"filter_ids": [filter_id]}).status_code == 404
    assert user_client.post(f"{path}/dry-run").status_code == 404
    assert user_client.post(f"{path}/run").status_code == 404
    assert user_client.get(f"{path}/runs").status_code == 404
    assert user_client.patch(f"/api/v1/filters/{filter_id}", json={"name": "x"}).status_code == 404
    assert user_client.delete(f"/api/v1/filters/{filter_id}").status_code == 404
    assert db_session.get(Filter, filter_id) is not None


def test_removing_the_account_removes_its_rule_sets(
    user_client: TestClient, db_session: Session, account: int, rule_set_id: int
) -> None:
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    assert user_client.delete(f"/api/v1/accounts/{account}").status_code == 204
    assert db_session.get(RuleSet, rule_set_id) is None
    assert db_session.scalars(select(Filter)).all() == []


def test_removing_the_user_removes_their_rule_sets(
    api_client: TestClient, db_session: Session, user: User, rule_set_id: int
) -> None:
    add_filter(api_client, rule_set_id, "Shop", SHOP_SPEC)
    admin = create_user(db_session, "root", PASSWORD, is_admin=True)
    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, admin))
    assert api_client.delete(f"/api/v1/users/{user.id}").status_code == 204
    assert db_session.get(RuleSet, rule_set_id) is None
    assert db_session.scalars(select(Filter)).all() == []


def jobs(db_session: Session) -> list[dict[str, Any]]:
    """The queued runs' jobs (a real worker in another test may have committed others)."""
    rows = db_session.execute(
        text(
            "SELECT task_name, args, lock, queueing_lock, status "
            f"FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs "
            "WHERE task_name = 'run_rule_set' ORDER BY id"
        )
    )
    return [dict(row) for row in rows.mappings()]


def test_run_now_queues_a_run_for_the_worker(
    user_client: TestClient, db_session: Session, user: User, account: int, rule_set_id: int
) -> None:
    run = f"/api/v1/rule-sets/{rule_set_id}/run"
    response = user_client.post(run)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "run_filters_off"

    settings = f"/api/v1/accounts/{account}/filter-settings"
    assert user_client.put(settings, json={"run_filters": True}).status_code == 200
    response = user_client.post(run)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "rule_set_disabled"

    path = f"/api/v1/rule-sets/{rule_set_id}"
    assert user_client.patch(path, json={"enabled": True}).status_code == 200
    response = user_client.post(run)
    assert response.status_code == 202
    # A rule set of one account answers its run; only one for all accounts answers a list.
    queued = response.json()
    assert queued["account_id"] == account
    assert (queued["trigger"], queued["dry_run"], queued["started_at"], queued["result"]) == (
        "manual",
        False,
        None,
        None,
    )
    assert jobs(db_session) == [
        {
            "task_name": "run_rule_set",
            "args": {"run_id": queued["id"]},
            "lock": f"mailbox:{user.id}:{account}",
            "queueing_lock": f"rule_set:{rule_set_id}:{account}",
            "status": "todo",
        }
    ]

    # One waiting run per rule set; the refused one leaves nothing behind.
    response = user_client.post(run)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "run_pending"
    assert [r["id"] for r in user_client.get(f"{path}/runs").json()] == [queued["id"]]
    assert len(jobs(db_session)) == 1


def test_a_dry_run_says_a_certificate_did_not_verify(user_client: TestClient) -> None:
    """On the filter engine's path too: the stub's TLS certificate is self-signed."""
    with SmtpStub() as stub:
        account = new_account(imap={"host": "127.0.0.1", "port": stub.port, "tls": "implicit"})
        work = added(user_client, account)
        created = user_client.post(f"/api/v1/accounts/{work}/rule-sets", json=rule_set())
        rule_set_id = created.json()["id"]
        add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
        response = user_client.post(f"/api/v1/rule-sets/{rule_set_id}/dry-run")
    unverified = f"{MESSAGES['unreachable']} {CERTIFICATE_DETAIL}"
    assert response.status_code == 502
    assert response.json()["detail"] == {"code": "unreachable", "message": unverified}
    [run] = user_client.get(f"/api/v1/rule-sets/{rule_set_id}/runs").json()
    assert run["error"] == f"unreachable: {unverified}"


def test_a_dry_run_failure_is_answered_and_kept(user_client: TestClient, rule_set_id: int) -> None:
    """The account's IMAP port 9 on loopback is closed."""
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    response = user_client.post(f"/api/v1/rule-sets/{rule_set_id}/dry-run")
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "unreachable"

    [run] = user_client.get(f"/api/v1/rule-sets/{rule_set_id}/runs").json()
    assert run["dry_run"] is True
    assert run["result"] is None
    assert run["error"] == f"unreachable: {MESSAGES['unreachable']}"
    assert run["finished_at"] is not None


def test_a_stored_spec_that_no_longer_loads_is_refused(
    user_client: TestClient, db_session: Session, rule_set_id: int
) -> None:
    filter_id = add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    stored = db_session.get_one(Filter, filter_id)
    stored.spec = {"criterion": {"type": "gone_criterion"}, "action": TO_ARCHIVE}
    db_session.commit()
    response = user_client.post(f"/api/v1/rule-sets/{rule_set_id}/dry-run")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_spec"
    [run] = user_client.get(f"/api/v1/rule-sets/{rule_set_id}/runs").json()
    assert run["error"].startswith("invalid_spec: ")


def test_a_dry_run_whose_account_went_answers_with_its_code(
    user_client: TestClient, rule_set_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The account deleted after the rule set was found: 404 with ``{code, message}``, unlike a
    missing path id."""
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    def gone(*_args: object, **_kwargs: object) -> None:
        raise RunRefused("account_not_found", "No such account")

    monkeypatch.setattr(filters_service, "dry_run_result", gone)
    response = user_client.post(f"/api/v1/rule-sets/{rule_set_id}/dry-run")
    assert response.status_code == 404
    assert response.json()["detail"] == {"code": "account_not_found", "message": "No such account"}
    [run] = user_client.get(f"/api/v1/rule-sets/{rule_set_id}/runs").json()
    assert run["error"].startswith("account_not_found: ")


def test_an_unexpected_dry_run_failure_keeps_its_text_out(
    user_client: TestClient,
    rule_set_id: int,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Its text may quote anything, mail included: the run and the log name only its kind."""
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    def failing(*_args: object, **_kwargs: object) -> None:
        msg = "Subject: a private matter"
        raise TypeError(msg)

    monkeypatch.setattr(filter_runs.Folder, "run", failing)
    caplog.set_level(logging.INFO)
    response = user_client.post(f"/api/v1/rule-sets/{rule_set_id}/dry-run")
    assert response.status_code == 500
    assert response.json()["detail"] == {"code": "run_failed", "message": "TypeError"}
    [run] = user_client.get(f"/api/v1/rule-sets/{rule_set_id}/runs").json()
    assert (run["error"], run["result"]) == ("run_failed: TypeError", None)
    assert run["finished_at"] is not None
    assert "private matter" not in caplog.text


def test_a_pattern_out_of_time_fails_the_dry_run(
    user_client: TestClient, rule_set_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    def slow(*_args: object, **_kwargs: object) -> None:
        msg = "regular expression '(a|aa)+$' timed out after 1.0s"
        raise MatchTimeoutError(msg)

    monkeypatch.setattr(filter_runs.Folder, "run", slow)
    response = user_client.post(f"/api/v1/rule-sets/{rule_set_id}/dry-run")
    assert (response.status_code, response.json()["detail"]["code"]) == (400, "match_timeout")
    [run] = user_client.get(f"/api/v1/rule-sets/{rule_set_id}/runs").json()
    assert run["error"].startswith("match_timeout: ")
    assert run["result"] is None


def test_a_user_runs_one_dry_run_at_a_time(
    user_client: TestClient,
    user: User,
    db_session: Session,
    rule_set_id: int,
    application: Any,
) -> None:
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    path = f"/api/v1/rule-sets/{rule_set_id}"
    with dry_run_slot(application.state.database_engine, user.id):
        response = user_client.post(f"{path}/dry-run")
        assert (response.status_code, response.json()["detail"]["code"]) == (
            429,
            "dry_run_busy",
        )
        # Another user's dry run is not held back.
        bob = create_user(db_session, "bob", PASSWORD)
        with dry_run_slot(application.state.database_engine, bob.id):
            pass
    assert user_client.get(f"{path}/runs").json() == []
    # Free again once the first one ends (port 9 is closed: the mailbox's refusal).
    assert user_client.post(f"{path}/dry-run").status_code == 502


def test_a_dry_run_needs_a_password_that_opens(
    user_client: TestClient, rule_set_id: int, application: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(application.state, "secrets_subkey", None)
    response = user_client.post(f"/api/v1/rule-sets/{rule_set_id}/dry-run")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "secrets_key_missing"


def test_old_finished_runs_are_pruned(db_session: Session, user: User) -> None:
    rule_set_row = RuleSet(user_id=user.id, account_id=1, folder=["INBOX"], name="x")
    rule_set_row.trigger = "manual"
    db_session.add(rule_set_row)
    db_session.flush()
    now = datetime.now(UTC)
    runs = [
        FilterRun(
            rule_set_id=rule_set_row.id,
            account_id=1,
            trigger="manual",
            dry_run=True,
            finished_at=now,
        )
        for _ in range(5)
    ]
    # Queued: never pruned, however many runs come after it.
    runs[1].dry_run = False
    runs[1].finished_at = None
    # Another account's runs of the same rule set count on their own.
    other = FilterRun(
        rule_set_id=rule_set_row.id, account_id=2, trigger="manual", dry_run=True, finished_at=now
    )
    db_session.add(other)
    db_session.flush()
    db_session.add_all(runs)
    db_session.flush()
    prune_runs(db_session, rule_set_row.id, 1, keep=2)
    kept = db_session.scalars(select(FilterRun.id).order_by(FilterRun.id)).all()
    assert kept == [other.id, runs[1].id, runs[3].id, runs[4].id]


def test_match_counts_skip_dry_runs_and_old_days(db_session: Session, user: User) -> None:
    rule_set_row = RuleSet(user_id=user.id, account_id=1, folder=["INBOX"], name="x")
    rule_set_row.trigger = "manual"
    db_session.add(rule_set_row)
    db_session.flush()
    row = Filter(rule_set_id=rule_set_row.id, position=0, name="x", spec=SHOP_SPEC)
    db_session.add(row)
    db_session.flush()
    entry = {"filter_id": row.id, "matched": [{"uid": 1}]}
    count_matches(db_session, {"dry_run": True, "filters": [entry]})
    # A filter removed while it ran is not counted.
    count_matches(db_session, {"dry_run": False, "filters": [entry, {**entry, "filter_id": 999}]})
    count_matches(db_session, {"dry_run": False, "filters": [{**entry, "matched_total": 250}]})
    assert matches_counted(db_session, [row.id, 999]) == {row.id: 251}

    today = datetime.now(UTC).date()
    for age in (MATCH_DAYS - 1, MATCH_DAYS):
        db_session.add(FilterMatchDay(filter_id=row.id, day=today - timedelta(days=age), count=7))
    db_session.flush()
    assert matches_counted(db_session, [row.id]) == {row.id: 258}
    prune_runs(db_session, rule_set_row.id, 1)
    days = db_session.scalars(select(FilterMatchDay.day).order_by(FilterMatchDay.day)).all()
    assert days == [today - timedelta(days=MATCH_DAYS - 1), today]


def test_a_report_carries_no_nul() -> None:
    row = Filter(id=7, rule_set_id=1, position=0, name="x", spec=SHOP_SPEC)
    entry = {"spec": None, "action": "x", "matched": [{"uid": 1, "subject": "a\x00b"}]}
    report: Any = {"folder": ["IN\x00BOX"], "filters": [entry]}
    labelled = labelled_report(report, [row], [load_filter(SHOP_SPEC)])
    assert labelled["folder"] == ["INBOX"]
    assert labelled["filters"][0]["matched"][0]["subject"] == "ab"


def test_a_dry_run_and_a_real_run_name_an_action_alike() -> None:
    spec = {"criterion": FROM_SHOP, "action": {"type": "star"}}
    row = Filter(id=7, rule_set_id=1, position=0, name="x", spec=spec)
    # The filter engine writes the repr of a filter it can serialise (a real run's) and the name of one
    # it cannot (a dry run's, bounded to the newest messages).
    labels = {
        labelled_report(
            {"folder": ["INBOX"], "filters": [{"spec": None, "action": written, "matched": []}]},
            [row],
            [load_filter(spec)],
        )["filters"][0]["action"]
        for written in ("Star()", "star")
    }
    assert labels == {"star"}


def code_of(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["detail"]["code"]


def test_a_rule_set_for_all_accounts_runs_on_inbox_of_each_running_account(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = added(user_client, new_account("work"))
    home = added(user_client, new_account("home"))
    every = "/api/v1/accounts/all/rule-sets"
    elsewhere = user_client.post(every, json=rule_set(folder=["Archive"]))
    assert code_of(elsewhere) == (400, "all_accounts_inbox_only")
    to_sort = user_client.post(every, json=rule_set(marker="to_sort"))
    assert code_of(to_sort) == (400, "to_sort_unsupported")
    assert user_client.get("/api/v1/accounts/any/rule-sets").status_code == 422

    created = user_client.post(every, json=rule_set(folder=["inbox"]))
    assert created.status_code == 201, created.json()
    assert (created.json()["account_id"], created.json()["folder"]) == ("all", ["INBOX"])
    rule_set_id = created.json()["id"]
    assert [row["id"] for row in user_client.get(every).json()] == [rule_set_id]
    assert user_client.get(f"/api/v1/accounts/{work}/rule-sets").json() == []
    path = f"/api/v1/rule-sets/{rule_set_id}"
    assert user_client.get(path).json()["account_id"] == "all"
    assert code_of(user_client.patch(path, json={"folder": ["Archive"]})) == (
        400,
        "all_accounts_inbox_only",
    )
    assert code_of(user_client.patch(path, json={"marker": "to_sort"})) == (
        400,
        "to_sort_unsupported",
    )
    marks = {"criterion": FROM_SHOP, "action": {"type": "mark_to_sort"}}
    added_marks = user_client.post(f"{path}/filters", json={"name": "Mark", "spec": marks})
    assert code_of(added_marks) == (400, "to_sort_unsupported")
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    assert code_of(user_client.post(f"{path}/dry-run")) == (400, "account_required")
    assert user_client.post(f"{path}/dry-run", params={"account_id": 999}).status_code == 404

    run = f"{path}/run"
    assert code_of(user_client.post(run)) == (400, "run_filters_off")
    for account in (work, home):
        settings = f"/api/v1/accounts/{account}/filter-settings"
        assert user_client.put(settings, json={"run_filters": True}).status_code == 200
    assert code_of(user_client.post(run)) == (400, "rule_set_disabled")
    assert user_client.patch(path, json={"enabled": True}).status_code == 200

    one = user_client.post(run, params={"account_id": home})
    assert one.status_code == 202
    assert [queued["account_id"] for queued in one.json()] == [home]
    # Every running account but the one whose run still waits.
    rest = user_client.post(run)
    assert [queued["account_id"] for queued in rest.json()] == [work]
    assert code_of(user_client.post(run)) == (409, "run_pending")
    assert [job["queueing_lock"] for job in jobs(db_session)] == [
        f"rule_set:{rule_set_id}:{home}",
        f"rule_set:{rule_set_id}:{work}",
    ]
    assert [job["lock"] for job in jobs(db_session)] == [
        f"mailbox:{user.id}:{home}",
        f"mailbox:{user.id}:{work}",
    ]
    listed = user_client.get(f"{path}/runs").json()
    assert [entry["account_id"] for entry in listed] == [work, home]

    # Removing an account forgets the runs on it, its mail named in their reports.
    assert user_client.delete(f"/api/v1/accounts/{home}").status_code == 204
    left = user_client.get(f"{path}/runs").json()
    assert [entry["account_id"] for entry in left] == [work]


def test_a_rule_set_of_one_account_runs_on_that_account_only(
    user_client: TestClient, account: int, rule_set_id: int
) -> None:
    other = added(user_client, new_account("home"))
    path = f"/api/v1/rule-sets/{rule_set_id}"
    for action in ("dry-run", "run"):
        response = user_client.post(f"{path}/{action}", params={"account_id": other})
        assert code_of(response) == (400, "wrong_account"), action
