"""Spec nodes: the JSON-compatible data form of criteria and actions.

A built-in criterion or action class declares ``spec_type`` (e.g. ``"from_is"``). Its
constructor arguments, bound with defaults applied, are recorded as the leaf node
``{"type": spec_type, "args": {...}}`` (``args`` omitted when empty). Arguments that are not
JSON scalars go through the class's ``spec_codecs``. Combinators build ``{"and": [...]}``,
``{"or": [...]}``, ``{"not": node}`` and ``{"seq": [...]}`` from their operands' nodes.

Loading lives in :mod:`imap_thingy.spec`; this module only records and renders nodes, so that
the criterion and action base classes can use it without import cycles.
"""

from __future__ import annotations

import functools
import inspect
import json
from datetime import date, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Self

from imap_thingy.core.flags import Flag
from imap_thingy.core.path import Path

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

type SpecNode = dict[str, Any]
"""A JSON-compatible spec node (see the module docstring)."""

_SCALARS = (str, bool, int, float, type(None))


class NotSerializableError(TypeError):
    """Raised by ``to_spec()`` on a criterion or action that has no data form.

    This is the case for criteria and actions built directly from a function
    (``Criterion(func, ...)``, ``Action(func, ...)``), subclasses that declare no ``spec_type``,
    combinations involving either, and built-ins given arguments with no JSON form.
    """


class Codec(NamedTuple):
    """Converts one constructor argument to its JSON form and back."""

    encode: Callable[[Any], Any]
    decode: Callable[[Any], Any]


def _encode_path(value: object) -> list[str]:
    if not isinstance(value, Path):
        raise TypeError(f"expected Path, got {type(value).__name__}")
    return list(value.segments)


def _decode_path(value: object) -> Path:
    if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
        raise TypeError("a path must be a list of segment strings")
    return Path(value)


def _encode_flag(value: object) -> str:
    if not isinstance(value, Flag):
        raise TypeError(f"expected Flag, got {type(value).__name__}")
    return value.name


def _decode_flag(value: object) -> Flag:
    if not isinstance(value, str) or value not in Flag.__members__:
        raise ValueError(f"unknown flag {value!r}; expected one of {list(Flag.__members__)}")
    return Flag[value]


def as_date(value: date | datetime | str) -> date:
    """Normalise a date, a datetime (its date part) or a ``DD-MMM-YYYY`` string to a date."""
    if isinstance(value, str):
        return datetime.strptime(value, "%d-%b-%Y").date()  # noqa: DTZ007 -- date only
    if isinstance(value, datetime):
        return value.date()
    return value


def _decode_date(value: object) -> date:
    if not isinstance(value, str):
        raise TypeError("a date must be an ISO 8601 string (YYYY-MM-DD)")
    return date.fromisoformat(value)


PATH = Codec(_encode_path, _decode_path)
"""Path ↔ list of segments (delimiter-agnostic, as :class:`~imap_thingy.core.Path`)."""
OPTIONAL_PATH = Codec(
    lambda v: None if v is None else _encode_path(v),
    lambda v: None if v is None else _decode_path(v),
)
"""Like :data:`PATH`, with ``None`` ↔ ``null``."""
FLAG = Codec(_encode_flag, _decode_flag)
"""Flag ↔ its enum member name (``"SEEN"``, ``"FLAGGED"``, ``"ANSWERED"``)."""
DATE = Codec(lambda v: as_date(v).isoformat(), _decode_date)
"""date / datetime / ``DD-MMM-YYYY`` string → ISO 8601 date string (decoded to a date)."""


def codecs(**by_argument: Codec) -> Mapping[str, Codec]:
    """Build a read-only ``spec_codecs`` mapping (argument name → codec)."""
    return MappingProxyType(by_argument)


def _encode_plain(value: object) -> object:
    if not isinstance(value, _SCALARS):
        raise TypeError(f"{type(value).__name__} has no JSON form")
    return value


def _canonical(node: SpecNode) -> str:
    # Compared as JSON text rather than as dicts so that equality agrees with the hash
    # (``1 == True`` as dict values, but they serialise differently).
    return json.dumps(node, sort_keys=True)


