"""The rule sets and filters that name a folder of an account, by its path segments: which ones
(a folder's delete is refused while any does) and rewriting them once the folder is renamed.
Database only: ``mailroom.mail.service`` calls it, and ``filters.service`` imports that.

A rule set names a folder by its own ``folder``; a filter by the ``folder`` of a ``move_to`` or a
``trash`` action anywhere in its spec (in a ``seq`` at any depth: the spec has no other path).
Either counts when it names the folder or one under it. Segments compare exactly, but for a
leading ``INBOX``, in any case, as IMAP names it. Specification:
``docs/requirements/filters.md``, *Folders renamed or deleted*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import select

from mailroom.models import Filter, RuleSet

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sqlalchemy.orm import Session

_MOVES: Final = frozenset({"move_to", "trash"})
"""The actions whose ``folder`` is a path (``trash``'s may be null: the account's Trash)."""


@dataclass(frozen=True, slots=True)
class RuleSetRef:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class Rewritten:
    """What a rename changed, ids in order."""

    rule_sets: list[int]
    """Every rule set changed: its own folder, or a filter of it."""
    filters: list[int]
    """The filters whose spec changed."""


def _same(index: int, segment: str, other: str) -> bool:
    return segment == other or (index == 0 and segment.upper() == other.upper() == "INBOX")


def under(path: Sequence[str], folder: Sequence[str]) -> bool:
    """Whether ``path`` is ``folder`` or a folder under it."""
    if not folder or len(path) < len(folder):
        return False
    return all(_same(i, a, b) for i, (a, b) in enumerate(zip(path, folder, strict=False)))


def _moved(path: Sequence[str], old: Sequence[str], new: Sequence[str]) -> list[str] | None:
    """``path`` once ``old`` is ``new``; None when it is not under ``old``."""
    if not under(path, old):
        return None
    return [*new, *path[len(old) :]]


def _path(node: dict[str, Any]) -> list[str] | None:
    """The folder of a ``move_to`` or ``trash`` action, None for any other node."""
    if node.get("type") not in _MOVES:
        return None
    args = node.get("args")
    folder = args.get("folder") if isinstance(args, dict) else None
    if isinstance(folder, list) and all(isinstance(segment, str) for segment in folder):
        return folder
    return None


def spec_folders(node: object) -> Iterator[list[str]]:
    """Every folder path the spec ``node`` moves mail to."""
    if isinstance(node, dict):
        path = _path(node)
        if path is not None:
            yield path
        for value in node.values():
            yield from spec_folders(value)
    elif isinstance(node, list):
        for item in node:
            yield from spec_folders(item)


def rewritten_spec(node: object, old: Sequence[str], new: Sequence[str]) -> object:
    """The spec ``node`` with each ``move_to`` or ``trash`` folder at ``old`` or under it moved
    under ``new``; a new structure, ``node`` left as it is. A ``trash`` to the account's Trash
    (folder null) stays."""
    if isinstance(node, dict):
        rewritten = {key: rewritten_spec(value, old, new) for key, value in node.items()}
        path = _path(node)
        moved = None if path is None else _moved(path, old, new)
        if moved is not None:
            rewritten["args"] = {**node["args"], "folder": moved}
        return rewritten
    if isinstance(node, list):
        return [rewritten_spec(item, old, new) for item in node]
    return node


def _rule_sets(db: Session, user_id: int, account_id: str, *, lock: bool) -> list[RuleSet]:
    statement = (
        select(RuleSet)
        .where(RuleSet.user_id == user_id, RuleSet.account_id == account_id)
        .order_by(RuleSet.id)
    )
    if lock:
        statement = statement.with_for_update()
    return list(db.scalars(statement))


def _filters(db: Session, rule_sets: Sequence[RuleSet], *, lock: bool) -> list[Filter]:
    if not rule_sets:
        return []
    statement = (
        select(Filter)
        .where(Filter.rule_set_id.in_([rule_set.id for rule_set in rule_sets]))
        .order_by(Filter.id)
    )
    if lock:
        statement = statement.with_for_update()
    return list(db.scalars(statement))


def naming_rule_sets(
    db: Session, user_id: int, account_id: str, segments: Sequence[str]
) -> list[RuleSetRef]:
    """The user's rule sets of the account that name the folder ``segments`` or one under it,
    by their own folder or by a filter's action, enabled or not, in id order."""
    rule_sets = _rule_sets(db, user_id, account_id, lock=False)
    naming = {rule_set.id for rule_set in rule_sets if under(rule_set.folder, segments)}
    for row in _filters(db, rule_sets, lock=False):
        if row.rule_set_id not in naming and any(
            under(path, segments) for path in spec_folders(row.spec)
        ):
            naming.add(row.rule_set_id)
    return [RuleSetRef(r.id, r.name) for r in rule_sets if r.id in naming]


def rewrite_folder(
    db: Session, user_id: int, account_id: str, old: Sequence[str], new: Sequence[str]
) -> Rewritten:
    """Points the user's rule sets of the account, and their filters' actions, from the folder
    ``old`` (and every folder under it) to ``new``, in one transaction, committed. Enabled rule
    sets too: they keep acting on the same folder, under its new name. The account's rule sets
    are locked first, then their filters, as every change of a filter locks its rule set."""
    rule_sets = _rule_sets(db, user_id, account_id, lock=True)
    filters = _filters(db, rule_sets, lock=True)
    changed: set[int] = set()
    for rule_set in rule_sets:
        moved = _moved(rule_set.folder, old, new)
        if moved is not None:
            rule_set.folder = moved
            changed.add(rule_set.id)
    rewritten_filters: list[int] = []
    for row in filters:
        spec = cast("dict[str, Any]", rewritten_spec(row.spec, old, new))
        if spec != row.spec:
            row.spec = spec
            rewritten_filters.append(row.id)
            changed.add(row.rule_set_id)
    db.commit()
    return Rewritten(
        rule_sets=[rule_set.id for rule_set in rule_sets if rule_set.id in changed],
        filters=rewritten_filters,
    )
