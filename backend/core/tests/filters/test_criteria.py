"""Tests for criteria classes."""

from collections.abc import Iterator
from datetime import date, datetime
from unittest.mock import MagicMock

from imap_thingy.filters.criteria import (
    Anything,
    BccContains,
    BccIs,
    BccMatches,
    CcContains,
    CcIs,
    CcMatches,
    Criterion,
    FromContains,
    FromIs,
    FromMatches,
    FromMatchesName,
    IsAnswered,
    IsRead,
    IsStarred,
    IsUnanswered,
    IsUnread,
    IsUnstarred,
    OlderThan,
    SubjectContains,
    SubjectIs,
    SubjectMatches,
    ToContains,
    ToIs,
    ToMatches,
)


def _query_atoms(q: object) -> Iterator[object]:
    if hasattr(q, "build"):
        yield from _query_atoms(q.build())
    elif isinstance(q, list):
        for x in q:
            yield from _query_atoms(x)
    elif isinstance(q, tuple) and len(q) >= 1 and q[0] in ("OR", "NOT"):
        for x in q[1:]:
            yield from _query_atoms(x)
    else:
        yield q


class TestCriteriaCombination:
    """Test criteria boolean operations."""

    def test_criteria_and_combination(self) -> None:
        """Test combining criteria with AND operator."""
        criterion1 = FromIs("sender@example.com")
        criterion2 = SubjectContains("Important")
        combined = criterion1 & criterion2
        assert isinstance(combined, Criterion)

    def test_criteria_or_combination(self) -> None:
        """Test combining criteria with OR operator."""
        criterion1 = FromIs("sender1@example.com")
        criterion2 = FromIs("sender2@example.com")
        combined = criterion1 | criterion2
        assert isinstance(combined, Criterion)

    def test_criteria_negation(self) -> None:
        """Test negating a criterion."""
        criterion = FromIs("sender@example.com")
        negated = ~criterion
        assert isinstance(negated, Criterion)

    def test_if_then_returns_filter(self) -> None:
        """Test If(c).then(action) returns Filter."""
        from imap_thingy.accounts import Path
        from imap_thingy.filters import Filter, If, MoveTo

        c = FromIs("a@x.com")
        a = MoveTo(Path("Dest"))
        f = If(c).then(a)
        assert isinstance(f, Filter)
        assert f.criterion is c
        assert f.action is a


