"""``MAILROOM_PUBLIC_URL`` and the SSO settings, read from the environment."""

from __future__ import annotations

import pytest

from mailroom.auth.sso import sso_settings_from_env
from mailroom.config.public_url import parse_public_url, public_url_from_env

OIDC = {
    "MAILROOM_OIDC_ISSUER": "https://idp.example.test/application/o/mailroom/",
    "MAILROOM_OIDC_CLIENT_ID": "mailroom",
    "MAILROOM_OIDC_CLIENT_SECRET": "s3cret",
}


@pytest.mark.parametrize(
    ("raw", "origin"),
    [
        ("https://Mail.Example.com", "https://mail.example.com"),
        ("https://mail.example.com/", "https://mail.example.com"),
        ("https://mail.example.com:8443", "https://mail.example.com:8443"),
        ("http://127.0.0.1:5180", "http://127.0.0.1:5180"),
        ("http://localhost:5180", "http://localhost:5180"),
        ("http://[::1]:5180", "http://[::1]:5180"),
        ("https://mail.example.com:443", "https://mail.example.com"),
        ("https://xn--bcher-kva.example:443/", "https://xn--bcher-kva.example"),
        ("http://127.0.0.1:80", "http://127.0.0.1"),
        ("http://localhost:443", "http://localhost:443"),
        ("https://192.0.2.10", "https://192.0.2.10"),
        ("https://[2001:db8::1]:443", "https://[2001:db8::1]"),
    ],
)
def test_a_public_url_is_an_origin(raw: str, origin: str) -> None:
    """The scheme's default port is dropped: the origin is what a browser sends as ``Origin``."""
    public = parse_public_url(raw)
    assert public.origin == origin
    assert public.host == origin.split("://", 1)[1]
    assert public.url("/api/v1/x") == f"{origin}/api/v1/x"


@pytest.mark.parametrize(
    "raw",
    [
        "mail.example.com",
        "ftp://mail.example.com",
        "http://mail.example.com",
        "https://mail.example.com/mailroom",
        "https://mail.example.com/?a=1",
        "https://user:pw@mail.example.com",
        "https://mail.example.com:port",
        "https://",
        "https://mail_server.example.com",
        "https://mail.example.com.",
        "https://-mail.example.com",
        "https://mail..example.com",
        "https://mail example.com",
        "https://mail.example.com%2f",
        "https://bücher.example",
        "https://mail.example.com\\@evil.example",
        "https://999.0.0.1",
        "https://1.2.3",
        "https://[::1%25eth0]",
        "https://[not:an:address]",
        "https://" + "a." * 127 + "com",
    ],
)
def test_anything_but_an_origin_is_refused(raw: str) -> None:
    with pytest.raises(ValueError, match=r"."):
        parse_public_url(raw)


def test_an_invalid_public_url_counts_as_unset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MAILROOM_PUBLIC_URL", "http://mail.example.com")
    assert public_url_from_env() is None
    monkeypatch.setenv("MAILROOM_PUBLIC_URL", "https://mail_server.example.com")
    assert public_url_from_env() is None
    assert "its host is not a valid host name" in caplog.text
    monkeypatch.setenv("MAILROOM_PUBLIC_URL", "")
    assert public_url_from_env() is None


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name in (*OIDC, "MAILROOM_OIDC_NAME", "MAILROOM_OIDC_LINK_CLAIM"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_sso_needs_the_whole_client_and_a_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    public = parse_public_url("https://mail.example.com")
    _set(monkeypatch, {})
    assert sso_settings_from_env(public) is None
    _set(monkeypatch, {k: v for k, v in OIDC.items() if k != "MAILROOM_OIDC_CLIENT_SECRET"})
    assert sso_settings_from_env(public) is None
    _set(monkeypatch, OIDC)
    assert sso_settings_from_env(None) is None
    _set(monkeypatch, {**OIDC, "MAILROOM_OIDC_ISSUER": "http://idp.example.test"})
    assert sso_settings_from_env(public) is None

    _set(monkeypatch, {**OIDC, "MAILROOM_OIDC_NAME": "Authentik"})
    settings = sso_settings_from_env(public)
    assert settings is not None
    assert settings.redirect_uri == "https://mail.example.com/api/v1/auth/oidc/callback"
    assert settings.name == "Authentik"
    assert settings.link_claim is None


@pytest.mark.parametrize(
    ("raw", "claim"),
    [("preferred_username", "preferred_username"), ("email", None), ("sub", None)],
)
def test_only_known_link_claims_count(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str, claim: str | None
) -> None:
    """``email`` is refused: a username never holds an ``@``, so it could never match."""
    _set(monkeypatch, {**OIDC, "MAILROOM_OIDC_LINK_CLAIM": raw})
    settings = sso_settings_from_env(parse_public_url("https://mail.example.com"))
    assert settings is not None
    assert settings.link_claim == claim
    assert settings.name == "SSO"
    assert ("is ignored" in caplog.text) == (claim is None)
