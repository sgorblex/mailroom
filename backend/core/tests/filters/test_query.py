"""IMAPQuery.criteria(): core's SEARCH criteria, flat for AND, a group for an AND inside OR or NOT."""

from datetime import date

from mailroom_core.filters.query import IMAPQuery, Q
from mailroom_core.mail import search_arguments


class TestCriteria:
    """The criteria each kind of query gives."""

    def test_base_is_its_key_and_value(self) -> None:
        assert Q(("FROM", "x@y.com")).criteria() == ["FROM", "x@y.com"]
        assert Q("ALL").criteria() == ["ALL"]
        assert Q(("SENTBEFORE", date(2026, 3, 1))).criteria() == ["SENTBEFORE", date(2026, 3, 1)]

    def test_and_concatenates(self) -> None:
        assert (Q(("FROM", "a")) & Q(("TO", "b")) & Q("UNSEEN")).criteria() == [
            "FROM",
            "a",
            "TO",
            "b",
            "UNSEEN",
        ]

    def test_or_and_not_take_single_keys_flat(self) -> None:
        a, b = Q(("FROM", "a")), Q(("TO", "b"))
        assert (a | b).criteria() == ["OR", "FROM", "a", "TO", "b"]
        assert (~a).criteria() == ["NOT", "FROM", "a"]

    def test_or_and_not_are_single_keys_themselves(self) -> None:
        a, b, c = Q(("FROM", "a")), Q(("TO", "b")), Q(("SUBJECT", "c"))
        assert (a | b | c).criteria() == ["OR", "OR", "FROM", "a", "TO", "b", "SUBJECT", "c"]
        assert (~(a | b)).criteria() == ["NOT", "OR", "FROM", "a", "TO", "b"]
        assert (~~a).criteria() == ["NOT", "NOT", "FROM", "a"]

    def test_an_and_inside_or_or_not_is_a_group(self) -> None:
        a, b, c = Q(("FROM", "a")), Q(("TO", "b")), Q(("SUBJECT", "c"))
        assert ((a & b) | c).criteria() == ["OR", ["FROM", "a", "TO", "b"], "SUBJECT", "c"]
        assert (~(a & b)).criteria() == ["NOT", ["FROM", "a", "TO", "b"]]

    def test_or_inside_and_stays_flat(self) -> None:
        a, b, c, d = Q(("FROM", "a")), Q(("TO", "b")), Q(("SUBJECT", "c")), Q(("BCC", "d"))
        assert (a & (b | c) & d).criteria() == [
            "FROM",
            "a",
            "OR",
            "TO",
            "b",
            "SUBJECT",
            "c",
            "BCC",
            "d",
        ]

    def test_deferred_is_made_at_each_call(self) -> None:
        made: list[int] = []

        def make() -> IMAPQuery:
            made.append(1)
            return Q(("SENTBEFORE", date(2026, 3, len(made))))

        deferred = IMAPQuery(IMAPQuery.Deferred(make))
        assert deferred.criteria() == ["SENTBEFORE", date(2026, 3, 1)]
        assert deferred.criteria() == ["SENTBEFORE", date(2026, 3, 2)]

    def test_deferred_inside_not_and_or_is_its_query(self) -> None:
        base = Q(("SENTBEFORE", date(2026, 3, 1)))
        deferred = IMAPQuery(IMAPQuery.Deferred(lambda: base))
        both = IMAPQuery(IMAPQuery.Deferred(lambda: base & Q("SEEN")))
        assert (~deferred | Q(("FROM", "a"))).criteria() == (~base | Q(("FROM", "a"))).criteria()
        assert (~both).criteria() == ["NOT", ["SENTBEFORE", date(2026, 3, 1), "SEEN"]]


class TestWritten:
    """What core's `search_arguments` writes of them."""

    def test_a_group_and_a_date(self) -> None:
        a = Q(("FROM", "a")) & Q(("SENTBEFORE", date(2026, 3, 1)))
        arguments, utf8 = search_arguments((~a | Q("FLAGGED")).criteria())
        assert arguments == [
            b"OR",
            b"NOT",
            b"(FROM",
            b"a",
            b"SENTBEFORE",
            b"01-Mar-2026)",
            b"FLAGGED",
        ]
        assert not utf8

    def test_non_ascii_text_needs_charset_utf8(self) -> None:
        arguments, utf8 = search_arguments(
            (Q("FLAGGED") & Q(("BODY", "Importo: 0,00 €"))).criteria()
        )
        assert arguments[:2] == [b"FLAGGED", b"BODY"]
        assert utf8
