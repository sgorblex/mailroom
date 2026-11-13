"""Web SSO through the API, against the fake provider on ``httpx.MockTransport``."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, parse_qsl, urlsplit

import httpx
import pytest
from sqlalchemy import func, select, update

from mailroom.auth.oidc import OidcProvider
from mailroom.auth.sessions import SESSION_COOKIE_NAME
from mailroom.auth.sso import (
    BINDING_COOKIE_NAME,
    CALLBACK_PATH,
    MAX_PENDING_PER_CLIENT,
    STARTS_PER_MINUTE,
    Sso,
    SsoSettings,
    safe_return_to,
    with_query,
)
from mailroom.dev.fake_idp import FakeIdp
from mailroom.models import ExternalIdentity, SsoLoginState, User
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.auth.sso import LinkClaim

ISSUER = "https://idp.example.test"
CLIENT = "mailroom-test"
START = "/api/v1/auth/oidc/start"


@pytest.fixture(scope="module")
def _keys() -> FakeIdp:
    return FakeIdp(issuer=ISSUER, client_id=CLIENT)


@pytest.fixture
def idp(_keys: FakeIdp) -> FakeIdp:
    return FakeIdp(issuer=ISSUER, client_id=CLIENT, keys=list(_keys.keys), subject="sub-alice")


def configure(
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    idp: FakeIdp,
    *,
    link_claim: LinkClaim | None = None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    settings = SsoSettings(
        issuer=ISSUER,
        client_id=CLIENT,
        client_secret=idp.client_secret,
        name="Example ID",
        link_claim=link_claim,
        redirect_uri=f"https://testserver{CALLBACK_PATH}",
    )
    provider = OidcProvider(ISSUER, transport=transport or httpx.MockTransport(idp.handle))
    monkeypatch.setattr(application.state, "sso", Sso(settings, provider), raising=False)


@pytest.fixture
def sso(application: FastAPI, monkeypatch: pytest.MonkeyPatch, idp: FakeIdp) -> FakeIdp:
    configure(application, monkeypatch, idp)
    return idp


def start(client: TestClient, **params: str) -> httpx.Response:
    response = client.get(START, params=params, follow_redirects=False)
    assert response.status_code == 303
    return response


def at_provider(idp: FakeIdp, started: httpx.Response) -> str:
    """The callback path the provider sends the browser back to."""
    location = started.headers["location"]
    assert location.startswith(f"{ISSUER}/authorize?")
    status, back = idp.authorize(dict(parse_qsl(urlsplit(location).query)))
    assert status == 302
    assert back.startswith(f"https://testserver{CALLBACK_PATH}?")
    return back.removeprefix("https://testserver")


def sign_in(client: TestClient, idp: FakeIdp, **params: str) -> httpx.Response:
    return client.get(at_provider(idp, start(client, **params)), follow_redirects=False)


_REFRESH = re.compile(r'<meta http-equiv="refresh" content="0;url=([^"]*)">')


def went_to(response: httpx.Response) -> str:
    """Where the browser goes next: a redirect's location, or the page a sign-in answers with,
    which goes on by itself from this site."""
    if response.status_code == 303:
        return response.headers["location"]
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["content-security-policy"] == "default-src 'none'"
    found = _REFRESH.search(response.text)
    assert found is not None, response.text
    return html.unescape(found.group(1))


def sso_error(response: httpx.Response) -> str | None:
    values = parse_qs(urlsplit(went_to(response)).query).get("sso_error")
    return values[0] if values else None


def signed_in_as(client: TestClient) -> str | None:
    user = client.get("/api/v1/auth/me").json()["user"]
    return user["username"] if user else None


def link(db: Session, user: User, subject: str = "sub-alice") -> ExternalIdentity:
    identity = ExternalIdentity(user_id=user.id, issuer=ISSUER, subject=subject)
    db.add(identity)
    db.commit()
    return identity


def user_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def test_config_says_whether_sso_is_on(
    api_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch, idp: FakeIdp
) -> None:
    monkeypatch.setattr(application.state, "sso", None, raising=False)
    assert api_client.get("/api/v1/auth/oidc/config").json() == {"enabled": False, "name": None}
    assert sso_error(api_client.get(START, follow_redirects=False)) == "not_configured"
    configure(application, monkeypatch, idp)
    assert api_client.get("/api/v1/auth/oidc/config").json() == {
        "enabled": True,
        "name": "Example ID",
    }


def test_start_sends_the_browser_to_the_provider_with_pkce_and_a_binding_cookie(
    api_client: TestClient, sso: FakeIdp, db_session: Session
) -> None:
    response = start(api_client, return_to="/filters")
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["client_id"] == [CLIENT]
    assert params["redirect_uri"] == [f"https://testserver{CALLBACK_PATH}"]
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["openid profile email"]
    for name in ("state", "nonce", "code_challenge"):
        assert params[name][0]
    cookie = response.headers["set-cookie"]
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/api/v1/auth/oidc", "Max-Age=600"):
        assert flag in cookie
    row = db_session.scalars(select(SsoLoginState)).one()
    assert row.return_to == "/filters"
    assert row.intent == "login"
    assert params["state"][0] not in row.state_hash  # only its hash is kept


def test_one_client_starts_only_so_many_flows_a_minute(
    api_client: TestClient, sso: FakeIdp, db_session: Session
) -> None:
    for _ in range(STARTS_PER_MINUTE):
        assert sso_error(start(api_client)) is None
    assert sso_error(start(api_client, return_to="/filters")) == "busy"
    pending = db_session.scalar(select(func.count()).select_from(SsoLoginState))
    assert pending == STARTS_PER_MINUTE


def pending_flows(db: Session, client: str, count: int) -> None:
    expires = datetime.now(UTC) + timedelta(minutes=5)
    db.add_all(
        SsoLoginState(
            state_hash=hashlib.sha256(f"{client}-{n}".encode()).hexdigest(),
            nonce="n",
            code_verifier="v",
            intent="login",
            return_to="/",
            browser_binding_hash="b" * 64,
            client_key=client,
            expires_at=expires,
        )
        for n in range(count)
    )
    db.commit()


def test_one_client_cannot_fill_the_pending_flows(
    api_client: TestClient, sso: FakeIdp, db_session: Session
) -> None:
    """Flows another client left pending do not count against this one (``testclient``)."""
    pending_flows(db_session, "198.51.100.7", MAX_PENDING_PER_CLIENT)
    assert sso_error(start(api_client)) is None
    pending_flows(db_session, "testclient", MAX_PENDING_PER_CLIENT)
    assert sso_error(start(api_client)) == "busy"


def test_a_linked_identity_signs_in(
    api_client: TestClient, sso: FakeIdp, db_session: Session, user: User
) -> None:
    identity = link(db_session, user)
    response = sign_in(api_client, sso, return_to="/filters?tab=runs")
    assert response.status_code == 200
    assert went_to(response) == "/filters?tab=runs"
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith(f"{SESSION_COOKIE_NAME}="))
    assert "SameSite=strict" in session_cookie
    assert any(c.startswith(f'{BINDING_COOKIE_NAME}=""') for c in cookies)  # deleted
    assert signed_in_as(api_client) == USERNAME
    db_session.refresh(identity)
    assert identity.last_used_at is not None
    assert db_session.scalar(select(func.count()).select_from(SsoLoginState)) == 0


@pytest.mark.parametrize("return_to", ['/p?a=1&b="><script>x</script>', "/p;x?y='1'&z=2"])
def test_the_page_after_a_sign_in_escapes_return_to(
    api_client: TestClient, sso: FakeIdp, db_session: Session, user: User, return_to: str
) -> None:
    link(db_session, user)
    response = sign_in(api_client, sso, return_to=return_to)
    assert "<script>" not in response.text
    assert html.escape(return_to, quote=True) in response.text
    assert went_to(response) == return_to


@pytest.mark.usefixtures("user")
def test_an_unlinked_identity_does_not_sign_in_or_sign_up(
    api_client: TestClient, sso: FakeIdp, db_session: Session
) -> None:
    before = user_count(db_session)
    response = sign_in(api_client, sso, return_to="/accounts")
    assert sso_error(response) == "not_linked"
    assert urlsplit(response.headers["location"]).path == "/accounts"
    assert signed_in_as(api_client) is None
    assert user_count(db_session) == before
    assert db_session.scalar(select(func.count()).select_from(ExternalIdentity)) == 0


def test_the_username_claim_links_on_first_sign_in(
    api_client: TestClient,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    idp: FakeIdp,
    db_session: Session,
    user: User,
) -> None:
    configure(application, monkeypatch, idp, link_claim="preferred_username")
    idp.username = "nobody"
    assert sso_error(sign_in(api_client, idp)) == "not_linked"
    idp.username = USERNAME
    assert sso_error(sign_in(api_client, idp)) is None
    assert signed_in_as(api_client) == USERNAME
    identity = db_session.scalars(select(ExternalIdentity)).one()
    assert (identity.user_id, identity.issuer, identity.subject) == (user.id, ISSUER, "sub-alice")


@pytest.mark.usefixtures("user")
@pytest.mark.parametrize(
    "claimed", [USERNAME.upper(), USERNAME.capitalize(), f" {USERNAME}", f"{USERNAME} "]
)
def test_the_username_claim_must_equal_the_username_exactly(
    api_client: TestClient,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    idp: FakeIdp,
    db_session: Session,
    claimed: str,
) -> None:
    """No case folding nor trimming: ``Alice`` is somebody else at the provider."""
    configure(application, monkeypatch, idp, link_claim="preferred_username")
    idp.username = claimed
    assert sso_error(sign_in(api_client, idp)) == "not_linked"
    assert db_session.scalars(select(ExternalIdentity)).all() == []


def test_the_username_claim_never_links_a_second_sign_in_from_the_issuer(
    api_client: TestClient,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    idp: FakeIdp,
    db_session: Session,
    user: User,
) -> None:
    """The user already signs in with another account at this provider: a new one naming their
    username is not taken as theirs."""
    configure(application, monkeypatch, idp, link_claim="preferred_username")
    link(db_session, user, subject="sub-alice-first")
    idp.username = USERNAME
    assert sso_error(sign_in(api_client, idp)) == "not_linked"
    assert db_session.scalars(select(ExternalIdentity.subject)).all() == ["sub-alice-first"]


def test_a_link_claim_never_moves_an_existing_link(
    api_client: TestClient,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    idp: FakeIdp,
    db_session: Session,
    user: User,
    admin: User,
) -> None:
    configure(application, monkeypatch, idp, link_claim="preferred_username")
    link(db_session, admin)
    idp.username = USERNAME
    assert sso_error(sign_in(api_client, idp)) is None
    assert signed_in_as(api_client) == admin.username
    assert db_session.scalars(select(ExternalIdentity.user_id)).all() == [admin.id]
    _ = user


@pytest.mark.usefixtures("user")
def test_a_callback_started_in_another_browser_is_refused(
    api_client: TestClient, sso: FakeIdp, db_session: Session, user: User
) -> None:
    """Login CSRF: an attacker starts a flow, signs in at the provider as themselves, and sends
    the victim's browser the callback. The victim has no binding cookie for that state."""
    link(db_session, user)
    attackers = start(api_client)
    callback = at_provider(sso, attackers)
    api_client.cookies.clear()  # the victim's browser
    response = api_client.get(callback, follow_redirects=False)
    assert sso_error(response) == "browser_mismatch"
    assert signed_in_as(api_client) is None
    assert db_session.scalar(select(func.count()).select_from(SsoLoginState)) == 0  # spent


