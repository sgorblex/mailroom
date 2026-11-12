"""IMAP search query representation: combine with &, |, ~; `.criteria()` gives core's SEARCH
criteria (`mailroom_core.mail.Criteria`), which `mail.search_uids` writes and sends."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

type BaseQuery = str | tuple[str, str] | tuple[str, int] | tuple[str, date]
"""A single search key, alone (`"SEEN"`) or with its value (`("FROM", "a@b.example")`)."""


class IMAPQuery:
    """IMAP search query; supports & (and), | (or), ~ (not).

    `.criteria()` is the query as a flat list of search keys and values, as
    `mailroom_core.mail.search_uids` takes it: an AND is the concatenation of its parts, an OR or
    a NOT is its keyword followed by its operands, each a single key as it is or a nested list
    (a parenthesised group) for an AND of several.
    """

    class And:
        """Logical AND of two IMAP queries."""

        def __init__(self, left: IMAPQuery, right: IMAPQuery) -> None:
            """Store left and right subqueries."""
            self.left = left
            self.right = right

        def criteria(self) -> list[object]:
            """Both sides' criteria, one after the other (SEARCH ANDs its keys)."""
            return [*self.left.criteria(), *self.right.criteria()]

        def __str__(self) -> str:
            return f"({self.left} & {self.right})"

    class Or:
        """Logical OR of two IMAP queries."""

        def __init__(self, left: IMAPQuery, right: IMAPQuery) -> None:
            """Store left and right subqueries."""
            self.left = left
            self.right = right

        def criteria(self) -> list[object]:
            """`OR` and its two operands (IMAP's OR is binary)."""
            return ["OR", *_operand(self.left), *_operand(self.right)]

        def __str__(self) -> str:
            return f"({self.left} | {self.right})"

    class Not:
        """Logical NOT of an IMAP query."""

        def __init__(self, query: IMAPQuery) -> None:
            """Store the subquery to negate."""
            self.query = query

        def criteria(self) -> list[object]:
            """`NOT` and its operand."""
            return ["NOT", *_operand(self.query)]

        def __str__(self) -> str:
            return f"!({self.query})"

    class Base:
        r"""A single IMAP criterion (e.g. "ALL", ("FROM", "x"), ("SENTBEFORE", date))."""

        def __init__(self, query: BaseQuery) -> None:
            """Store the raw criterion (string or (key, value) tuple)."""
            self.query = query

        def criteria(self) -> list[object]:
            """The key, and its value when it has one."""
            return [self.query] if isinstance(self.query, str) else list(self.query)

        def __str__(self) -> str:
            return str(self.query)

    class Deferred:
        """A query made when searched, for criteria relative to the time of the run."""

        def __init__(self, make: Callable[[], IMAPQuery]) -> None:
            """Store the function building the query."""
            self.make = make

        def criteria(self) -> list[object]:
            """The criteria of the query make() returns now."""
            return self.make().criteria()

        def __str__(self) -> str:
            return str(self.make())

    query: And | Or | Not | Base | Deferred

    def __init__(self, query: And | Or | Not | Base | Deferred) -> None:
        """Wrap an And/Or/Not/Base/Deferred query node."""
        self.query = query

    def criteria(self) -> list[object]:
        """This query as core's SEARCH criteria (`mailroom_core.mail.Criteria`), e.g.
        `["FROM", "a", "OR", "TO", "b", ["CC", "b", "UNSEEN"]]`."""
        return self.query.criteria()

    def __and__(self, other: IMAPQuery) -> IMAPQuery:
        return IMAPQuery(IMAPQuery.And(self, other))

    def __or__(self, other: IMAPQuery) -> IMAPQuery:
        return IMAPQuery(IMAPQuery.Or(self, other))

    def __invert__(self) -> IMAPQuery:
        return IMAPQuery(IMAPQuery.Not(self))

    def __str__(self) -> str:
        return str(self.query)


def _operand(query: IMAPQuery) -> list[object]:
    """`query` as one operand of OR or NOT: a single key (an OR and a NOT are one too) as it is,
    an AND of several as a group."""
    node = query.query
    while isinstance(node, IMAPQuery.Deferred):
        node = node.make().query
    items = node.criteria()
    return [items] if isinstance(node, IMAPQuery.And) else items


def build_base_query(query: BaseQuery) -> IMAPQuery:
    r"""Build an IMAPQuery from a single criterion (e.g. "ALL" or ("FROM", "a@b.com"))."""
    return IMAPQuery(IMAPQuery.Base(query))


Q = build_base_query
All = IMAPQuery(IMAPQuery.Base("ALL"))
