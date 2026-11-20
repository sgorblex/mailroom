"""Tests for criteria classes."""

from datetime import UTC, date, datetime
from itertools import pairwise
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import pytest

from mailroom_core.filters.criteria.address import (
    BccContains,
    BccIs,
    BccMatches,
    CcContains,
    CcIs,
    CcMatches,
    FromContains,
    FromDomain,
    FromIs,
    FromMatches,
    FromMatchesName,
    ToContains,
    ToIs,
    ToMatches,
)
from mailroom_core.filters.criteria.body import BodyContains, BodyMatches
from mailroom_core.filters.criteria.criterion import Anything, Criterion
from mailroom_core.filters.criteria.date import OlderThan, OlderThanDays
from mailroom_core.filters.criteria.flags import (
    IsAnswered,
    IsNotToSort,
    IsRead,
    IsStarred,
    IsToSort,
    IsUnanswered,
    IsUnread,
    IsUnstarred,
)
from mailroom_core.filters.criteria.lists import ListIs
from mailroom_core.filters.criteria.subject import SubjectContains, SubjectIs, SubjectMatches
from mailroom_core.filters.criteria.thread import InThread, ThreadMembers
from mailroom_core.filters.message import Message
from mailroom_core.mail import ParsedMessage, parsed_message
from mailroom_core.render import Address

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mailroom_core.filters.query import IMAPQuery


def _flat(items: list[object]) -> Iterator[object]:
    for item in items:
        if isinstance(item, list):
            yield from _flat(item)
        else:
            yield item


def _query_atoms(q: IMAPQuery) -> list[tuple[object, object]]:
    """Each (key, value) pair the query's criteria hold, groups flattened."""
    flat = list(_flat(q.criteria()))
    return list(pairwise(flat))


