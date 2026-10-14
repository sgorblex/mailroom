"""Rules as data: load criteria, actions and filters from JSON-compatible spec nodes.

``to_spec()`` on a built-in criterion, action or filter returns its spec; the ``load_*``
functions rebuild an equal object from it. The format, described by :func:`json_schema`:

- **leaf**: ``{"type": "<name>", "args": {...}}``, ``args`` omitted when the type takes none.
  ``<name>`` is the class name in ``snake_case`` (``FromIs`` → ``"from_is"``); ``args`` are the
  constructor arguments by parameter name, defaults included. Paths are lists of segments
  (``["Archive", "2025"]``), flags are ``Flag`` member names (``"SEEN"``), dates are ISO 8601
  (``"2025-01-31"``).
- **criterion combinators**: ``{"and": [c, ...]}``, ``{"or": [c, ...]}``, ``{"not": c}``.
- **action sequence**: ``{"seq": [a, ...]}``.
- **filter**: ``{"criterion": c, "action": a}``.

Chains of the same combinator are flattened: ``a & b & c`` gives one ``and`` with three items.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Mapping
from importlib import resources
from typing import TYPE_CHECKING, Any, cast

from imap_thingy.core.spec import NotSerializableError, SpecNode, SpecSerializable
from imap_thingy.filters import Action, Criterion, Filter

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "NotSerializableError",
    "SpecError",
    "SpecNode",
    "json_schema",
    "load_action",
    "load_criterion",
    "load_filter",
]


class SpecError(ValueError):
    """Raised when a spec node is malformed or names an unknown type or argument."""


def _check_mapping(node: object, what: str) -> Mapping[str, Any]:
    if not isinstance(node, Mapping):
        raise SpecError(f"{what} spec must be an object, got {type(node).__name__}")
    return node


def _check_items(node: Mapping[str, Any], key: str) -> list[Any]:
    items = node[key]
    if not isinstance(items, list) or not items:
        raise SpecError(f'"{key}" must be a non-empty list')
    return items


def _load_leaf(node: Mapping[str, Any], base: type[SpecSerializable]) -> Any:  # noqa: ANN401
    kind = base.__name__.lower()
    if set(node) - {"type", "args"}:
        raise SpecError(f"unexpected keys in {kind} leaf: {sorted(set(node) - {'type', 'args'})}")
    name = node["type"]
    cls = base._spec_registry.get(name) if isinstance(name, str) else None  # noqa: SLF001
    if cls is None:
        raise SpecError(f"unknown {kind} type {name!r}")
    args = _check_mapping(node.get("args", {}), f"{name} args")
    try:
        result = cls._from_spec_args(args)
    except (TypeError, ValueError) as e:
        raise SpecError(f"invalid args for {name!r}: {e}") from e
    if result._spec is None:  # noqa: SLF001
        raise SpecError(f"invalid args for {name!r}: an argument has no JSON form")
    return result


def _only_key(node: Mapping[str, Any], keys: tuple[str, ...], what: str) -> str:
    present = [k for k in keys if k in node]
    if len(present) != 1 or ("type" not in node and len(node) != 1):
        raise SpecError(f"{what} spec must have exactly one of {list(keys)}, got {sorted(node)}")
    return present[0]


def load_criterion(spec: Mapping[str, Any]) -> Criterion:
    """Build a criterion from its spec node.

    Raises:
        SpecError: The spec is malformed or names an unknown type or argument.

    """
    node = _check_mapping(spec, "criterion")
    match _only_key(node, ("type", "and", "or", "not"), "criterion"):
        case "type":
            return cast("Criterion", _load_leaf(node, Criterion))
        case "not":
            return ~load_criterion(node["not"])
        case "and":
            return functools.reduce(
                Criterion.__and__, map(load_criterion, _check_items(node, "and"))
            )
        case _:
            return functools.reduce(Criterion.__or__, map(load_criterion, _check_items(node, "or")))


def load_action(spec: Mapping[str, Any]) -> Action:
    """Build an action from its spec node.

    Raises:
        SpecError: The spec is malformed or names an unknown type or argument.

    """
    node = _check_mapping(spec, "action")
    if _only_key(node, ("type", "seq"), "action") == "type":
        return cast("Action", _load_leaf(node, Action))
    combine: Callable[[Action, Action], Action] = Action.__add__
    return functools.reduce(combine, map(load_action, _check_items(node, "seq")))


def load_filter(spec: Mapping[str, Any]) -> Filter:
    """Build a filter from ``{"criterion": ..., "action": ...}``.

    Raises:
        SpecError: The spec is malformed or names an unknown type or argument.

    """
    node = _check_mapping(spec, "filter")
    if set(node) != {"criterion", "action"}:
        raise SpecError(f'a filter spec has exactly "criterion" and "action", got {sorted(node)}')
    return Filter(load_criterion(node["criterion"]), load_action(node["action"]))


@functools.cache
def _schema_text() -> str:
    return resources.files(__package__).joinpath("schema.json").read_text(encoding="utf-8")


def json_schema() -> dict[str, Any]:
    """Return the JSON Schema (draft 2020-12) of the spec format, shipped as ``schema.json``.

    Its ``$defs`` hold ``criterion``, ``action`` and ``filter``; the root validates a filter.
    """
    schema: dict[str, Any] = json.loads(_schema_text())
    return schema
