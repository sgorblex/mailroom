"""IMAP search query representation: combine with &, |, ~; call .build() for imapclient."""

from __future__ import annotations

from datetime import date


class IMAPQuery:
    """IMAP search query; supports & (and), | (or), ~ (not). .build() returns list (AND), tuple (OR/NOT), or str/tuple (single criterion) for client.search()."""

    class And:
        """Logical AND of two IMAP queries."""

        def __init__(self, left: IMAPQuery, right: IMAPQuery) -> None:
            """Store left and right subqueries."""
            self.left = left
            self.right = right

        def _to_list(self, x: list | tuple | str) -> list:
            return x if isinstance(x, list) else [x]

        def build(self) -> list:
            """Return the IMAP list for this AND (flat list of criteria keys)."""
            return self._to_list(self.left.build()) + self._to_list(self.right.build())

        def __str__(self) -> str:
            return f"({self.left} & {self.right})"

    class Or:
        """Logical OR of two IMAP queries."""

        def __init__(self, left: IMAPQuery, right: IMAPQuery) -> None:
            """Store left and right subqueries."""
            self.left = left
            self.right = right

        def build(self) -> tuple:
            """Return nested binary OR for client.search() (IMAP OR is binary)."""
            return ("OR", self.left.build(), self.right.build())

        def __str__(self) -> str:
            return f"({self.left} | {self.right})"

    class Not:
        """Logical NOT of an IMAP query."""

        def __init__(self, query: IMAPQuery) -> None:
            """Store the subquery to negate."""
            self.query = query

        def build(self) -> tuple:
            """Return the NOT as a tuple (single key); AND wraps in list when combining."""
            return ("NOT", self.query.build())

        def __str__(self) -> str:
            return f"!({self.query})"

    class Base:
        r"""A single IMAP criterion (e.g. "ALL", ("FROM", "x"), ("SENTBEFORE", date))."""

        def __init__(self, query: str | tuple[str, str] | tuple[str, int] | tuple[str, date]) -> None:
            """Store the raw criterion (string or (key, value) tuple)."""
            self.query = query

        def build(self) -> str | tuple[str, str] | tuple[str, int] | tuple[str, date]:
            """Return the raw criterion."""
            return self.query

        def __str__(self) -> str:
            return str(self.query)

    query: And | Or | Not | Base

    def __init__(self, query: And | Or | Not | Base) -> None:
        """Wrap an And/Or/Not/Base query node."""
        self.query = query

    def build(self) -> list | tuple | str:
        r"""Return the IMAP search structure for this query (for use with client.search()). List for AND, tuple for OR/NOT, str or tuple for a single criterion."""
        return self.query.build()

    def __and__(self, other: IMAPQuery) -> IMAPQuery:
        return IMAPQuery(IMAPQuery.And(self, other))

    def __or__(self, other: IMAPQuery) -> IMAPQuery:
        return IMAPQuery(IMAPQuery.Or(self, other))

    def __invert__(self) -> IMAPQuery:
        return IMAPQuery(IMAPQuery.Not(self))

    def __str__(self) -> str:
        return str(self.query)


def build_base_query(query: str | tuple[str, str] | tuple[str, int] | tuple[str, date]) -> IMAPQuery:
    r"""Build an IMAPQuery from a single criterion (e.g. "ALL" or ("FROM", "a@b.com"))."""
    return IMAPQuery(IMAPQuery.Base(query))


Q = build_base_query
All = IMAPQuery(IMAPQuery.Base("ALL"))
