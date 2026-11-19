"""Filter specs: the filter engine's rules as data (``mailroom_core.filters.spec``), checked before
they are kept."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, Final

import regex
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from mailroom_core.filters import SpecError, json_schema, load_filter

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mailroom_core.filters import AccountResolver, Filter, ThreadResolver


HIDDEN_CRITERIA: Final = frozenset({"in_thread"})
"""Criteria the app keeps for its own rule sets (a muted thread's): not in the schema it offers,
refused in a spec a user writes (``hidden_criterion``)."""


class InvalidSpecError(ValueError):
    """A spec the filter engine does not load. The message is the engine's ``SpecError`` text, the
    schema's complaint, or the regular expression's error."""


def loaded_filter(
    spec: Mapping[str, Any],
    *,
    threads: ThreadResolver | None = None,
    accounts: AccountResolver | None = None,
) -> Filter:
    """The filter engine's filter for ``spec``, ``threads`` giving an ``in_thread`` its thread's
    members (without it, it matches nothing) and ``accounts`` a ``move_to_account`` its target
    (without it, its moves fail). Raises ``InvalidSpecError``."""
    try:
        return load_filter(spec, threads=threads, accounts=accounts)
    except SpecError as exc:
        raise InvalidSpecError(str(exc)) from None
    except RecursionError:
        msg = "the spec is nested too deeply"
        raise InvalidSpecError(msg) from None


def normalized_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """``spec`` as the filter engine serialises the filter it loads to (defaults filled in, chains
    of one combinator flattened): the form a filter is stored in. Also refused: a spec the engine
    loads but its schema does not describe (an argument of the wrong type, say), and a ``pattern``
    that is no regular expression. Raises ``InvalidSpecError``."""
    normalized = loaded_filter(spec).to_spec()
    try:
        error = best_match(_validator().iter_errors(normalized))
        if error is not None:
            where = "/".join(str(part) for part in error.absolute_path) or "the spec"
            msg = f"{where}: {error.message}"
            raise InvalidSpecError(msg)
        _check_patterns(normalized)
    except RecursionError:
        msg = "the spec is nested too deeply"
        raise InvalidSpecError(msg) from None
    return normalized


@functools.cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json_schema())


def _check_patterns(node: object) -> None:
    """Every leaf's ``pattern`` argument (the ``*_matches`` criteria) compiles, with ``regex`` as
    the filter engine matches it."""
    if isinstance(node, list):
        for item in node:
            _check_patterns(item)
        return
    if not isinstance(node, dict):
        return
    args = node.get("args")
    if "type" in node and isinstance(args, dict) and isinstance(args.get("pattern"), str):
        try:
            regex.compile(args["pattern"])
        except regex.error as exc:
            msg = f"{node['type']}: invalid regular expression ({exc})"
            raise InvalidSpecError(msg) from None
    for value in node.values():
        _check_patterns(value)


def offered_schema() -> dict[str, Any]:
    """The engine's JSON Schema without ``HIDDEN_CRITERIA``: the spec the web editor and agents
    are offered."""
    schema = json_schema()
    leaves = []
    for leaf in schema["$defs"]["criterion_leaf"]["oneOf"]:
        names = leaf["properties"]["type"]["enum"]
        kept = [name for name in names if name not in HIDDEN_CRITERIA]
        if kept:
            leaf["properties"]["type"]["enum"] = kept
            leaves.append(leaf)
    schema["$defs"]["criterion_leaf"]["oneOf"] = leaves
    return schema


def hidden_criterion(node: object) -> str | None:
    """The first of ``HIDDEN_CRITERIA`` anywhere in ``node``, a criterion, or None."""
    if isinstance(node, list):
        return next(filter(None, map(hidden_criterion, node)), None)
    if not isinstance(node, dict):
        return None
    if node.get("type") in HIDDEN_CRITERIA:
        return str(node["type"])
    return next(filter(None, map(hidden_criterion, node.values())), None)
