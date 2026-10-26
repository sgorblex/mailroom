from __future__ import annotations

from datetime import timedelta

import pytest
from starlette.requests import Request

from mailroom.auth.password import hash_password, password_matches
from mailroom.auth.sessions import SESSION_TTL_DAYS_ENV, session_ttl
from mailroom.auth.throttle import MAX_WAIT_SECONDS, LoginThrottle, RateLimit, client_key
from mailroom.auth.users import UserError, normalized_username


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_throttle_doubles_the_wait_up_to_the_cap() -> None:
    clock = FakeClock()
    throttle = LoginThrottle(clock)
    waits = []
    for _ in range(12):
        assert throttle.begin_attempt("a") == 0
        waits.append(throttle.begin_attempt("a"))
        clock.now += waits[-1]
    assert waits == [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, MAX_WAIT_SECONDS, MAX_WAIT_SECONDS]
    assert throttle.begin_attempt("b") == 0


def test_a_rate_limit_counts_each_client_in_a_sliding_window() -> None:
    clock = FakeClock()
    limit = RateLimit(3, 60, clock)
    for _ in range(3):
        assert limit.allow("a")
        clock.now += 10
    assert not limit.allow("a")
    assert limit.allow("b")
    clock.now += 31  # the first hit is a minute old
    assert limit.allow("a")
    assert not limit.allow("a")


def test_rate_limit_sweeps_a_client_it_never_let_through() -> None:
    clock = FakeClock()
    limit = RateLimit(0, 60, clock)
    assert not limit.allow("a")
    clock.now += 60
    assert not limit.allow("b")


def test_throttle_forgets_a_client_on_success() -> None:
    throttle = LoginThrottle(FakeClock())
    assert throttle.begin_attempt("a") == 0
    throttle.record_success("a")
    assert throttle.begin_attempt("a") == 0


def test_throttle_forgets_a_quiet_client() -> None:
    clock = FakeClock()
    throttle = LoginThrottle(clock)
    for _ in range(12):
        throttle.begin_attempt("a")
        clock.now += MAX_WAIT_SECONDS
    clock.now += 24 * 60 * 60
    throttle.begin_attempt("b")
    assert throttle.begin_attempt("a") == 0
    assert throttle.begin_attempt("a") == 1


def _request(peer: str) -> Request:
    return Request({"type": "http", "client": (peer, 50000), "headers": []})


def test_client_key_groups_ipv6_by_56() -> None:
    assert client_key(_request("2001:db8:1:2ff::1")) == "2001:db8:1:200::/56"
    assert client_key(_request("192.0.2.7")) == "192.0.2.7"


def test_password_matches() -> None:
    stored = hash_password("secret")
    assert password_matches(stored, "secret")
    assert not password_matches(stored, "Secret")
    assert not password_matches(None, "secret")
    assert not password_matches("not a hash", "secret")


@pytest.mark.parametrize(
    ("raw", "expected"), [("Alice", "alice"), (" bob.smith ", "bob.smith"), ("a_1-b", "a_1-b")]
)
def test_usernames_are_stored_lowercase(raw: str, expected: str) -> None:
    assert normalized_username(raw) == expected


@pytest.mark.parametrize("raw", ["", " ", ".alice", "al ice", "alice@example.org", "a" * 65])
def test_malformed_usernames_are_refused(raw: str) -> None:
    with pytest.raises(UserError):
        normalized_username(raw)


@pytest.mark.parametrize(
    ("raw", "days"), [("", 14), ("30", 30), ("0", 1), ("9999", 365), ("x", 14)]
)
def test_session_ttl(monkeypatch: pytest.MonkeyPatch, raw: str, days: int) -> None:
    monkeypatch.setenv(SESSION_TTL_DAYS_ENV, raw)
    assert session_ttl() == timedelta(days=days)
