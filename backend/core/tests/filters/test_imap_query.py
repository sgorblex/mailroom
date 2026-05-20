"""Tests for IMAPQuery build() structure: list (AND), tuple (OR/NOT), str/tuple (single criterion)."""

from unittest.mock import MagicMock

from imapclient.imapclient import _normalise_search_criteria

from imap_thingy.core import Q
from imap_thingy.get_mail import SearchCriteria, _normalise_search_criteria_utf8, search_mail


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
        a, b, c = Q(("FROM", "a")), Q(("TO", "b")), Q(("SUBJECT", "c"))
        for query in [Q(("FROM", "a")).build(), (a | b).build(), ((a | b) & c).build()]:
            out = _normalise_search_criteria(query)
            assert isinstance(out, list)
            assert len(out) >= 1

    def test_utf8_criteria_normalises_nested_and_with_euro(self) -> None:
        """UTF-8 AND lists flatten simple pairs (no parens around each criterion)."""
        a, b = Q(("FROM", "a")), Q(("SUBJECT", "Price \u20ac99"))
        built = (a & b).build()
        out = _normalise_search_criteria_utf8(built, "UTF-8")
        subj = "Price \u20ac99"
        assert out == [b"FROM", b"a", b"SUBJECT", _normalise_search_criteria_utf8(("SUBJECT", subj), "UTF-8")[1]]
        assert b"(" not in out
        assert b")" not in out

    def test_utf8_flagged_and_body_euro_is_flat(self) -> None:
        body = "Importo: 0,00 \u20ac EUR"
        built: SearchCriteria = ["FLAGGED", ("BODY", body)]
        out = _normalise_search_criteria_utf8(built, "UTF-8")
        assert out == [b"FLAGGED", b"BODY", _normalise_search_criteria_utf8(("BODY", body), "UTF-8")[1]]
        assert b"(" not in out

    def test_utf8_flagged_and_or_keeps_group_parens(self) -> None:
        built: SearchCriteria = ["FLAGGED", ("OR", ("TO", "a"), ("CC", "a"))]
        out = _normalise_search_criteria_utf8(built, "UTF-8")
        assert out[0] == b"FLAGGED"
        assert out[1] == b"(OR"
        assert out[-1] == b"a)"
        assert b"TO" in out
        assert b"CC" in out

    def test_search_mail_uses_uid_search_path(self) -> None:
        client = MagicMock()
        client._raw_command_untagged.return_value = [b""]
        search_mail(client, Q(("SUBJECT", "Price \u20ac99")))
        client._raw_command_untagged.assert_called_once()
        args, _kwargs = client._raw_command_untagged.call_args
        assert args[0] == b"SEARCH"
        assert args[1][0:2] == [b"CHARSET", b"UTF-8"]