class SpecSerializable:
    """Mixin recording a spec node per instance; base of Criterion and Action.

    A subclass that sets ``spec_type`` in its own body is registered under that name and its
    ``__init__`` is wrapped to record the leaf node. Only the most derived class with a
    ``spec_type`` records it, and only when it is the instance's exact type: a subclass without
    its own ``spec_type`` is not serialisable, since its behaviour may differ from its parent's.
    """

    spec_type: ClassVar[str]
    spec_codecs: ClassVar[Mapping[str, Codec]] = MappingProxyType({})
    _spec_registry: ClassVar[dict[str, type[Any]]]
    _spec: SpecNode | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401 -- forwarded as-is
        """Register a class declaring ``spec_type`` and wrap its ``__init__``."""
        super().__init_subclass__(**kwargs)
        if "spec_type" not in cls.__dict__:
            return
        if cls.spec_type in cls._spec_registry:
            raise ValueError(f"duplicate spec_type {cls.spec_type!r} for {cls.__qualname__}")
        cls._spec_registry[cls.spec_type] = cls
        init = cls.__init__
        signature = inspect.signature(init)

        @functools.wraps(init)
        def __init__(self: SpecSerializable, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401, N807
            init(self, *args, **kwargs)
            if type(self) is cls:
                bound = signature.bind(self, *args, **kwargs)
                bound.apply_defaults()
                self._spec = cls._leaf(dict(bound.arguments))

        cls.__init__ = __init__  # type: ignore[method-assign, assignment]

    @classmethod
    def _leaf(cls, arguments: dict[str, Any]) -> SpecNode | None:
        arguments.pop(next(iter(arguments)))  # self
        try:
            args = {
                name: cls.spec_codecs[name].encode(value)
                if name in cls.spec_codecs
                else _encode_plain(value)
                for name, value in arguments.items()
            }
        except TypeError, ValueError:
            return None
        node: SpecNode = {"type": cls.spec_type}
        if args:
            node["args"] = args
        return node

    @classmethod
    def _from_spec_args(cls, args: Mapping[str, Any]) -> Self:
        """Build an instance from a leaf node's (JSON) ``args``."""
        kwargs = {
            name: cls.spec_codecs[name].decode(value) if name in cls.spec_codecs else value
            for name, value in args.items()
        }
        return cls(**kwargs)

    def to_spec(self) -> SpecNode:
        """Return this object's spec node: a fresh JSON-compatible dict.

        Raises:
            NotSerializableError: The object, or one of its parts, has no data form.

        """
        if self._spec is None:
            raise NotSerializableError(
                f"{type(self).__name__} has no spec: it was built from a custom function, or "
                "from a subclass or argument that has no data form"
            )
        result: SpecNode = json.loads(json.dumps(self._spec))
        return result

    def __eq__(self, other: object) -> bool:
        """Equal when both have specs and the specs are equal; else identity."""
        if not isinstance(other, SpecSerializable):
            return NotImplemented
        if self._spec is None or other._spec is None:
            return self is other
        if self._spec_registry is not other._spec_registry:
            return False
        return _canonical(self._spec) == _canonical(other._spec)

    def __hash__(self) -> int:
        """Hash of the spec, or identity when there is none."""
        if self._spec is None:
            return object.__hash__(self)
        return hash(_canonical(self._spec))


def combine(op: str, *operands: SpecSerializable) -> SpecNode | None:
    """Return ``{op: [...]}`` over the operands' nodes, flattening nested nodes of the same op.

    None if any operand has no spec.
    """
    items: list[SpecNode] = []
    for operand in operands:
        node = operand._spec  # noqa: SLF001 -- same family of classes
        if node is None:
            return None
        items.extend(node.get(op, [node]))
    return {op: items}


def render(node: SpecNode, registry: Mapping[str, type[Any]]) -> str:
    """Render a node as a Python-like expression (used by ``__repr__``)."""
    if "type" in node:
        name = registry[node["type"]].__name__
        args = ", ".join(f"{k}={v!r}" for k, v in node.get("args", {}).items())
        return f"{name}({args})"
    if "not" in node:
        return f"~{render(node['not'], registry)}"
    (op, items), *_ = node.items()
    sep = {"and": " & ", "or": " | ", "seq": " + "}[op]
    return "(" + sep.join(render(item, registry) for item in items) + ")"


def wrap_repr(obj: SpecSerializable, registry: Mapping[str, type[Any]]) -> str:
    """``__repr__`` body: the rendered spec, or the default object repr when there is none."""
    if obj._spec is None:  # noqa: SLF001 -- same family of classes
        return object.__repr__(obj)
    return render(obj._spec, registry)  # noqa: SLF001
