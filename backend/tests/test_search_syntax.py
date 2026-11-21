"""The search syntax's parser against the vectors the web client's parser is held to as well."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from mailroom.mail.search_syntax import ParsedSearch, SearchSyntaxError, combined, parse_search

VECTORS = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "test" / "search-syntax-vectors.json"
)


def _vectors() -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = json.loads(VECTORS.read_text(encoding="utf-8"))
    return loaded


@pytest.mark.parametrize("vector", _vectors(), ids=lambda vector: repr(vector["input"]))
def test_the_parser_agrees_with_the_shared_vectors(vector: dict[str, Any]) -> None:
    if "error" in vector:
        with pytest.raises(SearchSyntaxError) as refused:
            parse_search(vector["input"])
        assert refused.value.code == vector["error"]
    else:
        assert parse_search(vector["input"]).as_json() == vector["parsed"]


def test_a_refusal_never_quotes_the_query() -> None:
    for query in ('from:"secret', "after:secret", "is:secret", "from:secret from:other"):
        with pytest.raises(SearchSyntaxError) as refused:
            parse_search(query)
        assert "secret" not in refused.value.message


def test_combined_keeps_what_both_ask_and_refuses_a_disagreement() -> None:
    fields = ParsedSearch(sender="ada", since=date(2026, 3, 1))
    both = combined(fields, parse_search("from:ada is:unread"), "differ")
    assert both == ParsedSearch(sender="ada", since=date(2026, 3, 1), unread=True)
    with pytest.raises(SearchSyntaxError) as refused:
        combined(fields, parse_search("from:bob"), "differ")
    assert (refused.value.code, refused.value.message) == ("conflict", "differ")
