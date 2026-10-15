from __future__ import annotations

import pytest

from tests.support import worker_db


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("postgresql://mailroom:mailroom@127.0.0.1:5434/mailroom_test", True),
        ("postgresql://mailroom:mailroom@localhost:5434/mailroom_test", True),
        ("postgresql://mailroom:mailroom@127.0.0.1:5434/mailroom", False),
        ("postgresql://mailroom:mailroom@db.example.org:5432/mailroom_test", False),
        ("mysql://mailroom:mailroom@127.0.0.1:3306/mailroom_test", False),
    ],
)
def test_database_url_allowlist(url: str, allowed: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAILROOM_INTEGRATION_TESTS_ALLOW_ANY_DATABASE", raising=False)
    monkeypatch.delenv("TESTCONTAINERS_HOST_OVERRIDE", raising=False)
    monkeypatch.delenv("TC_HOST", raising=False)
    assert worker_db.test_database_url_allowed(url) is allowed
