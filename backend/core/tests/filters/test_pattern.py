"""Tests for timed matching of user patterns."""

import time
from contextlib import contextmanager
from datetime import UTC
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from mailroom_core.filters.actions.flags import MarkAsRead
from mailroom_core.filters.actions.move import MoveTo
from mailroom_core.filters.criteria.address import (
    BccMatches,
    CcMatches,
    FromMatches,
    FromMatchesName,
    ToMatches,
)
from mailroom_core.filters.criteria.body import BodyMatches
from mailroom_core.filters.criteria.subject import SubjectMatches
from mailroom_core.filters.filter import Filter
from mailroom_core.filters.message import Message
from mailroom_core.filters.path import Path
from mailroom_core.filters.pattern import (
    DEFAULT_MATCH_TIMEOUT,
    MatchTimeoutError,
    get_match_timeout,
    set_match_timeout,
)
from mailroom_core.filters.runner import RunRulesError, run_rules
from mailroom_core.filters.spec import SpecError, load_filter
from mailroom_core.mail import FetchedMessage, ParsedMessage
from mailroom_core.render import Address

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from mailroom_core.filters.account import Account
    from mailroom_core.filters.criteria.criterion import Criterion

# Exponential backtracking: every split of the a's into "a" and "aa" is tried before failing.
CATASTROPHIC = r"(a|aa)+$"
VICTIM = "a" * 60 + "b"


@pytest.fixture(autouse=True)
def _restore_timeout() -> Iterator[None]:
    yield
    set_match_timeout(DEFAULT_MATCH_TIMEOUT)


def _msg(**fields: Any) -> Message:
    """A message whose parsed fields are `fields`: addresses as (name, address) pairs, a naive
    date taken as UTC."""

    def people(key: str) -> tuple[Address, ...]:
        return tuple(
            Address(name or None, address or None) for name, address in fields.get(key, [])
        )

    sent = fields.get("date")
    parsed = ParsedMessage(
        sender=people("from_"),
        to=people("to"),
        cc=people("cc"),
        bcc=people("bcc"),
        subject=fields.get("subject", ""),
        date=sent.replace(tzinfo=UTC) if sent is not None else None,
        text=fields.get("text", ""),
    )
    return Message(1, parsed, [])


class TestMatchTimeout:
    def test_default(self) -> None:
        assert get_match_timeout() == DEFAULT_MATCH_TIMEOUT == 1.0

    @pytest.mark.parametrize(
        ("criterion", "msg"),
        [
            (SubjectMatches(CATASTROPHIC), _msg(subject=VICTIM)),
            (BodyMatches(CATASTROPHIC), _msg(text=VICTIM)),
            (FromMatches(CATASTROPHIC), _msg(from_=[("", VICTIM)])),
            (FromMatchesName(CATASTROPHIC), _msg(from_=[(VICTIM, "x@example.com")])),
            (ToMatches(CATASTROPHIC), _msg(to=[("", VICTIM)])),
            (CcMatches(CATASTROPHIC), _msg(cc=[("", VICTIM)])),
            (BccMatches(CATASTROPHIC), _msg(bcc=[("", VICTIM)])),
        ],
    )
    def test_catastrophic_pattern_times_out(self, criterion: Criterion, msg: Message) -> None:
        set_match_timeout(0.05)
        start = time.monotonic()
        with pytest.raises(MatchTimeoutError, match=r"timed out after 0\.05s"):
            criterion.select({1: msg})
        assert time.monotonic() - start < 5

    def test_is_a_timeout_error(self) -> None:
        assert issubclass(MatchTimeoutError, TimeoutError)

    def test_none_disables_and_non_positive_is_rejected(self) -> None:
        set_match_timeout(None)
        assert get_match_timeout() is None
        assert SubjectMatches(r"\[.*\] .*").select({1: _msg(subject="[x] y")})
        with pytest.raises(ValueError, match="positive"):
            set_match_timeout(0)

    def test_timeout_fails_the_filter_and_keeps_the_partial_report(self, account: Account) -> None:
        set_match_timeout(0.05)
        folder = account / "INBOX"
        report = folder.new_report(dry_run=True)
        first = Filter(SubjectMatches("nothing"), MarkAsRead())
        slow = Filter(SubjectMatches(CATASTROPHIC), MoveTo(Path("D")))

        with _one_message(), pytest.raises(MatchTimeoutError):
            folder.run_into(report, [first, slow], dry_run=True)

        assert [r["spec"] for r in report["filters"]] == [first.to_spec()]

    def test_parallel_runner_reports_the_timeout(self, account: Account) -> None:
        set_match_timeout(0.05)
        slow = Filter(SubjectMatches(CATASTROPHIC), MoveTo(Path("D")))

        with _one_message(), pytest.raises(RunRulesError) as exc_info:
            run_rules([(account / "INBOX", [slow])], dry_run=True, parallel=True)

        error = exc_info.value.reports[0]["error"]
        assert error is not None
        assert "timed out" in error


@contextmanager
def _one_message() -> Iterator[None]:
    """Every search finds message 1, whose subject is VICTIM."""
    source = f"From: a@b.example\r\nSubject: {VICTIM}\r\n\r\n".encode()
    with (
        patch("mailroom_core.filters.account.search_uids", return_value=[1]),
        patch(
            "mailroom_core.filters.account.fetch_sources",
            return_value={1: FetchedMessage(1, source, frozenset())},
        ),
        patch("mailroom_core.filters.account.fetch_envelopes", return_value={}),
    ):
        yield


class TestPatterns:
    @pytest.mark.parametrize(
        ("criterion", "msg", "expected"),
        [
            (SubjectMatches(r"\[Preview\] .*"), _msg(subject="[Preview] hi"), True),
            (SubjectMatches(r"Preview"), _msg(subject="[Preview] hi"), False),
            (BodyMatches(r"line1.line2"), _msg(text="x line1\nline2 y"), True),
            (BodyMatches(r"^line2"), _msg(text="line1\nline2"), False),
            (FromMatches(r".*@example\.com"), _msg(from_=[("Ann", "ann@example.com")]), True),
            (FromMatches(r"example"), _msg(from_=[("Ann", "ann@example.com")]), False),
            (FromMatchesName(r"A.n"), _msg(from_=[("Ann", "ann@example.com")]), True),
            (ToMatches(r"b.*"), _msg(to=[("", "a@x"), ("", "bob@x")]), True),
            (CcMatches(r"c@x"), _msg(cc=[("", "c@x")]), True),
            (BccMatches(r"d@x"), _msg(bcc=[("", "e@x")]), False),
        ],
    )
    def test_matching_unchanged(self, criterion: Criterion, msg: Message, expected: bool) -> None:
        assert bool(criterion.select({1: msg})) is expected

    @pytest.mark.parametrize(
        "cls", [SubjectMatches, BodyMatches, FromMatches, FromMatchesName, ToMatches]
    )
    def test_invalid_pattern_fails_at_construction(self, cls: Callable[[str], Criterion]) -> None:
        with pytest.raises(ValueError, match="invalid regular expression"):
            cls("(")

    def test_invalid_pattern_is_a_spec_error(self) -> None:
        spec = {
            "criterion": {"type": "subject_matches", "args": {"pattern": "("}},
            "action": {"type": "mark_as_read"},
        }
        with pytest.raises(SpecError, match="invalid regular expression"):
            load_filter(spec)