def _parsed(**fields: Any) -> ParsedMessage:
    """A parsed message with `fields` and nothing else."""
    return ParsedMessage(
        **{"sender": (), "to": (), "cc": (), "bcc": (), "subject": "", "date": None, "text": ""}
        | fields
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

    def test_if_then_returns_filter(self) -> None:
        """Test If(c).then(action) returns Filter."""
        from mailroom_core.filters.actions.move import MoveTo
        from mailroom_core.filters.filter import Filter, If
        from mailroom_core.filters.path import Path

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
        assert criterion.imap_query.criteria() == ["FROM", "test@example.com"]

    def test_to_is_creation(self) -> None:
        """Test ToIs criterion creation."""
        criterion = ToIs("test@example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["TO", "test@example.com"]

    def test_to_is_without_cc_bcc(self) -> None:
        """Test ToIs criterion without CC and BCC."""
        criterion = ToIs("test@example.com", incl_cc=False, incl_bcc=False)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["TO", "test@example.com"]

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
        assert criterion.imap_query.criteria() == ["CC", "test@example.com"]

    def test_bcc_is_creation(self) -> None:
        """Test BccIs criterion creation."""
        criterion = BccIs("test@example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["BCC", "test@example.com"]

    @pytest.mark.parametrize(
        ("criterion", "field"),
        [
            (FromIs("ana@x.org"), "sender"),
            (ToIs("ana@x.org"), "to"),
            (CcIs("ana@x.org"), "cc"),
            (BccIs("ana@x.org"), "bcc"),
        ],
    )
    def test_exact_address_criteria_are_only_prefiltered(
        self, criterion: Criterion, field: str
    ) -> None:
        """The server's FROM/TO/CC/BCC search matches substrings: the address is checked once
        fetched, exactly but in any case."""
        from mailroom_core.filters.message import Message

        messages = {
            uid: Message(uid, _parsed(**{field: (Address(None, address),)}), [])
            for uid, address in enumerate(
                ["ana@x.org", "banana@x.org", "Ana@X.org", "ana@x.org.uk"]
            )
        }
        assert criterion.is_efficient is False
        assert sorted(criterion.select(messages)) == [0, 2]
        assert sorted((~criterion).select(messages)) == [1, 3]

    @pytest.mark.parametrize("criterion", [FromContains("x"), ToContains("x", incl_cc=True)])
    def test_contains_address_criteria_are_answered_by_the_server(
        self, criterion: Criterion
    ) -> None:
        assert criterion.is_efficient is True

    def test_from_matches_creation(self) -> None:
        """Test FromMatches criterion creation."""
        criterion = FromMatches(r".*@example\.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["ALL"]

    def test_from_contains_creation(self) -> None:
        """Test FromContains criterion creation."""
        criterion = FromContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["FROM", "example.com"]

    def test_to_contains_creation(self) -> None:
        """Test ToContains criterion creation."""
        criterion = ToContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["TO", "example.com"]

    def test_to_contains_without_cc_bcc(self) -> None:
        """Test ToContains criterion without CC and BCC."""
        criterion = ToContains("example.com", incl_cc=False, incl_bcc=False)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["TO", "example.com"]

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
        assert criterion.imap_query.criteria() == ["ALL"]

    def test_to_matches_default_does_not_include_cc_bcc(self) -> None:
        """Test ToMatches default excludes CC and BCC."""
        from mailroom_core.filters.message import Message

        parsed = _parsed(
            cc=(Address("Name", "cc@example.com"),), bcc=(Address("Name", "bcc@example.com"),)
        )
        msg = Message(0, parsed, [])
        criterion = ToMatches(r".*@example\.com")
        assert criterion.func(msg) is False

    def test_cc_contains_creation(self) -> None:
        """Test CcContains criterion creation."""
        criterion = CcContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["CC", "example.com"]

    def test_cc_matches_creation(self) -> None:
        """Test CcMatches criterion creation."""
        criterion = CcMatches(r".*@example\.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["ALL"]

    def test_bcc_contains_creation(self) -> None:
        """Test BccContains criterion creation."""
        criterion = BccContains("example.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["BCC", "example.com"]

    def test_bcc_matches_creation(self) -> None:
        """Test BccMatches criterion creation."""
        criterion = BccMatches(r".*@example\.com")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["ALL"]

    def test_from_matches_name_creation(self) -> None:
        """Test FromMatchesName criterion creation."""
        criterion = FromMatchesName(r"John.*")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["ALL"]

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
        assert criterion.imap_query.criteria() == ["SUBJECT", "Important"]

    def test_subject_is_creation(self) -> None:
        """Test SubjectIs uses SUBJECT IMAP prefilter and is not efficient."""
        criterion = SubjectIs("Exact Subject")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["SUBJECT", "Exact Subject"]
        assert criterion.is_efficient is False

    def test_subject_is_select_exact_match(self) -> None:
        """SubjectIs.select matches only when parsed subject equals exactly."""
        from mailroom_core.filters.message import Message

        exact = "Exact Subject"
        p_match = _parsed(subject=exact)
        p_substring = _parsed(subject=f"Re: {exact}")
        messages = {
            1: Message(1, p_match, []),
            2: Message(2, p_substring, []),
        }
        result = SubjectIs(exact).select(messages)
        assert result == {1: messages[1]}

    def test_from_and_subject_is_imap_query(self) -> None:
        """FromIs & SubjectIs uses FROM and SUBJECT in IMAP; exact match checked client-side."""
        combined = FromIs("sender@example.com") & SubjectIs("Exact Subject")
        assert combined.is_efficient is False
        atoms = list(_query_atoms(combined.imap_query))
        assert ("FROM", "sender@example.com") in atoms
        assert ("SUBJECT", "Exact Subject") in atoms

    def test_subject_matches_creation(self) -> None:
        """Test SubjectMatches criterion creation."""
        criterion = SubjectMatches(r"^\[.*\]")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["ALL"]


class TestBodyCriteria:
    """Test body-based criteria."""

    def test_body_contains_creation(self) -> None:
        """Test BodyContains criterion creation and IMAP query."""
        criterion = BodyContains("needle")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["BODY", "needle"]
        assert criterion.is_efficient is True

    def test_body_matches_creation(self) -> None:
        """Test BodyMatches uses ALL IMAP query and is not efficient."""
        criterion = BodyMatches(r"foo.*bar")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["ALL"]
        assert criterion.is_efficient is False


class TestDateCriteria:
    """Test date-based criteria."""

    def test_older_than_with_date(self) -> None:
        """Test OlderThan with date object."""
        cutoff = date(2025, 1, 1)
        criterion = OlderThan(cutoff)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["SENTBEFORE", date(2025, 1, 1)]

    def test_older_than_with_datetime(self) -> None:
        """Test OlderThan with datetime object."""
        cutoff = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        criterion = OlderThan(cutoff)
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["SENTBEFORE", date(2025, 1, 1)]

    def test_older_than_with_string(self) -> None:
        """Test OlderThan with string date."""
        criterion = OlderThan("01-Jan-2025")
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["SENTBEFORE", date(2025, 1, 1)]

    def test_older_than_days_is_evaluated_when_run(self) -> None:
        """OlderThanDays computes its cutoff each time it is used, not when built."""
        criterion = OlderThanDays(30)
        assert criterion.is_efficient is True
        today_path = "mailroom_core.filters.criteria.date._today"
        with patch(today_path, return_value=date(2026, 3, 31)) as today:
            assert criterion.imap_query.criteria() == ["SENTBEFORE", date(2026, 3, 1)]
            assert (criterion & IsRead()).imap_query.criteria() == [
                "SENTBEFORE",
                date(2026, 3, 1),
                "SEEN",
            ]
            today.return_value = date(2026, 4, 30)
            assert criterion.imap_query.criteria() == ["SENTBEFORE", date(2026, 3, 31)]
            msg = cast("Any", SimpleNamespace(parsed=SimpleNamespace(date=datetime(2026, 3, 30))))  # noqa: DTZ001
            assert criterion.func(msg) is True
            today.return_value = date(2026, 4, 29)
            assert criterion.func(msg) is False

    def test_older_than_days_zero_is_before_today(self) -> None:
        """OlderThanDays(0) selects mail sent before today."""
        with patch("mailroom_core.filters.criteria.date._today", return_value=date(2026, 3, 31)):
            assert OlderThanDays(0).imap_query.criteria() == ["SENTBEFORE", date(2026, 3, 31)]

    @pytest.mark.parametrize(
        ("days", "error"),
        [(-1, ValueError), (36501, ValueError), (1.5, TypeError), (True, TypeError)],
    )
    def test_older_than_days_rejects_bad_days(self, days: object, error: type[Exception]) -> None:
        """OlderThanDays takes a non-negative int only."""
        with pytest.raises(error):
            OlderThanDays(cast("Any", days))


class TestFlagCriteria:
    """Test flag-based criteria."""

    def test_is_read_creation(self) -> None:
        """Test IsRead criterion creation."""
        criterion = IsRead()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["SEEN"]

    def test_is_unread_creation(self) -> None:
        """Test IsUnread criterion creation."""
        criterion = IsUnread()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["UNSEEN"]

    def test_is_starred_creation(self) -> None:
        """Test IsStarred criterion creation."""
        criterion = IsStarred()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["FLAGGED"]

    def test_is_unstarred_creation(self) -> None:
        """Test IsUnstarred criterion creation."""
        criterion = IsUnstarred()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["UNFLAGGED"]

    def test_to_sort_searches_its_keyword_and_ignores_case(self) -> None:
        from mailroom_core.filters.message import Message

        assert IsToSort().imap_query.criteria() == ["KEYWORD", "$MailroomSort"]
        assert IsNotToSort().imap_query.criteria() == ["UNKEYWORD", "$MailroomSort"]
        marked = Message(0, _parsed(), [b"\\SEEN", b"$mailroomsort"])
        assert IsToSort().func(marked) is True
        assert IsNotToSort().func(marked) is False
        assert IsRead().func(marked) is True
        assert IsToSort().func(Message(1, _parsed(), [b"\\Seen"])) is False

    def test_is_answered_creation(self) -> None:
        """Test IsAnswered criterion creation."""
        criterion = IsAnswered()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["ANSWERED"]

    def test_is_unanswered_creation(self) -> None:
        """Test IsUnanswered criterion creation."""
        criterion = IsUnanswered()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["UNANSWERED"]


class TestSpecialCriteria:
    """Test special criteria."""

    def test_anything_creation(self) -> None:
        """Test Anything criterion creation."""
        criterion = Anything()
        assert isinstance(criterion, Criterion)
        assert criterion.imap_query.criteria() == ["ALL"]


class TestCriteriaFiltering:
    """Test actual filtering logic with mocked messages."""

    def test_anything_select_returns_all_messages(self) -> None:
        """Test that Anything().select(messages) returns all messages."""
        from mailroom_core.filters.message import Message

        criterion = Anything()
        p1 = _parsed(subject="one")
        p2 = _parsed(subject="two")
        messages = {1: Message(1, p1, []), 2: Message(2, p2, [])}
        result = criterion.select(messages)
        assert len(result) == 2
        assert result[1].parsed is p1
        assert result[2].parsed is p2
        assert criterion.imap_query.criteria() == ["ALL"]

    def test_criteria_and_combination_imap_query(self) -> None:
        """Test that AND combination merges IMAP queries."""
        criterion1 = FromIs("sender@example.com")
        criterion2 = SubjectContains("Important")
        combined = criterion1 & criterion2
        assert combined.imap_query is not None
        atoms = list(_query_atoms(combined.imap_query))
        assert ("FROM", "sender@example.com") in atoms
        assert ("SUBJECT", "Important") in atoms

    def test_body_contains_select_plain(self) -> None:
        """BodyContains.select matches substring on the decoded body."""
        from mailroom_core.filters.message import Message

        p = _parsed(text="hello invoice")
        messages = {1: Message(1, p, [])}
        assert BodyContains("invoice").select(messages) == messages
        assert BodyContains("missing").select(messages) == {}

    def test_body_contains_select_html_fallback(self) -> None:
        """Without a plain part, the HTML's visible text is matched: across tags, never in them."""
        from mailroom_core.filters.message import Message

        source = b"Content-Type: text/html; charset=utf-8\r\n\r\n<p class=x>total <b>due</b></p>"
        messages = {1: Message(1, parsed_message(source), [])}
        assert BodyContains("total due").select(messages) == messages
        assert BodyContains("class").select(messages) == {}

    def test_contains_criteria_ignore_case_as_the_server_does(self) -> None:
        """Combined with a criterion that needs a fetch, a `*_contains` is decided by its
        predicate, which must agree with the server's case-insensitive search."""
        from mailroom_core.filters.message import Message

        p = _parsed(
            sender=(Address(None, "news@shop.example"),),
            subject="Your ORDER",
            text="Invoice attached",
        )
        messages = {1: Message(1, p, [])}
        for contains in (
            FromContains("Shop.example"),
            SubjectContains("order"),
            BodyContains("INVOICE"),
        ):
            either = contains | FromIs("x@y.example")
            assert either.is_efficient is False
            assert either.select(messages) == messages

    def test_body_matches_select_multiline_dotall(self) -> None:
        """BodyMatches uses re.search with DOTALL across newlines."""
        from mailroom_core.filters.message import Message

        p = _parsed(text="line1\nline2")
        messages = {1: Message(1, p, [])}
        assert BodyMatches(r"line1.line2").select(messages) == messages

    def test_body_contains_and_from_imap_query(self) -> None:
        """AND with BodyContains merges BODY into IMAP query."""
        combined = FromIs("sender@example.com") & BodyContains("pay")
        atoms = list(_query_atoms(combined.imap_query))
        assert ("FROM", "sender@example.com") in atoms
        assert ("BODY", "pay") in atoms

    def test_criteria_or_combination_imap_query(self) -> None:
        """Test that OR combination creates OR query."""
        criterion1 = FromIs("sender1@example.com")
        criterion2 = FromIs("sender2@example.com")
        combined = criterion1 | criterion2
        assert combined.imap_query is not None
        assert combined.imap_query.criteria() == [
            "OR",
            "FROM",
            "sender1@example.com",
            "FROM",
            "sender2@example.com",
        ]

    def test_criteria_negation_imap_query(self) -> None:
        """Negating an exact query gives its NOT; a prefilter's NOT would drop matches, so a
        negated prefilter searches everything and the predicate decides."""
        assert (~FromContains("sender@example.com")).imap_query.criteria() == [
            "NOT",
            "FROM",
            "sender@example.com",
        ]
        for prefiltered in (FromIs("sender@example.com"), SubjectIs("x"), BodyMatches("x")):
            negated = ~prefiltered
            assert negated.is_efficient is False
            assert negated.imap_query.criteria() == ["ALL"]
            assert negated.negated_prefilter is prefiltered.imap_query


def _from(address: str) -> Message:
    return Message(0, _parsed(sender=(Address(None, address),)), [])


class TestFromDomain:
    def test_matches_the_exact_domain_in_any_case(self) -> None:
        criterion = FromDomain("Example.com")
        assert criterion.func(_from("news@EXAMPLE.com"))
        assert not criterion.func(_from("news@mail.example.com"))
        assert not criterion.func(_from("news@notexample.com"))
        assert not criterion.func(_from("example.com@evil.org"))
        assert not criterion.func(_from("@example.com"))

    def test_subdomains_only_when_asked(self) -> None:
        criterion = FromDomain("example.com", incl_subdomains=True)
        assert criterion.func(_from("a@example.com"))
        assert criterion.func(_from("a@mail.Example.com"))
        assert not criterion.func(_from("a@badexample.com"))

    def test_a_unicode_domain_matches_its_idna_form(self) -> None:
        for given, address in (
            ("bücher.de", "a@xn--bcher-kva.de"),
            ("xn--bcher-kva.de", "a@BÜCHER.de"),
        ):
            assert FromDomain(given).func(_from(address))

    def test_any_from_address_counts(self) -> None:
        parsed = _parsed(sender=(Address(None, "a@other.org"), Address(None, "b@example.com")))
        assert FromDomain("example.com").func(Message(0, parsed, []))

    def test_a_plain_domain_is_prefiltered_by_the_servers_from_search(self) -> None:
        assert FromDomain("Example.com").imap_query.criteria() == ["FROM", "@example.com"]
        assert FromDomain("example.com", incl_subdomains=True).imap_query.criteria() == [
            "FROM",
            "example.com",
        ]
        assert FromDomain("bücher.de").imap_query.criteria() == ["ALL"]
        assert FromDomain("xn--bcher-kva.de").imap_query.criteria() == ["ALL"]
        assert FromDomain("example.com").is_efficient is False

    @pytest.mark.parametrize("domain", ["", "a@example.com", "exa mple.com", "com", "::1", "a>b.c"])
    def test_refuses_what_is_no_domain(self, domain: str) -> None:
        with pytest.raises(ValueError, match="not a domain"):
            FromDomain(domain)


class TestListIs:
    def test_matches_the_list_id_in_any_case(self) -> None:
        message = Message(0, _parsed(list_id="News.Example.ORG"), [])
        assert ListIs("news.example.org").func(message)
        assert ListIs("<NEWS.example.org>").func(message)
        assert not ListIs("example.org").func(message)
        assert not ListIs("news.example.org").func(Message(0, _parsed(), []))

    def test_fetches_every_candidate(self) -> None:
        assert ListIs("a.example").imap_query.criteria() == ["ALL"]

    def test_refuses_an_empty_id(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            ListIs(" <> ")


class TestInThread:
    MEMBERS = ThreadMembers(frozenset({"<root@x.org>", "<b@x.org>"}), frozenset({42}))

    def criterion(self) -> InThread:
        return InThread("t1", threads=lambda name: self.MEMBERS if name == "t1" else None)

    @pytest.mark.parametrize(
        "fields",
        [
            {"message_id": "<b@x.org>"},
            {"in_reply_to": ("<other@x.org>", "<b@x.org>")},
            {"references": ("<root@x.org>", "<c@x.org>")},
        ],
    )
    def test_matches_by_any_threading_id(self, fields: dict[str, Any]) -> None:
        assert self.criterion().func(Message(0, _parsed(**fields), []))

    def test_matches_by_gmail_thread(self) -> None:
        assert self.criterion().func(Message(0, _parsed(), [], gmail_thread=42))
        assert not self.criterion().func(Message(0, _parsed(), [], gmail_thread=43))

    def test_never_by_subject(self) -> None:
        message = Message(0, _parsed(subject="Re: the thread", message_id="<new@x.org>"), [])
        assert not self.criterion().func(message)

    def test_an_unknown_thread_or_no_resolver_matches_nothing(self) -> None:
        message = Message(0, _parsed(message_id="<b@x.org>"), [], gmail_thread=42)
        assert not InThread("t1").func(message)
        assert not InThread("t2", threads=lambda _: None).func(message)

    def test_the_resolver_is_asked_once_when_built(self) -> None:
        asked: list[str] = []

        def threads(name: str) -> ThreadMembers:
            asked.append(name)
            return self.MEMBERS

        criterion = InThread("t1", threads=threads)
        for _ in range(3):
            criterion.func(Message(0, _parsed(), []))
        assert asked == ["t1"]
