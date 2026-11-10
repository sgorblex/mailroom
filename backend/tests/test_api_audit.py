from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from mailroom.auth.users import create_user
from mailroom.models import AuditRecord, RuleSet
from mailroom.oauth.models import OAuthClient

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User

AUDIT = "/api/v1/me/audit"


def add(db_session: Session, user_id: int, **fields: Any) -> int:
    record = AuditRecord(
        user_id=user_id,
        **{
            "actor": "user",
            "action": "move",
            "account_id": "work",
            "result": "ok",
            "detail": {"folder": "INBOX", "uid": 1, "from": "ana@example.org"},
            **fields,
        },
    )
    db_session.add(record)
    db_session.flush()
    return record.id


def listed(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get(AUDIT, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.get(AUDIT).status_code == 401


def test_records_come_newest_first_a_page_at_a_time(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    ids = [add(db_session, user.id) for _ in range(5)]
    first = listed(user_client, limit=2)
    assert [r["id"] for r in first["records"]] == ids[:-3:-1]
    second = listed(user_client, limit=2, cursor=first["next_cursor"])
    assert [r["id"] for r in second["records"]] == ids[2:0:-1]
    last = listed(user_client, limit=2, cursor=second["next_cursor"])
    assert ([r["id"] for r in last["records"]], last["next_cursor"]) == ([ids[0]], None)
    assert first["records"][0]["detail"] == {"folder": "INBOX", "uid": 1, "from": "ana@example.org"}


@pytest.mark.parametrize("cursor", ["x1", "²", "1" * 19, "-3"])
def test_a_cursor_this_list_did_not_give_is_refused(user_client: TestClient, cursor: str) -> None:
    response = user_client.get(AUDIT, params={"cursor": cursor})
    assert (response.status_code, response.json()["detail"]["code"]) == (400, "invalid_cursor")


def test_the_filters_narrow_the_list(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    move = add(db_session, user.id)
    sent = add(db_session, user.id, actor="agent", action="send", account_id="home")
    refused = add(db_session, user.id, actor="agent", action="trash", result="invalid_argument")

    def ids(**params: Any) -> list[int]:
        return [r["id"] for r in listed(user_client, **params)["records"]]

    assert ids(account_id="home") == [sent]
    assert ids(actor="agent") == [refused, sent]
    assert ids(action="move") == [move]
    assert ids(result="failed") == [refused]
    assert ids(result="invalid_argument", actor="agent") == [refused]


def test_a_user_sees_only_their_own(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    other = create_user(db_session, "someone-else", "a long enough password")
    add(db_session, other.id)
    mine = add(db_session, user.id)
    assert [r["id"] for r in listed(user_client)["records"]] == [mine]


@pytest.mark.parametrize("named", [True, False])
def test_an_agents_record_names_its_app_while_the_app_is_known(
    user_client: TestClient, db_session: Session, user: User, named: bool
) -> None:
    db_session.add(
        OAuthClient(
            client_id="https://claude.ai/client",
            kind="cimd",
            client_name="Claude" if named else None,
            redirect_uris=[],
            metadata_={},
        )
    )
    add(db_session, user.id, actor="agent", client_id="https://claude.ai/client")
    add(db_session, user.id, actor="agent", client_id="gone-client")
    gone, known = listed(user_client)["records"]
    assert known["app"] == ("Claude" if named else "https://claude.ai/client")
    assert (gone["app"], gone["client_id"]) == ("gone-client", "gone-client")


def test_a_filters_record_names_its_rule_set_while_it_exists(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    rule_set = RuleSet(
        user_id=user.id, account_id="work", folder=["INBOX"], name="Shops", trigger="manual"
    )
    db_session.add(rule_set)
    db_session.flush()
    add(db_session, user.id, actor="filter", rule_set_id=rule_set.id, run_id=3)
    add(db_session, user.id, actor="filter", rule_set_id=rule_set.id + 1000, run_id=4)
    other = create_user(db_session, "someone-else", "a long enough password")
    theirs = RuleSet(
        user_id=other.id, account_id="work", folder=["INBOX"], name="Theirs", trigger="manual"
    )
    db_session.add(theirs)
    db_session.flush()
    add(db_session, user.id, actor="filter", rule_set_id=theirs.id, run_id=5)
    foreign, gone, kept = listed(user_client)["records"]
    assert (kept["rule_set"], kept["run_id"]) == ("Shops", 3)
    assert gone["rule_set"] is None
    # Another user's rule set is never named, even under a record's id.
    assert foreign["rule_set"] is None