class TestAddressCriteria:
    """Test address-based criteria."""

    def test_from_is_creation(self) -> None:
        """Test FromIs criterion creation."""
        criterion = FromIs("test@example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("FROM", "test@example.com")

    def test_to_is_creation(self) -> None:
        """Test ToIs criterion creation."""
        criterion = ToIs("test@example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("TO", "test@example.com")

    def test_to_is_without_cc_bcc(self) -> None:
        """Test ToIs criterion without CC and BCC."""
        criterion = ToIs("test@example.com", incl_cc=False, incl_bcc=False)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("TO", "test@example.com")

    def test_to_is_with_cc_bcc(self) -> None:
        """Test ToIs criterion with CC and BCC enabled."""
        criterion = ToIs("test@example.com", incl_cc=True, incl_bcc=True)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query is not None
        atoms = list(_query_atoms(criterion.imap_query))
        assert ("TO", "test@example.com") in atoms
        assert ("CC", "test@example.com") in atoms
        assert ("BCC", "test@example.com") in atoms

    def test_cc_is_creation(self) -> None:
        """Test CcIs criterion creation."""
        criterion = CcIs("test@example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("CC", "test@example.com")

    def test_bcc_is_creation(self) -> None:
        """Test BccIs criterion creation."""
        criterion = BccIs("test@example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("BCC", "test@example.com")

    def test_from_matches_creation(self) -> None:
        """Test FromMatches criterion creation."""
        criterion = FromMatches(r".*@example\.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "ALL"

    def test_from_contains_creation(self) -> None:
        """Test FromContains criterion creation."""
        criterion = FromContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("FROM", "example.com")

    def test_to_contains_creation(self) -> None:
        """Test ToContains criterion creation."""
        criterion = ToContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("TO", "example.com")

    def test_to_contains_without_cc_bcc(self) -> None:
        """Test ToContains criterion without CC and BCC."""
        criterion = ToContains("example.com", incl_cc=False, incl_bcc=False)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("TO", "example.com")

    def test_to_contains_with_cc_bcc(self) -> None:
        """Test ToContains criterion with CC and BCC enabled."""
        criterion = ToContains("example.com", incl_cc=True, incl_bcc=True)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query is not None
        atoms = list(_query_atoms(criterion.imap_query))
        assert ("TO", "example.com") in atoms
        assert ("CC", "example.com") in atoms
        assert ("BCC", "example.com") in atoms

    def test_to_matches_creation(self) -> None:
        """Test ToMatches criterion creation."""
        criterion = ToMatches(r".*@example\.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "ALL"

    def test_to_matches_default_does_not_include_cc_bcc(self) -> None:
        """Test ToMatches default excludes CC and BCC."""
        from imap_thingy.core import Message

        parsed = MagicMock()
        parsed.to = []
        parsed.cc = [("Name", "cc@example.com")]
        parsed.bcc = [("Name", "bcc@example.com")]
        msg = Message(0, parsed, [])
        criterion = ToMatches(r".*@example\.com")
        assert criterion.func(msg) is False

    def test_cc_contains_creation(self) -> None:
        """Test CcContains criterion creation."""
        criterion = CcContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("CC", "example.com")

    def test_cc_matches_creation(self) -> None:
        """Test CcMatches criterion creation."""
        criterion = CcMatches(r".*@example\.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "ALL"

    def test_bcc_contains_creation(self) -> None:
        """Test BccContains criterion creation."""
        criterion = BccContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("BCC", "example.com")

    def test_bcc_matches_creation(self) -> None:
        """Test BccMatches criterion creation."""
        criterion = BccMatches(r".*@example\.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "ALL"

    def test_from_matches_name_creation(self) -> None:
        """Test FromMatchesName criterion creation."""
        criterion = FromMatchesName(r"John.*")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "ALL"

    def test_to_matches_with_options(self) -> None:
        """Test ToMatches criterion with incl_cc and incl_bcc options."""
        criterion = ToMatches(r".*@example\.com", incl_cc=True, incl_bcc=True)
        assert isinstance(criterion, Criterion)

    def test_to_matches_without_cc_bcc(self) -> None:
        """Test ToMatches criterion without CC and BCC."""
        criterion = ToMatches(r".*@example\.com", incl_cc=False, incl_bcc=False)
        assert isinstance(criterion, Criterion)


class TestSubjectCriteria:
    """Test subject-based criteria."""

    def test_subject_contains_creation(self) -> None:
        """Test SubjectContains criterion creation."""
        criterion = SubjectContains("Important")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("SUBJECT", "Important")

    def test_subject_is_creation(self) -> None:
        """Test SubjectIs criterion creation."""
        criterion = SubjectIs("Exact Subject")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("SUBJECT", "Exact Subject")

    def test_subject_matches_creation(self) -> None:
        """Test SubjectMatches criterion creation."""
        criterion = SubjectMatches(r"^\[.*\]")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "ALL"


class TestDateCriteria:
    """Test date-based criteria."""

    def test_older_than_with_date(self) -> None:
        """Test OlderThan with date object."""
        cutoff = date(2025, 1, 1)
        criterion = OlderThan(cutoff)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("SENTBEFORE", "01-Jan-2025")

    def test_older_than_with_datetime(self) -> None:
        """Test OlderThan with datetime object."""
        cutoff = datetime(2025, 1, 1, 12, 0, 0)
        criterion = OlderThan(cutoff)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("SENTBEFORE", "01-Jan-2025")

    def test_older_than_with_string(self) -> None:
        """Test OlderThan with string date."""
        criterion = OlderThan("01-Jan-2025")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == ("SENTBEFORE", "01-Jan-2025")


class TestFlagCriteria:
    """Test flag-based criteria."""

    def test_is_read_creation(self) -> None:
        """Test IsRead criterion creation."""
        criterion = IsRead()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "SEEN"

    def test_is_unread_creation(self) -> None:
        """Test IsUnread criterion creation."""
        criterion = IsUnread()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "UNSEEN"

    def test_is_starred_creation(self) -> None:
        """Test IsStarred criterion creation."""
        criterion = IsStarred()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "FLAGGED"

    def test_is_unstarred_creation(self) -> None:
        """Test IsUnstarred criterion creation."""
        criterion = IsUnstarred()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "UNFLAGGED"

    def test_is_answered_creation(self) -> None:
        """Test IsAnswered criterion creation."""
        criterion = IsAnswered()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "ANSWERED"

    def test_is_unanswered_creation(self) -> None:
        """Test IsUnanswered criterion creation."""
        criterion = IsUnanswered()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "UNANSWERED"


class TestSpecialCriteria:
    """Test special criteria."""

    def test_anything_creation(self) -> None:
        """Test Anything criterion creation."""
        criterion = Anything()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.build() == "ALL"


class TestCriteriaFiltering:
    """Test actual filtering logic with mocked messages."""

    def test_anything_select_returns_all_messages(self) -> None:
        """Test that Anything().select(messages) returns all messages."""
        from mailparser.core import MailParser

        from imap_thingy.core import Message

        criterion = Anything()
        p1 = MagicMock(spec=MailParser)
        p2 = MagicMock(spec=MailParser)
        messages = {1: Message(1, p1, []), 2: Message(2, p2, [])}
        result = criterion.select(messages)
        assert len(result) == 2
        assert result[1].parsed is p1 and result[2].parsed is p2
        assert criterion.imap_query.build() == "ALL"

    def test_criteria_and_combination_imap_query(self) -> None:
        """Test that AND combination merges IMAP queries."""
        criterion1 = FromIs("sender@example.com")
        criterion2 = SubjectContains("Important")
        combined = criterion1 & criterion2
        assert combined.imap_query is not None
        atoms = list(_query_atoms(combined.imap_query))
        assert ("FROM", "sender@example.com") in atoms
        assert ("SUBJECT", "Important") in atoms

    def test_criteria_or_combination_imap_query(self) -> None:
        """Test that OR combination creates OR query."""
        criterion1 = FromIs("sender1@example.com")
        criterion2 = FromIs("sender2@example.com")
        combined = criterion1 | criterion2
        assert combined.imap_query is not None
        b = combined.imap_query.build()
        first_key = b[0] if isinstance(b, list) else b
        assert first_key[0] == "OR"

    def test_criteria_negation_imap_query(self) -> None:
        """Test that negation creates NOT query."""
        criterion = FromIs("sender@example.com")
        negated = ~criterion
        assert negated.imap_query is not None
        b = negated.imap_query.build()
        first_key = b[0] if isinstance(b, list) else b
        assert first_key[0] == "NOT"
