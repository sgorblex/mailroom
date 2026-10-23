"""Tests for timed matching of user patterns."""

import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest

from imap_thingy.core import Message, Path
from imap_thingy.filters import (
    DEFAULT_MATCH_TIMEOUT,
    BccMatches,
    BodyMatches,
    CcMatches,
    Criterion,
    Filter,
    FromMatches,
    FromMatchesName,
    MarkAsRead,
    MatchTimeoutError,
    MoveTo,
    SubjectMatches,
    ToMatches,
    get_match_timeout,
    set_match_timeout,
)
from imap_thingy.runner import RunRulesError, run_rules
from imap_thingy.spec import SpecError, load_filter
from imap_thingy.utils import matches

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.conftest import MockEMailAccount

# Exponential backtracking: every split of the a's into "a" and "aa" is tried before failing.
CATASTROPHIC = r"(a|aa)+$"
VICTIM = "a" * 60 + "b"


@pytest.fixture(autouse=True)
def _restore_timeout() -> Iterator[None]:
    yield
    set_match_timeout(DEFAULT_MATCH_TIMEOUT)


def _msg(**fields: Any) -> Message:  # noqa: ANN401
    parsed = {
        "from_": [],
        "to": [],
        "cc": [],
        "bcc": [],
        "subject": "",
        "text_plain": [],
        "text_html": [],
        **fields,
    }
    return Message(1, cast("Any", SimpleNamespace(**parsed)), [])


class TestMatchTimeout:
    def test_default(self) -> None:
        assert get_match_timeout() == DEFAULT_MATCH_TIMEOUT == 1.0

    @pytest.mark.parametrize(
        ("criterion", "msg"),
        [
            (SubjectMatches(CATASTROPHIC), _msg(subject=VICTIM)),
            (BodyMatches(CATASTROPHIC), _msg(text_plain=[VICTIM])),
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

    def test_utils_matches_is_timed(self) -> None:
        set_match_timeout(0.05)
        with pytest.raises(MatchTimeoutError):
            matches(CATASTROPHIC, VICTIM)

    def test_none_disables_and_non_positive_is_rejected(self) -> None:
        set_match_timeout(None)
        assert get_match_timeout() is None
        assert SubjectMatches(r"\[.*\] .*").select({1: _msg(subject="[x] y")})
        with pytest.raises(ValueError, match="positive"):
            set_match_timeout(0)

    def test_timeout_fails_the_filter_and_keeps_the_partial_report(
        self, mock_account: MockEMailAccount
    ) -> None:
        set_match_timeout(0.05)
        conn: MagicMock = mock_account.connect.return_value
        conn._raw_command_untagged = MagicMock(return_value=[b"1"])
        conn.fetch = MagicMock(return_value={})
        folder = mock_account / "INBOX"
        report = folder.new_report(dry_run=True)
        first = Filter(SubjectMatches("nothing"), MarkAsRead())
        slow = Filter(SubjectMatches(CATASTROPHIC), MoveTo(Path("D")))

        with (
            patch(
                "imap_thingy.accounts.account.fetch_mail",
                return_value={1: _msg(subject=VICTIM)},
            ),
            pytest.raises(MatchTimeoutError),
        ):
            folder.run_into(report, [first, slow], dry_run=True)

        assert [r["spec"] for r in report["filters"]] == [first.to_spec()]
        conn.logout.assert_called_once()

    def test_parallel_runner_reports_the_timeout(self, mock_account: MockEMailAccount) -> None:
        set_match_timeout(0.05)
        conn: MagicMock = mock_account.connect.return_value
        conn._raw_command_untagged = MagicMock(return_value=[b"1"])
        slow = Filter(SubjectMatches(CATASTROPHIC), MoveTo(Path("D")))

        with (
            patch(
                "imap_thingy.accounts.account.fetch_mail",
                return_value={1: _msg(subject=VICTIM)},
            ),
            pytest.raises(RunRulesError) as exc_info,
        ):
            run_rules([(mock_account / "INBOX", [slow])], dry_run=True, parallel=True)

        error = exc_info.value.reports[0]["error"]
        assert error is not None
        assert "timed out" in error


class TestPatterns:
    @pytest.mark.parametrize(
        ("criterion", "msg", "expected"),
        [
            (SubjectMatches(r"\[Preview\] .*"), _msg(subject="[Preview] hi"), True),
            (SubjectMatches(r"Preview"), _msg(subject="[Preview] hi"), False),
            (BodyMatches(r"line1.line2"), _msg(text_plain=["x line1\nline2 y"]), True),
            (BodyMatches(r"^line2"), _msg(text_plain=["line1\nline2"]), False),
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
