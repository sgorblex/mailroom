"""Changing what an account keeps the Handled mark as, and the rule sets that changes the meaning
of: those limited to it, or with a filter that matches or sets it.

Specification: ``docs/requirements/filters.md``, *Handled*.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel
from sqlalchemy import or_, select

from mailroom.accounts.handled import handled_mark, store_handled_mark
from mailroom.accounts.service import Via, require_account
from mailroom.errors import Refusal, mail_refusal
from mailroom.filters.service import ALL_ACCOUNTS, Accounts, check_mark, has_node
from mailroom.mail import service as mail_service
from mailroom.models import Filter, RuleSet
from mailroom_core.mail import HandledMark, MailError, parse_handled

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.context import AppContext

logger = logging.getLogger(__name__)

_NODES: Final = frozenset({"is_handled", "is_not_handled", "mark_handled", "unmark_handled"})


class HandledRuleSet(BaseModel):
    id: int
    account_id: Accounts
    name: str
    enabled: bool


class HandledSetting(BaseModel):
    mark: str
    """``star``, ``keyword:<name>`` or ``label:<name>``."""
    rule_sets: list[HandledRuleSet]
    """The account's rule sets, and those for all accounts, whose meaning follows it, enabled or
    not, oldest first."""


def handled_setting(ctx: AppContext, db: Session, user_id: int, account_id: int) -> HandledSetting:
    """What the account keeps Handled as, and the rule sets it gives its meaning."""
    require_account(ctx, user_id, account_id)
    return HandledSetting(
        mark=str(handled_mark(db, user_id, account_id)),
        rule_sets=_rule_sets(db, user_id, account_id),
    )


def change_handled(
    ctx: AppContext,
    db: Session,
    user_id: int,
    account_id: int,
    text: str,
    confirmed: list[int],
    *,
    via: Via = "web",
) -> HandledSetting:
    """Keep the account's Handled mark as ``text``: ``invalid_argument`` when it is none;
    ``handled_unsupported`` for a keyword on a server that keeps none, or a label off Gmail;
    ``handled_rule_sets`` unless ``confirmed`` names every rule set whose meaning changes
    (``handled_setting``'s). A label there already under another case is taken as it is spelt, as
    Gmail has no two names that differ in case only. Marks already made are left as they are."""
    require_account(ctx, user_id, account_id)
    mark = _parsed(text)
    db.commit()  # not idle in a transaction while the mailbox answers
    check_mark(ctx, user_id, account_id, mark, via)
    if mark.kind == "label":
        listed = mail_service.folders(ctx, user_id, account_id, counts=False, every=True, via=via)
        mark = _as_spelt(mark, [folder.path for folder in listed])
    if mark != handled_mark(db, user_id, account_id):
        missing = {one.id for one in _rule_sets(db, user_id, account_id)} - set(confirmed)
        if missing:
            msg = "Rule sets change their meaning with it: check them again."
            raise Refusal("conflict", "handled_rule_sets", msg)
        store_handled_mark(db, user_id, account_id, mark)
    db.commit()
    logger.info(
        "account_handled_changed",
        extra={
            "event": "account_handled_changed",
            "user_id": user_id,
            "via": via,
            "account_id": account_id,
            "kind": mark.kind,
        },
    )
    return handled_setting(ctx, db, user_id, account_id)


def _parsed(text: str) -> HandledMark:
    try:
        return parse_handled(text)
    except MailError as error:
        raise mail_refusal(error.code, error.message) from None


def _as_spelt(mark: HandledMark, paths: list[str]) -> HandledMark:
    if mark.name in paths:
        return mark
    same = (path for path in paths if path.casefold() == mark.name.casefold())
    path = next(same, None)
    return mark if path is None else _parsed(f"label:{path}")


def _rule_sets(db: Session, user_id: int, account_id: int) -> list[HandledRuleSet]:
    rows = db.scalars(
        select(RuleSet)
        .where(
            RuleSet.user_id == user_id,
            RuleSet.kind == "user",
            or_(RuleSet.account_id == account_id, RuleSet.account_id.is_(None)),
        )
        .order_by(RuleSet.id)
    ).all()
    specs = db.execute(
        select(Filter.rule_set_id, Filter.spec).where(
            Filter.rule_set_id.in_([row.id for row in rows])
        )
    )
    using = {rule_set_id for rule_set_id, spec in specs if has_node(spec, _NODES)}
    return [
        HandledRuleSet(
            id=row.id,
            account_id=ALL_ACCOUNTS if row.account_id is None else row.account_id,
            name=row.name,
            enabled=row.enabled,
        )
        for row in rows
        if row.marker == "handled" or row.id in using
    ]
