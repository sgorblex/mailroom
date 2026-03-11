"""Tests for IMAPQuery build() structure: list (AND), tuple (OR/NOT), str/tuple (single criterion)."""

from imap_thingy.core import Q


class TestIMAPQueryBuildStructure:
    """Test that build() returns the shape expected by client.search()."""

    def test_base_returns_raw_criterion(self) -> None:
        b = Q(("FROM", "x@y.com")).build()
        assert b == ("FROM", "x@y.com")
        assert Q("ALL").build() == "ALL"

    def test_standalone_or_returns_nested_tuple(self) -> None:
        a, b = Q(("FROM", "a")), Q(("TO", "b"))
        built = (a | b).build()
        assert built == ("OR", ("FROM", "a"), ("TO", "b"))

    def test_or_three_way_is_nested_binary(self) -> None:
        a, b, c = Q(("FROM", "a")), Q(("TO", "b")), Q(("SUBJECT", "c"))
        built = (a | b | c).build()
        assert built == ("OR", ("OR", ("FROM", "a"), ("TO", "b")), ("SUBJECT", "c"))

    def test_standalone_not_returns_tuple(self) -> None:
        built = (~Q(("FROM", "x"))).build()
        assert built == ("NOT", ("FROM", "x"))

    def test_and_of_two_base_returns_list_of_tuples(self) -> None:
        built = (Q(("FROM", "a")) & Q(("TO", "b"))).build()
        assert built == [("FROM", "a"), ("TO", "b")]

    def test_or_and_base_returns_list_with_or_tuple_then_criterion(self) -> None:
        a, b, c = Q(("FROM", "a")), Q(("TO", "b")), Q(("SUBJECT", "c"))
        built = ((a | b) & c).build()
        assert built == [("OR", ("FROM", "a"), ("TO", "b")), ("SUBJECT", "c")]

    def test_or_and_or_returns_list_of_two_or_tuples(self) -> None:
        a, b, c, d = Q(("FROM", "a")), Q(("TO", "b")), Q(("SUBJECT", "c")), Q(("BCC", "d"))
        built = ((a | b) & (c | d)).build()
        assert built == [("OR", ("FROM", "a"), ("TO", "b")), ("OR", ("SUBJECT", "c"), ("BCC", "d"))]

    def test_base_and_or_and_base_returns_list_crit_or_crit(self) -> None:
        a, b, c, d = Q(("FROM", "a")), Q(("TO", "b")), Q(("SUBJECT", "c")), Q(("BCC", "d"))
        built = (a & (b | c) & d).build()
        assert built == [("FROM", "a"), ("OR", ("TO", "b"), ("SUBJECT", "c")), ("BCC", "d")]

    def test_imapclient_normalises_built_output(self) -> None:
        from imapclient.imapclient import _normalise_search_criteria

        a, b, c = Q(("FROM", "a")), Q(("TO", "b")), Q(("SUBJECT", "c"))
        for query in [Q(("FROM", "a")).build(), (a | b).build(), ((a | b) & c).build()]:
            out = _normalise_search_criteria(query)
            assert isinstance(out, list)
            assert len(out) >= 1
