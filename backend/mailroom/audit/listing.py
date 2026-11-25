"""A user's audit trail, newest first, a page at a time: what the web's *Activity* page and the
MCP tool ``audit_list`` show. The connected app's and the rule set's names are read now, so a
record of one since removed keeps its id only."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel
from sqlalchemy import or_, select

from mailroom.errors import Refusal
from mailroom.filters.transfers import moving_beyond
from mailroom.models import AuditRecord, RuleSet
from mailroom.oauth.models import OAuthClient

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.audit import Actor
    from mailroom.reach import Reach

PAGE_SIZE: Final = 50
_CURSOR: Final = re.compile(r"[0-9]{1,18}")
"""A record's id: what ``next_cursor`` holds."""
MAX_PAGE_SIZE: Final = 100
OTHER_ACCOUNT: Final = ("destination_account", "source_account")
"""The fields of a record's detail that name another account than its own."""
OTHER_RULE_SETS: Final = frozenset({"rule_sets", "filters", "all_accounts_rule_sets"})
"""What a folder's rename changed, in rule sets of other accounts too."""
OF_RULE_SET: Final = frozenset({"filter", "filter_id"})


class ListedRecord(BaseModel):
    id: int
    at: datetime
    actor: str
    """``user``, ``agent`` or ``filter``."""
    app: str | None
    """The connected app's name (its client id when it gave none), for an agent's record."""
    client_id: str | None
    rule_set: str | None
    """The rule set's name, for a filter's record while the rule set exists."""
    rule_set_id: int | None
    run_id: int | None
    action: str
    account_id: int
    result: str
    detail: dict[str, Any]


class RecordPage(BaseModel):
    records: list[ListedRecord]
    next_cursor: str | None


def records(
    db: Session,
    user_id: int,
    *,
    reach: Reach,
    cursor: str | None = None,
    limit: int = PAGE_SIZE,
    account: int | None = None,
    action: str | None = None,
    actor: Actor | None = None,
    result: str | None = None,
) -> RecordPage:
    """The user's records within ``reach`` (a move between accounts only when both are; when it
    leaves accounts out, a rule set for all accounts, or moving mail to one beyond, unnamed),
    newest first, matching every filter given; ``cursor`` is a page's ``next_cursor``. ``result``
    ``failed`` matches every result but ``ok``."""
    query = (
        select(AuditRecord, OAuthClient.client_name, RuleSet.name)
        .outerjoin(OAuthClient, OAuthClient.client_id == AuditRecord.client_id)
        .outerjoin(
            RuleSet,
            (RuleSet.id == AuditRecord.rule_set_id)
            & (RuleSet.user_id == user_id)
            & reach.covers(RuleSet.account_id),
        )
        .where(AuditRecord.user_id == user_id, reach.covers(AuditRecord.account_id))
        .order_by(AuditRecord.id.desc())
        .limit(limit + 1)
    )
    if reach.accounts is not None:
        within = [str(account_id) for account_id in reach.accounts]
        for key in OTHER_ACCOUNT:
            other = AuditRecord.detail[key].astext
            query = query.where(or_(other.is_(None), other.in_(within)))
    if cursor is not None:
        query = query.where(AuditRecord.id < _after(cursor))
    if account is not None:
        query = query.where(AuditRecord.account_id == account)
    if action is not None:
        query = query.where(AuditRecord.action == action)
    if actor is not None:
        query = query.where(AuditRecord.actor == actor)
    if result == "failed":
        query = query.where(AuditRecord.result != "ok")
    elif result is not None:
        query = query.where(AuditRecord.result == result)
    rows = db.execute(query).all()
    beyond = moving_beyond(
        db, reach, [row[0].rule_set_id for row in rows if row[0].rule_set_id is not None]
    )
    page = [
        _listed(record, app, None if record.rule_set_id in beyond else rule_set, reach)
        for record, app, rule_set in rows[:limit]
    ]
    more = len(rows) > limit
    return RecordPage(records=page, next_cursor=str(page[-1].id) if more and page else None)


def _after(cursor: str) -> int:
    if _CURSOR.fullmatch(cursor) is None:
        raise Refusal("invalid", "invalid_cursor", "That cursor is not one this list gave.")
    return int(cursor)


def _listed(
    record: AuditRecord, app: str | None, rule_set: str | None, reach: Reach
) -> ListedRecord:
    detail = record.detail
    rule_set_id, run_id = record.rule_set_id, record.run_id
    if reach.narrowed:
        hidden = OTHER_RULE_SETS
        if rule_set is None:  # none, removed, or one beyond the reach
            rule_set_id = run_id = None
            hidden |= OF_RULE_SET
        detail = {key: value for key, value in detail.items() if key not in hidden}
    return ListedRecord(
        id=record.id,
        at=record.at,
        actor=record.actor,
        app=app or record.client_id,
        client_id=record.client_id,
        rule_set=rule_set,
        rule_set_id=rule_set_id,
        run_id=run_id,
        action=record.action,
        account_id=record.account_id,
        result=record.result,
        detail=detail,
    )
