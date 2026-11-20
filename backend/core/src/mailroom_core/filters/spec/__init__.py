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

What a spec names but does not hold, the host supplies as it loads it: the members of an
``in_thread`` criterion's thread (``threads``), and the connections a ``move_to_account`` action
moves to another account with (``accounts``).

Chains of the same combinator are flattened: ``a & b & c`` gives one ``and`` with three items.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Mapping
from importlib import resources
from typing import TYPE_CHECKING, Any, Final, cast

from mailroom_core.filters.actions.action import Action
from mailroom_core.filters.criteria.criterion import Criterion
from mailroom_core.filters.filter import Filter
from mailroom_core.filters.spec.nodes import NotSerializableError, SpecNode, SpecSerializable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from mailroom_core.filters.actions.transfer import AccountResolver
    from mailroom_core.filters.criteria.thread import ThreadResolver

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


def _load_leaf(
    node: Mapping[str, Any],
    base: type[SpecSerializable],
    host: Mapping[str, Any],
) -> Any:  # noqa: ANN401
    kind = base.__name__.lower()
    if set(node) - {"type", "args"}:
        raise SpecError(f"unexpected keys in {kind} leaf: {sorted(set(node) - {'type', 'args'})}")
    name = node["type"]
    cls = base._spec_registry.get(name) if isinstance(name, str) else None  # noqa: SLF001
    if cls is None:
        raise SpecError(f"unknown {kind} type {name!r}")
    args = _check_mapping(node.get("args", {}), f"{name} args")
    try:
        result = cls._from_spec_args(args, host)
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


def load_criterion(spec: Mapping[str, Any], *, threads: ThreadResolver | None = None) -> Criterion:
    """Build a criterion from its spec node; ``threads`` gives each ``in_thread`` its thread's
    members (none without it, so it matches nothing).

    Raises:
        SpecError: The spec is malformed or names an unknown type or argument.

    """
    host = {} if threads is None else {"threads": threads}
    return _load_criterion(spec, host)


def _load_criterion(spec: Mapping[str, Any], host: Mapping[str, Any]) -> Criterion:
    node = _check_mapping(spec, "criterion")

    def load(item: Mapping[str, Any]) -> Criterion:
        return _load_criterion(item, host)

    match _only_key(node, ("type", "and", "or", "not"), "criterion"):
        case "type":
            return cast("Criterion", _load_leaf(node, Criterion, host))
        case "not":
            return ~load(node["not"])
        case "and":
            return functools.reduce(Criterion.__and__, map(load, _check_items(node, "and")))
        case _:
            return functools.reduce(Criterion.__or__, map(load, _check_items(node, "or")))


def load_action(spec: Mapping[str, Any], *, accounts: AccountResolver | None = None) -> Action:
    """Build an action from its spec node; ``accounts`` gives each ``move_to_account`` the
    connections to its account, looked up when it moves (without it, its moves fail).

    Raises:
        SpecError: The spec is malformed or names an unknown type or argument.

    """
    host = {} if accounts is None else {"accounts": accounts}
    return _load_action(spec, host)


_REMOVING: Final = frozenset({"delete", "trash", "junk", "move_to", "move_to_account"})
"""Actions after which a message is no longer where the run found it."""


def _load_action(spec: Mapping[str, Any], host: Mapping[str, Any]) -> Action:
    node = _check_mapping(spec, "action")
    if _only_key(node, ("type", "seq"), "action") == "type":
        return cast("Action", _load_leaf(node, Action, host))
    combine: Callable[[Action, Action], Action] = Action.__add__
    items = [_load_action(item, host) for item in _check_items(node, "seq")]
    leaves = list(_leaf_types(node))
    if "move_to_account" in leaves:
        after = leaves[leaves.index("move_to_account") + 1 :]
        removing = next((leaf for leaf in after if leaf in _REMOVING), None)
        if removing is not None:
            raise SpecError(
                f"a seq cannot have {removing} after move_to_account, which takes the messages "
                "from the folder: put move_to_account last"
            )
    return functools.reduce(combine, items)


def _leaf_types(node: Mapping[str, Any]) -> Iterator[str]:
    """The types of a loaded action node's leaves, in order, nested seqs flattened."""
    if "seq" in node:
        for item in node["seq"]:
            yield from _leaf_types(item)
    else:
        yield node["type"]


def load_filter(
    spec: Mapping[str, Any],
    *,
    threads: ThreadResolver | None = None,
    accounts: AccountResolver | None = None,
) -> Filter:
    """Build a filter from ``{"criterion": ..., "action": ...}`` (``threads`` as
    `load_criterion` takes it, ``accounts`` as `load_action`).

    Raises:
        SpecError: The spec is malformed or names an unknown type or argument.

    """
    node = _check_mapping(spec, "filter")
    if set(node) != {"criterion", "action"}:
        raise SpecError(f'a filter spec has exactly "criterion" and "action", got {sorted(node)}')
    return Filter(
        load_criterion(node["criterion"], threads=threads),
        load_action(node["action"], accounts=accounts),
    )


@functools.cache
def _schema_text() -> str:
    return resources.files(__package__).joinpath("schema.json").read_text(encoding="utf-8")


def json_schema() -> dict[str, Any]:
    """Return the JSON Schema (draft 2020-12) of the spec format, shipped as ``schema.json``.

    Its ``$defs`` hold ``criterion``, ``action`` and ``filter``; the root validates a filter.
    """
    schema: dict[str, Any] = json.loads(_schema_text())
    return schema