@pytest.mark.usefixtures("user")
def test_a_wrong_binding_cookie_is_refused(
    api_client: TestClient, sso: FakeIdp, db_session: Session, user: User
) -> None:
    link(db_session, user)
    callback = at_provider(sso, start(api_client))
    api_client.cookies.set(BINDING_COOKIE_NAME, "forged", path="/api/v1/auth/oidc")
    assert sso_error(api_client.get(callback, follow_redirects=False)) == "browser_mismatch"


def test_an_unknown_or_replayed_state_is_refused(
    api_client: TestClient, sso: FakeIdp, db_session: Session, user: User
) -> None:
    link(db_session, user)
    assert (
        sso_error(api_client.get(f"{CALLBACK_PATH}?state=made-up&code=x", follow_redirects=False))
        == "state_invalid"
    )
    assert sso_error(api_client.get(CALLBACK_PATH, follow_redirects=False)) == "state_invalid"
    callback = at_provider(sso, start(api_client))
    assert sso_error(api_client.get(callback, follow_redirects=False)) is None
    api_client.cookies.delete(SESSION_COOKIE_NAME)
    assert sso_error(api_client.get(callback, follow_redirects=False)) == "state_invalid"
    assert signed_in_as(api_client) is None


def test_an_expired_state_is_refused(
    api_client: TestClient, sso: FakeIdp, db_session: Session, user: User
) -> None:
    link(db_session, user)
    callback = at_provider(sso, start(api_client, return_to="/filters"))
    db_session.execute(
        update(SsoLoginState).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    response = api_client.get(callback, follow_redirects=False)
    assert sso_error(response) == "state_invalid"
    assert urlsplit(response.headers["location"]).path == "/filters"


@pytest.mark.parametrize(
    ("claims", "algorithm"),
    [
        ({"nonce": "another"}, None),
        ({"nonce": None}, None),
        ({"aud": "other"}, None),
        ({}, "HS256"),
    ],
    ids=["nonce mismatch", "no nonce", "audience", "HS256"],
)
def test_an_id_token_that_does_not_hold_is_refused(
    api_client: TestClient,
    sso: FakeIdp,
    db_session: Session,
    user: User,
    claims: dict[str, object],
    algorithm: str | None,
) -> None:
    link(db_session, user)
    sso.id_token_claims = claims
    sso.id_token_algorithm = algorithm
    assert sso_error(sign_in(api_client, sso)) == "failed"
    assert signed_in_as(api_client) is None


@pytest.mark.usefixtures("user")
def test_a_denial_at_the_provider_comes_back_as_such(api_client: TestClient, sso: FakeIdp) -> None:
    sso.deny = True
    assert sso_error(sign_in(api_client, sso)) == "denied"


def test_an_unreachable_provider_is_unavailable(
    api_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch, idp: FakeIdp
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    configure(application, monkeypatch, idp, transport=httpx.MockTransport(down))
    response = api_client.get(START, params={"return_to": "/filters"}, follow_redirects=False)
    assert response.headers["location"] == "/filters?sso_error=unavailable"


@pytest.mark.parametrize(
    "return_to",
    [
        "//evil.example",
        "/\\evil.example",
        "https://evil.example/",
        "evil.example",
        "javascript:alert(1)",
        "/\t/evil.example",
        "/" + "a" * 600,
    ],
)
def test_return_to_never_leaves_the_site(
    api_client: TestClient, sso: FakeIdp, db_session: Session, user: User, return_to: str
) -> None:
    link(db_session, user)
    response = api_client.get(START, params={"return_to": return_to}, follow_redirects=False)
    if response.status_code == 422:  # over the length bound
        return
    callback = at_provider(sso, response)
    assert went_to(api_client.get(callback, follow_redirects=False)) == "/"


@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("/filters/3?tab=runs#x", "/filters/3?tab=runs#x"),
        ("/", "/"),
        (None, "/"),
        ("", "/"),
        ("//evil.example/x", "/"),
        ("/\\evil.example", "/"),
        ("https://evil.example", "/"),
        ("/a\nb", "/"),
    ],
)
def test_safe_return_to(raw: str | None, safe: str) -> None:
    assert safe_return_to(raw) == safe


