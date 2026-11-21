"""Each identity's signature (``docs/requirements/mail-client.md``, *Compose*): kept sanitised with
its text, only for the account's identities, only the user's own accounts, and forgotten with the
account and the user."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from mailroom.accounts.app_data import forget_user_accounts
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.mail import signatures
from mailroom.models import IdentitySignature
from tests.test_api_accounts import added, new_account

if TYPE_CHECKING:
    import pytest
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User


def _url(account_id: int) -> str:
    return f"/api/v1/accounts/{account_id}/signatures"


def _two_identities(client: TestClient) -> int:
    identities = [
        {"address": "me@example.org", "name": "Me", "default": True},
        {"address": "Alias@example.org", "name": "Alias"},
    ]
    return added(client, new_account("work", identities=identities))


def _rows(db_session: Session, user_id: int) -> list[IdentitySignature]:
    db_session.expire_all()
    return list(
        db_session.scalars(select(IdentitySignature).where(IdentitySignature.user_id == user_id))
    )


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.get(_url(1)).status_code == 401
    assert api_client.put(_url(1), json={"address": "a@x.org", "html": "x"}).status_code == 401


def test_each_identity_keeps_a_sanitised_signature_with_its_text(
    user_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    work = _two_identities(user_client)
    assert user_client.get(_url(work)).json() == [
        {"address": "me@example.org", "html": "", "text": ""},
        {"address": "Alias@example.org", "html": "", "text": ""},
    ]

    caplog.set_level(logging.INFO)
    html = (
        "<p onclick='x()'>Ada <b>Lovelace</b><script>alert(1)</script></p>"
        '<p><a href="https://ada.example">site</a> <a href="javascript:x()">j</a>'
        '<img src="https://tracker.example/p.gif"></p>'
        "<div data-mailroom-signature><p>nested</p></div>"
    )
    answer = user_client.put(_url(work), json={"address": "alias@EXAMPLE.org", "html": html})
    assert answer.status_code == 200, answer.text
    kept = answer.json()
    assert kept["address"] == "Alias@example.org"
    assert kept["html"] == (
        '<p>Ada <b>Lovelace</b></p><p><a href="https://ada.example">site</a> <a>j</a></p>'
        "<div><p>nested</p></div>"
    )
    assert kept["text"].split() == ["Ada", "Lovelace", "[site](https://ada.example)", "j", "nested"]
    assert user_client.get(_url(work)).json()[1] == kept
    assert "Lovelace" not in caplog.text
    assert "example.org" not in caplog.text

    cleared = user_client.put(_url(work), json={"address": "Alias@example.org", "html": "<p> </p>"})
    assert cleared.json() == {"address": "Alias@example.org", "html": "", "text": ""}
    assert user_client.get(_url(work)).json()[1]["html"] == ""


def test_only_the_accounts_identities_of_the_users_own_accounts(
    api_client: TestClient, db_session: Session, user: User, admin: User
) -> None:
    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, admin))
    others = added(api_client, new_account("admin"))
    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, user))
    mine = added(api_client, new_account("work"))

    assert api_client.get(_url(others)).status_code == 404
    elsewhere = api_client.put(_url(others), json={"address": "admin@example.org", "html": "x"})
    assert elsewhere.status_code == 404
    stranger = api_client.put(_url(mine), json={"address": "admin@example.org", "html": "x"})
    assert stranger.status_code == 404
    assert stranger.json()["detail"]["code"] == "identity_not_found"
    assert _rows(db_session, user.id) == []

    too_long = "x" * (signatures.MAX_SIGNATURE_CHARS + 1)
    over = api_client.put(_url(mine), json={"address": "work@example.org", "html": too_long})
    assert over.status_code == 422


def test_forgotten_with_the_account_and_the_user(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = _two_identities(user_client)
    home = added(user_client, new_account("home"))
    for account, address in ((work, "me@example.org"), (home, "home@example.org")):
        answer = user_client.put(_url(account), json={"address": address, "html": "<p>Ada</p>"})
        assert answer.status_code == 200
    assert {row.account_id for row in _rows(db_session, user.id)} == {work, home}

    assert user_client.delete(f"/api/v1/accounts/{work}").status_code == 204
    assert [row.account_id for row in _rows(db_session, user.id)] == [home]

    forget_user_accounts(db_session, user.id)
    db_session.commit()
    assert _rows(db_session, user.id) == []


def test_a_signature_goes_below_the_text_in_a_marked_block() -> None:
    signature = signatures.Signature("<p>Ada</p>", "Ada")
    assert signatures.signed_text("Hi", signature) == "Hi\n\n-- \nAda"
    assert signatures.signed_text("", signature) == "-- \nAda"
    assert signatures.signed_text("Hi", None) == "Hi"
    assert signatures.signed_html("<p>Hi</p>", signature) == (
        "<p>Hi</p><div data-mailroom-signature><p>Ada</p></div>"
    )
    assert signatures.signed_html(None, signature) is None
