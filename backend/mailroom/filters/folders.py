"""The rule sets and filters that name a folder of an account, by its path segments: which ones
(a folder's delete is refused while any does) and rewriting them once the folder is renamed.
Database only: ``mailroom.mail.service.folder_writes`` calls it, and ``filters.service`` imports
the mail service.

A rule set names a folder by its own ``folder``; a filter by the ``folder`` of a ``move_to`` or a
``trash`` action anywhere in its spec (in a ``seq`` at any depth), and a folder of another account
by a ``move_to_account`` naming that account, in a rule set of any of the user's accounts.
Either counts when it names the folder or one under it. Segments compare exactly, but for a
leading ``INBOX``, in any case, as IMAP names it. A rule set for all accounts names the folder on
each account: it holds a delete back, but a rename on one account does not rewrite it, and is
reported instead. Specification:
``docs/requirements/filters.md``, *Folders renamed or deleted*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import or_, select, true

from mailroom.models import Filter, RuleSet

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sqlalchemy.orm import Session

_MOVES: Final = frozenset({"move_to", "trash"})
"""The actions whose ``folder`` is a path in the rule set's account (``trash``'s may be null: the
account's Trash)."""
_TO_ACCOUNT: Final = "move_to_account"
"""The action whose ``folder`` is a path in the account its ``account`` names."""


@dataclass(frozen=True, slots=True)
class RuleSetRef:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class Rewritten:
    """What a rename changed, ids in order."""

    rule_sets: list[int]
    """Every rule set changed: its own folder, or a filter of it (one moving to the account from
    another account's rule set too)."""
    filters: list[int]
    """The filters whose spec changed."""
    all_accounts: list[int]
    """The rule sets for all accounts that name the folder, left as they are: their other
    accounts may still have it."""


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


def _path(node: dict[str, Any], target: int | None = None) -> list[str] | None:
    """The folder of a ``move_to`` or ``trash`` action, or with ``target`` of a
    ``move_to_account`` to that account; None for any other node."""
    args = node.get("args")
    if not isinstance(args, dict):
        return None
    if target is None and node.get("type") not in _MOVES:
        return None
    if target is not None and (node.get("type") != _TO_ACCOUNT or args.get("account") != target):
        return None
    folder = args.get("folder")
    if isinstance(folder, list) and all(isinstance(segment, str) for segment in folder):
        return folder
    return None


def spec_folders(node: object, target: int | None = None) -> Iterator[list[str]]:
    """Every folder path the spec ``node`` moves mail to in its rule set's account, or with
    ``target`` in that other account."""
    if isinstance(node, dict):
        path = _path(node, target)
        if path is not None:
            yield path
        for value in node.values():
            yield from spec_folders(value, target)
    elif isinstance(node, list):
        for item in node:
            yield from spec_folders(item, target)


def rewritten_spec(
    node: object, old: Sequence[str], new: Sequence[str], target: int | None = None
) -> object:
    """The spec ``node`` with each ``move_to`` or ``trash`` folder (with ``target``, each
    ``move_to_account`` folder of that account) at ``old`` or under it moved under ``new``; a new
    structure, ``node`` left as it is. A ``trash`` to the account's Trash (folder null) stays."""
    if isinstance(node, dict):
        rewritten = {key: rewritten_spec(value, old, new, target) for key, value in node.items()}
        path = _path(node, target)
        moved = None if path is None else _moved(path, old, new)
        if moved is not None:
            rewritten["args"] = {**node["args"], "folder": moved}
        return rewritten
    if isinstance(node, list):
        return [rewritten_spec(item, old, new, target) for item in node]
    return node


def _rule_sets(
    db: Session,
    user_id: int,
    account_id: int | None,
    *,
    lock: bool,
    all_accounts: bool = False,
    every: bool = False,
) -> list[RuleSet]:
    """The account's rule sets (None: the all-accounts ones), with the all-accounts ones too when
    ``all_accounts``; ``every``: all the user's."""
    of_account = (
        RuleSet.account_id.is_(None) if account_id is None else RuleSet.account_id == account_id
    )
    if all_accounts:
        of_account = or_(of_account, RuleSet.account_id.is_(None))
    if every:
        of_account = true()
    statement = (
        select(RuleSet)
        .where(RuleSet.user_id == user_id, of_account, RuleSet.kind == "user")
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


def _naming(
    db: Session, rule_sets: Sequence[RuleSet], segments: Sequence[str], account_id: int
) -> list[RuleSet]:
    """Those of ``rule_sets`` that name the folder ``segments`` of ``account_id`` or one under
    it, by their own folder or by a filter's action, in their order: a rule set of another
    account only by a filter's ``move_to_account`` to it."""
    own = [r for r in rule_sets if r.account_id in (None, account_id)]
    naming = {rule_set.id for rule_set in own if under(rule_set.folder, segments)}
    owned = {rule_set.id for rule_set in own}
    for row in _filters(db, rule_sets, lock=False):
        paths = [*spec_folders(row.spec, account_id)]
        if row.rule_set_id in owned:
            paths += spec_folders(row.spec)
        if row.rule_set_id not in naming and any(under(path, segments) for path in paths):
            naming.add(row.rule_set_id)
    return [rule_set for rule_set in rule_sets if rule_set.id in naming]


def naming_rule_sets(
    db: Session, user_id: int, account_id: int, segments: Sequence[str]
) -> list[RuleSetRef]:
    """The user's rule sets of the account, and for all accounts, that name the folder
    ``segments`` or one under it, by their own folder or by a filter's action, and those of any
    account moving to it (``move_to_account``), enabled or not, in id order."""
    rule_sets = _rule_sets(db, user_id, account_id, lock=False, every=True)
    return [RuleSetRef(r.id, r.name) for r in _naming(db, rule_sets, segments, account_id)]


def rewrite_folder(
    db: Session, user_id: int, account_id: int, old: Sequence[str], new: Sequence[str]
) -> Rewritten:
    """Points the user's rule sets of the account, and their filters' actions, from the folder
    ``old`` (and every folder under it) to ``new``, in one transaction, committed; and the
    ``move_to_account`` actions to it of every rule set of the user's. Enabled rule sets too:
    they keep acting on the same folder, under its new name. The rule sets are locked first,
    then their filters, as every change of a filter locks its rule set. The rule sets for all
    accounts keep their own folder and ``move_to`` and ``trash`` paths: those naming ``old`` are
    reported."""
    shared = _naming(db, _rule_sets(db, user_id, None, lock=False), old, account_id)
    every = _rule_sets(db, user_id, account_id, lock=True, every=True)
    rule_sets = [rule_set for rule_set in every if rule_set.account_id == account_id]
    own = {rule_set.id for rule_set in rule_sets}
    filters = _filters(db, every, lock=True)
    changed: set[int] = set()
    for rule_set in rule_sets:
        moved = _moved(rule_set.folder, old, new)
        if moved is not None:
            rule_set.folder = moved
            changed.add(rule_set.id)
    rewritten_filters: list[int] = []
    for row in filters:
        spec = cast("dict[str, Any]", rewritten_spec(row.spec, old, new, account_id))
        if row.rule_set_id in own:
            spec = cast("dict[str, Any]", rewritten_spec(spec, old, new))
        if spec != row.spec:
            row.spec = spec
            rewritten_filters.append(row.id)
            changed.add(row.rule_set_id)
    db.commit()
    return Rewritten(
        rule_sets=[rule_set.id for rule_set in every if rule_set.id in changed],
        filters=rewritten_filters,
        all_accounts=[rule_set.id for rule_set in shared],
    )