def test_with_query_keeps_the_path_and_replaces_the_parameter() -> None:
    assert with_query("/p?a=1&sso_error=x#f", sso_error="y") == "/p?a=1&sso_error=y#f"


def test_linking_needs_a_session(api_client: TestClient, sso: FakeIdp) -> None:
    response = start(api_client, intent="link")
    assert response.headers["location"] == "/profile?sso_error=signed_out"


def test_linking_from_another_site_is_refused(user_client: TestClient, sso: FakeIdp) -> None:
    response = user_client.get(
        START,
        params={"intent": "link"},
        headers={"Sec-Fetch-Site": "cross-site"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/profile?sso_error=signed_out"


def test_a_signed_in_user_links_a_sign_in_then_uses_it(
    user_client: TestClient, sso: FakeIdp, db_session: Session, user: User
) -> None:
    response = sign_in(user_client, sso, intent="link")
    assert response.headers["location"] == "/profile"
    identities = user_client.get("/api/v1/me/identities").json()
    assert [(i["issuer"], i["subject"], i["name"]) for i in identities] == [
        (ISSUER, "sub-alice", "Example ID")
    ]
    assert identities[0]["last_used_at"] is not None

    assert user_client.post("/api/v1/auth/logout").status_code == 204
    assert sso_error(sign_in(user_client, sso)) is None
    assert signed_in_as(user_client) == USERNAME
    assert db_session.scalar(select(func.count()).select_from(ExternalIdentity)) == 1


def test_an_identity_linked_to_someone_else_is_not_taken_over(
    user_client: TestClient, sso: FakeIdp, db_session: Session, admin: User
) -> None:
    link(db_session, admin)
    assert sso_error(sign_in(user_client, sso, intent="link")) == "identity_taken"
    assert db_session.scalars(select(ExternalIdentity.user_id)).all() == [admin.id]


def test_users_list_and_unlink_only_their_own_identities(
    user_client: TestClient,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    user: User,
    admin: User,
) -> None:
    monkeypatch.setattr(application.state, "sso", None, raising=False)
    own = link(db_session, user)
    theirs = link(db_session, admin, subject="sub-root")
    listed = user_client.get("/api/v1/me/identities").json()
    assert [i["id"] for i in listed] == [own.id]
    assert listed[0]["name"] is None  # SSO is off in this test: no label
    assert user_client.delete(f"/api/v1/me/identities/{theirs.id}").status_code == 404
    assert user_client.delete(f"/api/v1/me/identities/{own.id}").status_code == 204
    assert user_client.get("/api/v1/me/identities").json() == []
    assert db_session.scalars(select(ExternalIdentity.id)).all() == [theirs.id]


def test_identities_need_a_session(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/me/identities").status_code == 401
    assert api_client.delete("/api/v1/me/identities/1").status_code == 401


def test_removing_a_user_removes_their_identities(db_session: Session, user: User) -> None:
    link(db_session, user)
    db_session.delete(user)
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(ExternalIdentity)) == 0


@pytest.mark.usefixtures("user")
def test_password_sign_in_still_works_with_sso_on(api_client: TestClient, sso: FakeIdp) -> None:
    _ = sso
    response = api_client.post(
        "/api/v1/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    assert response.status_code == 200
