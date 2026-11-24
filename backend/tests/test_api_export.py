"""The user's data as files: ``/me/export/rule-sets`` and ``/me/export/audit.csv``."""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from mailroom.api.main import create_application
from mailroom.audit import listing
from mailroom.audit.export import COLUMNS
from mailroom.auth.users import create_user
from mailroom.config.features import Features
from mailroom.filters.spec import normalized_spec
from mailroom.mcp.groups import GROUPS
from mailroom.models import Filter, RuleSet
from mailroom.oauth.models import OAuthClient
from mailroom.observability.logging_config import JsonFormatter
from mailroom.reach import WHOLE, Reach
from tests.test_api_audit import add
from tests.test_api_filters import SHOP_SPEC
from tests.test_api_tokens import add_accounts, made, script

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("numbered_accounts")]

RULE_SETS = "/api/v1/me/export/rule-sets"
AUDIT = "/api/v1/me/export/audit.csv"


@pytest.fixture(autouse=True)
def _every_group(
    application: FastAPI, api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = api_client
    monkeypatch.setattr(application.state, "tool_groups", GROUPS)


def move_to(account: int) -> dict[str, Any]:
    return {"type": "move_to_account", "args": {"account": account, "folder": ["INBOX"]}}


def add_rule_set(
    db: Session, user_id: int, account: int | None, *specs: dict[str, Any], **fields: Any
) -> int:
    row = RuleSet(
        **{
            "user_id": user_id,
            "account_id": account,
            "folder": ["INBOX"],
            "name": "Shops",
            "trigger": "manual",
            **fields,
        }
    )
    db.add(row)
    db.flush()
    # Last first: the export follows their positions.
    for position, spec in reversed(list(enumerate(specs))):
        db.add(
            Filter(
                rule_set_id=row.id,
                position=position,
                name=f"f{position}",
                enabled=position == 0,
                spec=spec,
            )
        )
        db.flush()
    db.commit()
    return row.id


def downloaded(response: httpx.Response, stem: str, suffix: str) -> httpx.Response:
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith(f'attachment; filename="mailroom-{stem}-20')
    assert disposition.endswith(f'.{suffix}"')
    return response


def exported(client: TestClient) -> list[dict[str, Any]]:
    response = downloaded(client.get(RULE_SETS), "rule-sets", "json")
    assert response.headers["content-type"] == "application/json"
    document = response.json()
    assert (document["format"], document["version"]) == ("mailroom-rule-sets", 1)
    assert datetime.fromisoformat(document["exported_at"]).tzinfo is not None
    rule_sets: list[dict[str, Any]] = document["rule_sets"]
    return rule_sets


def rows(client: TestClient, **params: str) -> list[dict[str, str]]:
    response = downloaded(client.get(AUDIT, params=params), "audit", "csv")
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    reader = csv.DictReader(io.StringIO(response.text, newline=""))
    assert tuple(reader.fieldnames or ()) == COLUMNS
    return list(reader)


@pytest.mark.parametrize("path", [RULE_SETS, AUDIT])
def test_signed_out_browsers_are_refused(api_client: TestClient, path: str) -> None:
    assert api_client.get(path).status_code == 401


@pytest.mark.parametrize("path", [RULE_SETS, AUDIT])
def test_an_export_leaves_a_line(
    user_client: TestClient, user: User, caplog: pytest.LogCaptureFixture, path: str
) -> None:
    with caplog.at_level(logging.INFO):
        user_client.get(path)
    [line] = [r for r in caplog.records if r.message == "data_exported"]
    what = "rule_sets" if path == RULE_SETS else "audit"
    written = json.loads(JsonFormatter().format(line))
    assert (written["event"], written["user_id"], written["token_id"], written["what"]) == (
        "data_exported",
        user.id,
        None,
        what,
    )


# Rule sets


def test_rule_sets_name_accounts_by_login_and_hold_no_id(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    add_accounts(user_client, "work", "home")
    work, home = (
        {"login": account["login"], "name": account["name"]}
        for account in user_client.get("/api/v1/accounts").json()
    )
    assert work != home
    moving = {
        "criterion": {"not": {"type": "is_unread"}},
        "action": {"seq": [{"type": "mark_as_read"}, move_to(2)]},
    }
    specs = [normalized_spec(spec) for spec in (SHOP_SPEC, moving)]
    add_rule_set(
        db_session,
        user.id,
        1,
        *specs,
        folder=["Lists", "Caffè"],
        trigger="scheduled",
        interval_minutes=30,
        marker="star",
        enabled=True,
    )
    add_rule_set(db_session, user.id, None, name="Everywhere")

    first, everywhere = exported(user_client)
    filters = first.pop("filters")
    assert first == {
        "name": "Shops",
        "account": work,
        "folder": ["Lists", "Caffè"],
        "trigger": "scheduled",
        "interval_minutes": 30,
        "marker": "star",
        "enabled": True,
    }
    assert filters[0] == {"name": "f0", "enabled": True, "spec": specs[0]}
    assert (filters[1]["name"], filters[1]["enabled"]) == ("f1", False)
    assert filters[1]["spec"]["criterion"] == specs[1]["criterion"]
    [read, moved] = filters[1]["spec"]["action"]["seq"]
    assert read["type"] == "mark_as_read"
    assert (moved["type"], moved["args"]["account"]) == ("move_to_account", home)
    assert moved["args"]["folder"] == ["INBOX"]
    assert (everywhere["account"], everywhere["filters"]) == ("all", [])


def test_an_account_gone_is_not_named(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    spec = {"criterion": {"type": "is_unread"}, "action": move_to(9)}
    add_rule_set(db_session, user.id, 8, spec)
    [rule_set] = exported(user_client)
    assert rule_set["account"] is None
    assert rule_set["filters"][0]["spec"]["action"]["args"]["account"] is None


def test_the_hidden_rule_sets_and_another_users_are_left_out(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    assert exported(user_client) == []
    add_rule_set(db_session, user.id, 1, SHOP_SPEC, kind="mute", name="Muted")
    other = create_user(db_session, "someone-else", "a long enough password")
    add_rule_set(db_session, other.id, 1, SHOP_SPEC, name="Theirs")
    assert exported(user_client) == []


@pytest.mark.parametrize(
    ("accounts", "names"),
    [
        (None, ["Work", "To home", "Home", "Everywhere"]),
        ([1], ["Work"]),
        ([1, 2], ["Work", "To home", "Home"]),
        ([], []),
    ],
)
def test_a_token_exports_the_rule_sets_within_its_reach(
    user_client: TestClient,
    application: FastAPI,
    db_session: Session,
    user: User,
    accounts: list[int] | None,
    names: list[str],
) -> None:
    add_accounts(user_client, "work", "home")
    add_rule_set(db_session, user.id, 1, SHOP_SPEC, name="Work")
    moving = {"criterion": {"type": "is_unread"}, "action": move_to(2)}
    add_rule_set(db_session, user.id, 1, moving, name="To home")
    add_rule_set(db_session, user.id, 2, SHOP_SPEC, name="Home")
    add_rule_set(db_session, user.id, None, SHOP_SPEC, name="Everywhere")
    reach = WHOLE if accounts is None else Reach(frozenset(accounts))
    client = script(application, made(db_session, user, ["filters_read"], reach)[1])
    assert [rule_set["name"] for rule_set in exported(client)] == names


@pytest.mark.parametrize(("path", "group"), [(RULE_SETS, "filters_read"), (AUDIT, "audit_read")])
def test_a_token_needs_the_group(
    application: FastAPI, db_session: Session, user: User, path: str, group: str
) -> None:
    others = [each.name for each in GROUPS if each.name != group]
    refused = script(application, made(db_session, user, others)[1]).get(path)
    assert (refused.status_code, refused.json()["detail"]["code"]) == (403, "token_not_approved")
    assert script(application, made(db_session, user, [group])[1]).get(path).status_code == 200


# The audit trail


def test_the_trail_is_a_row_per_record_newest_first(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    add_accounts(user_client, "work")
    [account] = user_client.get("/api/v1/accounts").json()
    rule_set = add_rule_set(db_session, user.id, 1, name="Caffè, shops")
    detail = {
        "folder": "INBOX",
        "from": 'Ana "A" <ana@example.org>',
        "subject_sha256": "ab" * 32,
        "note": "two\nlines, é",
    }
    at = datetime(2026, 11, 20, 10, 30, tzinfo=UTC)
    first = add(db_session, user.id, at=at, detail=detail)
    second = add(db_session, user.id, actor="filter", rule_set_id=rule_set, run_id=4, account_id=7)

    newest, oldest = rows(user_client)
    assert (newest["id"], oldest["id"]) == (str(second), str(first))
    assert json.loads(oldest.pop("detail")) == detail
    assert oldest == {
        "id": str(first),
        "at": "2026-11-20T10:30:00+00:00",
        "actor": "user",
        "app": "",
        "client_id": "",
        "grant_id": "",
        "token_id": "",
        "rule_set": "",
        "rule_set_id": "",
        "run_id": "",
        "action": "move",
        "account_id": "1",
        "account": account["login"],
        "result": "ok",
    }
    assert (newest["rule_set"], newest["run_id"]) == ("Caffè, shops", "4")
    # An account removed since, or never there.
    assert (newest["account_id"], newest["account"]) == ("7", "")


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("=1+1", "'=1+1"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\t=1", "'\t'=1"),
        ("\r=1", "'\r'=1"),
        ("＝1", "'＝1"),  # noqa: RUF001
        # A spreadsheet that splits on ; or a tab, or reads the quotes otherwise.
        ("x;=1+1", "x;'=1+1"),
        ("x;;+1;-1\t@A1", "x;;'+1;'-1\t'@A1"),
        ("x;＝1", "x;'＝1"),  # noqa: RUF001
        ('x;"=1+1;', "x;'\"=1+1;"),
        ("x; =1", "x;' =1"),
        ("x\n=1;", "x\n'=1;"),
        ("a-b; c=d", "a-b; c=d"),
    ],
)
def test_text_a_spreadsheet_would_run_is_quoted(
    user_client: TestClient, db_session: Session, user: User, name: str, shown: str
) -> None:
    db_session.add(
        OAuthClient(
            client_id="https://evil.example",
            kind="cimd",
            client_name=name,
            redirect_uris=[],
            metadata_={},
        )
    )
    add(db_session, user.id, actor="agent", client_id="https://evil.example")
    [row] = rows(user_client)
    assert row["app"] == shown


def test_the_detail_holds_nothing_a_spreadsheet_would_split_on(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    detail = {"from": '"x;=1+1"@example.org', "to": ["a\t=1;@b"]}
    add(db_session, user.id, detail=detail)
    [row] = rows(user_client)
    assert ";" not in row["detail"]
    assert "\t" not in row["detail"]
    assert json.loads(row["detail"]) == detail


def test_a_long_trail_comes_whole_in_several_pages(
    user_client: TestClient,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(listing, "EXPORT_PAGE", 2)
    for count in range(5):
        assert len(rows(user_client)) == count
        add(db_session, user.id)
    ids = [int(row["id"]) for row in rows(user_client)]
    assert len(ids) == 5
    assert ids == sorted(ids, reverse=True)


def test_a_page_is_read_only_once_the_one_before_is_taken(
    db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(listing, "EXPORT_PAGE", 1)
    for _ in range(3):
        add(db_session, user.id)
    reads = 0
    read = listing._listed_rows  # noqa: SLF001

    def counted(*args: Any) -> Any:
        nonlocal reads
        reads += 1
        return read(*args)

    monkeypatch.setattr(listing, "_listed_rows", counted)
    pages = listing.record_pages(db_session, user.id, reach=WHOLE)
    assert reads == 0
    next(pages)
    assert reads == 1
    # One added meanwhile is newer than where the export is: not in it, nor any row twice.
    add(db_session, user.id)
    assert len(list(pages)) == 2


def test_the_days_are_the_users_both_included(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    user.time_zone = "Asia/Tokyo"  # UTC+9
    db_session.flush()
    early = add(db_session, user.id, at=datetime(2026, 11, 19, 14, 59, tzinfo=UTC))
    start = add(db_session, user.id, at=datetime(2026, 11, 19, 15, 0, tzinfo=UTC))
    end = add(db_session, user.id, at=datetime(2026, 11, 21, 14, 59, 59, tzinfo=UTC))
    late = add(db_session, user.id, at=datetime(2026, 11, 21, 15, 0, tzinfo=UTC))

    def ids(**params: str) -> list[int]:
        return [int(row["id"]) for row in rows(user_client, **params)]

    assert ids(since="2026-11-20", until="2026-11-21") == [end, start]
    assert ids(since="2026-11-20", until="2026-11-20") == [start]
    assert ids(since="2026-11-20") == [late, end, start]
    assert ids(until="2026-11-21") == [end, start, early]
    assert ids(since="2027-01-01") == []


@pytest.mark.parametrize(
    "params",
    [
        {"since": "2026-11-21", "until": "2026-11-20"},
        {"until": "9999-12-31"},
        {"since": "0001-01-01"},
    ],
)
def test_days_that_make_no_range_are_refused(
    user_client: TestClient, db_session: Session, user: User, params: dict[str, str]
) -> None:
    user.time_zone = "Asia/Tokyo"
    db_session.flush()
    response = user_client.get(AUDIT, params=params)
    assert (response.status_code, response.json()["detail"]["code"]) == (400, "invalid_range")


@pytest.mark.parametrize("params", [{"since": "yesterday"}, {"until": "2026-13-01"}])
def test_what_is_no_day_is_refused(user_client: TestClient, params: dict[str, str]) -> None:
    assert user_client.get(AUDIT, params=params).status_code == 422


def test_a_user_exports_only_their_own_records(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    other = create_user(db_session, "someone-else", "a long enough password")
    add(db_session, other.id)
    mine = add(db_session, user.id)
    assert [row["id"] for row in rows(user_client)] == [str(mine)]


def test_a_token_exports_the_records_within_its_reach(
    user_client: TestClient, application: FastAPI, db_session: Session, user: User
) -> None:
    add_accounts(user_client, "work", "home")
    everywhere = add_rule_set(db_session, user.id, None, name="Everywhere")
    within = add(db_session, user.id, detail={"uid": 1, "rule_sets": [everywhere]})
    add(db_session, user.id, account_id=2)
    add(db_session, user.id, detail={"uid": 3, "destination_account": 2})
    ran = add(db_session, user.id, actor="filter", rule_set_id=everywhere, run_id=5)
    client = script(application, made(db_session, user, ["audit_read"], Reach(frozenset({1})))[1])
    newest, oldest = rows(client)
    assert (newest["id"], oldest["id"]) == (str(ran), str(within))
    assert (newest["rule_set"], newest["rule_set_id"], newest["run_id"]) == ("", "", "")
    assert json.loads(oldest["detail"]) == {"uid": 1}


@pytest.mark.parametrize(
    ("features", "there"),
    [
        (Features(mail=True, mcp=False, filters=False), {AUDIT}),
        (Features(mail=False, mcp=True, filters=False), {AUDIT}),
        (Features(mail=False, mcp=False, filters=True), {AUDIT, RULE_SETS}),
        (Features(mail=False, mcp=False, filters=False), set()),
    ],
)
def test_the_exports_are_where_their_features_are(features: Features, there: set[str]) -> None:
    paths = create_application(features=features).openapi()["paths"]
    assert {path for path in (AUDIT, RULE_SETS) if path in paths} == there
