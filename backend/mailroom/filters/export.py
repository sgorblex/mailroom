"""A user's rule sets as a document to keep, or to read elsewhere: no id of this instance in it.

Specification: ``docs/requirements/filters.md``, *Export*.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel
from sqlalchemy import select

from mailroom.filters.service import ALL_ACCOUNTS, Marker, Spec, Trigger
from mailroom.filters.transfers import ACTION, moving_beyond
from mailroom.models import Filter, RuleSet

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.orm import Session

    from mailroom.context import AppContext

FORMAT: Final = "mailroom-rule-sets"
VERSION: Final = 1


class ExportedAccount(BaseModel):
    """An account as an export names it."""

    login: str
    name: str | None


class ExportedFilter(BaseModel):
    name: str
    enabled: bool
    spec: Spec
    """As stored, but for a ``move_to_account``'s ``account``: an ``ExportedAccount``, or null
    once it is removed."""


class ExportedRuleSet(BaseModel):
    name: str
    account: ExportedAccount | Literal["all"] | None
    """``all`` for every account whose Run filters is on; null for an account not there any
    more."""
    folder: list[str]
    trigger: Trigger
    interval_minutes: int | None
    marker: Marker
    enabled: bool
    filters: list[ExportedFilter]
    """In order, disabled ones too."""


class RuleSetsExport(BaseModel):
    format: Literal["mailroom-rule-sets"] = FORMAT
    version: Literal[1] = VERSION
    """Raised when a reader of the last one would misread the next."""
    exported_at: datetime
    rule_sets: list[ExportedRuleSet]
    """Oldest first."""


def rule_sets_export(ctx: AppContext, db: Session, user_id: int) -> RuleSetsExport:
    """Every rule set of the user's within the caller's reach, as ``rule_sets`` lists them: never
    the hidden ones."""
    accounts = {
        account.id: ExportedAccount(login=account.login, name=account.name)
        for account in ctx.accounts(user_id).settings.account
    }
    rows = db.scalars(
        select(RuleSet)
        .where(
            RuleSet.user_id == user_id,
            RuleSet.kind == "user",
            ctx.reach.covers(RuleSet.account_id),
        )
        .order_by(RuleSet.id)
    ).all()
    beyond = moving_beyond(db, ctx.reach, [row.id for row in rows])
    kept = [row for row in rows if row.id not in beyond]
    filters: dict[int, list[ExportedFilter]] = {row.id: [] for row in kept}
    for row in db.scalars(
        select(Filter)
        .where(Filter.rule_set_id.in_(filters))
        .order_by(Filter.rule_set_id, Filter.position)
    ):
        exported = ExportedFilter(
            name=row.name, enabled=row.enabled, spec=_naming_accounts(row.spec, accounts)
        )
        filters[row.rule_set_id].append(exported)
    return RuleSetsExport(
        exported_at=datetime.now(UTC),
        rule_sets=[
            ExportedRuleSet.model_validate(
                {
                    "name": row.name,
                    "account": (
                        ALL_ACCOUNTS if row.account_id is None else accounts.get(row.account_id)
                    ),
                    "folder": row.folder,
                    "trigger": row.trigger,
                    "interval_minutes": row.interval_minutes,
                    "marker": row.marker,
                    "enabled": row.enabled,
                    "filters": filters[row.id],
                }
            )
            for row in kept
        ],
    )


def _naming_accounts(spec: Spec, accounts: Mapping[int, ExportedAccount]) -> Spec:
    """``spec`` with the accounts its action moves to named as an export names them."""
    return {**spec, "action": _named(spec.get("action"), accounts)}


def _named(node: object, accounts: Mapping[int, ExportedAccount]) -> object:
    if isinstance(node, list):
        return [_named(item, accounts) for item in node]
    if not isinstance(node, dict):
        return node
    args = node.get("args")
    if node.get("type") == ACTION and isinstance(args, dict):
        target = accounts.get(args.get("account", 0))
        return {
            **node,
            "args": {**args, "account": None if target is None else target.model_dump()},
        }
    return {key: _named(value, accounts) for key, value in node.items()}
