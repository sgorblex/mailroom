"""Tests for criteria classes."""

from datetime import date, datetime
from unittest.mock import MagicMock

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.criteria import (
    BccContains,
    BccIs,
    BccMatches,
    CcContains,
    CcIs,
    CcMatches,
    Criterion,
    DuplicateCriterion,
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
    SelectAll,
    SubjectContains,
    SubjectIs,
    SubjectMatches,
    ToContains,
    ToIs,
    ToMatches,
)


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


class TestAddressCriteria:
    """Test address-based criteria."""

    def test_from_is_creation(self) -> None:
        """Test FromIs criterion creation."""
        criterion = FromIs("test@example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["FROM", "test@example.com"]

    def test_to_is_creation(self) -> None:
        """Test ToIs criterion creation."""
        criterion = ToIs("test@example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["TO", "test@example.com"]

    def test_cc_is_creation(self) -> None:
        """Test CcIs criterion creation."""
        criterion = CcIs("test@example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["CC", "test@example.com"]

    def test_bcc_is_creation(self) -> None:
        """Test BccIs criterion creation."""
        criterion = BccIs("test@example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["BCC", "test@example.com"]

    def test_from_matches_creation(self) -> None:
        """Test FromMatches criterion creation."""
        criterion = FromMatches(r".*@example\.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query is None

    def test_from_contains_creation(self) -> None:
        """Test FromContains criterion creation."""
        criterion = FromContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["FROM", "example.com"]

    def test_to_contains_creation(self) -> None:
        """Test ToContains criterion creation."""
        criterion = ToContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["TO", "example.com"]

    def test_to_matches_creation(self) -> None:
        """Test ToMatches criterion creation."""
        criterion = ToMatches(r".*@example\.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query is None

    def test_cc_contains_creation(self) -> None:
        """Test CcContains criterion creation."""
        criterion = CcContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["CC", "example.com"]

    def test_cc_matches_creation(self) -> None:
        """Test CcMatches criterion creation."""
        criterion = CcMatches(r".*@example\.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query is None

    def test_bcc_contains_creation(self) -> None:
        """Test BccContains criterion creation."""
        criterion = BccContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["BCC", "example.com"]

    def test_bcc_matches_creation(self) -> None:
        """Test BccMatches criterion creation."""
        criterion = BccMatches(r".*@example\.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query is None

    def test_from_matches_name_creation(self) -> None:
        """Test FromMatchesName criterion creation."""
        criterion = FromMatchesName(r"John.*")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query is None

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
        assert criterion.imap_query == ["SUBJECT", "Important"]

    def test_subject_is_creation(self) -> None:
        """Test SubjectIs criterion creation."""
        criterion = SubjectIs("Exact Subject")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["SUBJECT", "Exact Subject"]

    def test_subject_matches_creation(self) -> None:
        """Test SubjectMatches criterion creation."""
        criterion = SubjectMatches(r"^\[.*\]")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query is None


class TestDateCriteria:
    """Test date-based criteria."""

    def test_older_than_with_date(self) -> None:
        """Test OlderThan with date object."""
        cutoff = date(2025, 1, 1)
        criterion = OlderThan(cutoff)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["SENTBEFORE", "01-Jan-2025"]

    def test_older_than_with_datetime(self) -> None:
        """Test OlderThan with datetime object."""
        cutoff = datetime(2025, 1, 1, 12, 0, 0)
        criterion = OlderThan(cutoff)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["SENTBEFORE", "01-Jan-2025"]

    def test_older_than_with_string(self) -> None:
        """Test OlderThan with string date."""
        criterion = OlderThan("01-Jan-2025")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["SENTBEFORE", "01-Jan-2025"]


class TestFlagCriteria:
    """Test flag-based criteria."""

    def test_is_read_creation(self) -> None:
        """Test IsRead criterion creation."""
        criterion = IsRead()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["SEEN"]

    def test_is_unread_creation(self) -> None:
        """Test IsUnread criterion creation."""
        criterion = IsUnread()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["UNSEEN"]

    def test_is_starred_creation(self) -> None:
        """Test IsStarred criterion creation."""
        criterion = IsStarred()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["FLAGGED"]

    def test_is_unstarred_creation(self) -> None:
        """Test IsUnstarred criterion creation."""
        criterion = IsUnstarred()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["UNFLAGGED"]

    def test_is_answered_creation(self) -> None:
        """Test IsAnswered criterion creation."""
        criterion = IsAnswered()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["ANSWERED"]

    def test_is_unanswered_creation(self) -> None:
        """Test IsUnanswered criterion creation."""
        criterion = IsUnanswered()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["UNANSWERED"]


class TestDuplicateCriterion:
    """Test DuplicateCriterion."""

    def test_duplicate_criterion_creation(self) -> None:
        """Test DuplicateCriterion creation."""
        criterion = DuplicateCriterion()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query is None

    def test_duplicate_criterion_filter_no_duplicates(self, mock_account: EMailAccount) -> None:
        """Test DuplicateCriterion with no duplicates."""
        from unittest.mock import patch

        mock_msg1 = MagicMock()
        mock_msg1.message_id = "msg1@example.com"
        mock_msg1.subject = "Subject 1"
        mock_msg1.from_ = [("Name", "from1@example.com")]
        mock_msg1.date = "2025-01-01"

        mock_msg2 = MagicMock()
        mock_msg2.message_id = "msg2@example.com"
        mock_msg2.subject = "Subject 2"
        mock_msg2.from_ = [("Name", "from2@example.com")]
        mock_msg2.date = "2025-01-02"

        mock_account.connection.search = MagicMock(return_value=[1, 2])
        mock_account.connection.fetch = MagicMock(
            return_value={
                1: {b"BODY[]": b"Message 1", b"FLAGS": []},
                2: {b"BODY[]": b"Message 2", b"FLAGS": []},
            }
        )

        with patch("imap_thingy.filters.criteria.base.mailparser") as mock_mailparser:
            mock_mailparser.parse_from_bytes.side_effect = [mock_msg1, mock_msg2]
            criterion = DuplicateCriterion()
            result = criterion.filter(mock_account.connection)
            assert result == []

    def test_duplicate_criterion_filter_with_duplicates(self, mock_account: EMailAccount) -> None:
        """Test DuplicateCriterion with duplicate messages."""
        from unittest.mock import patch

        mock_msg1 = MagicMock()
        mock_msg1.message_id = "<duplicate@example.com>"
        mock_msg1.subject = "Test"
        mock_msg1.from_ = [("Name", "test@example.com")]
        mock_msg1.date = "2025-01-01"

        mock_msg2 = MagicMock()
        mock_msg2.message_id = "<duplicate@example.com>"
        mock_msg2.subject = "Test"
        mock_msg2.from_ = [("Name", "test@example.com")]
        mock_msg2.date = "2025-01-01"

        mock_account.connection.search = MagicMock(return_value=[1, 2])
        mock_account.connection.fetch = MagicMock(
            return_value={
                1: {b"BODY[]": b"Message 1", b"FLAGS": []},
                2: {b"BODY[]": b"Message 2", b"FLAGS": []},
            }
        )

        with patch("imap_thingy.filters.criteria.base.mailparser") as mock_mailparser:
            mock_mailparser.parse_from_bytes.side_effect = [mock_msg1, mock_msg2]
            criterion = DuplicateCriterion()
            result = criterion.filter(mock_account.connection)
            assert len(result) == 1
            assert result[0] == 2


class TestSpecialCriteria:
    """Test special criteria."""

    def test_select_all_creation(self) -> None:
        """Test SelectAll criterion creation."""
        criterion = SelectAll()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query == ["ALL"]


class TestCriteriaFiltering:
    """Test actual filtering logic with mocked messages."""

    def test_efficient_criterion_uses_search(self, mock_account: EMailAccount) -> None:
        """Test that EfficientCriterion uses search instead of fetch."""
        mock_account.connection.search = MagicMock(return_value=[1, 2, 3])
        criterion = SelectAll()
        result = criterion.filter(mock_account.connection)
        assert result == [1, 2, 3]
        mock_account.connection.search.assert_called_once_with(["ALL"])
        mock_account.connection.fetch.assert_not_called()

    def test_criteria_and_combination_imap_query(self) -> None:
        """Test that AND combination merges IMAP queries."""
        criterion1 = FromIs("sender@example.com")
        criterion2 = SubjectContains("Important")
        combined = criterion1 & criterion2
        assert combined.imap_query is not None
        assert "FROM" in combined.imap_query
        assert "SUBJECT" in combined.imap_query

    def test_criteria_or_combination_imap_query(self) -> None:
        """Test that OR combination creates OR query."""
        criterion1 = FromIs("sender1@example.com")
        criterion2 = FromIs("sender2@example.com")
        combined = criterion1 | criterion2
        assert combined.imap_query is not None
        assert combined.imap_query[0] == "OR"

    def test_criteria_negation_imap_query(self) -> None:
        """Test that negation creates NOT query."""
        criterion = FromIs("sender@example.com")
        negated = ~criterion
        assert negated.imap_query is not None
        assert negated.imap_query[0] == "NOT"
