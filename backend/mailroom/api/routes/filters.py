"""Each user's filters: rule sets per account folder, their ordered filters, dry runs, Run now and
the run history. Only the signed-in user's own: another user's id is a 404.

Specification: ``docs/requirements/filters.md``.
"""

from __future__ import annotations

import logging
import ssl
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from imap_thingy.spec import json_schema
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mailroom.api.deps import CurrentUser, DbSession
from mailroom.api.refusals import mail_status, refused
from mailroom.api.routes.accounts import Store, Subkey, checked_account
from mailroom.filters.runs import (
    DryRunBusy,
    RunRefused,
    dry_run_result,
    dry_run_slot,
    enabled_filters,
    prune_runs,
    run_filters_on,
    runs_of,
)
from mailroom.filters.spec import InvalidSpecError, normalized_spec
from mailroom.models import Filter, FilterRun, RuleSet, User
from mailroom.worker.tasks import queue_run

if TYPE_CHECKING:
    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.store import AccountStore

logger = logging.getLogger(__name__)

filters_router = APIRouter()

Trigger = Literal["new_mail", "scheduled", "manual"]
Segment = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")]
Name = Annotated[str, Field(min_length=1, max_length=200)]
IntervalMinutes = Annotated[int, Field(ge=1, le=7 * 24 * 60)]
Spec = dict[str, Any]
AccountId = Annotated[str, Path(min_length=1, max_length=64)]
RowId = Annotated[int, Path(ge=1)]

MAX_FILTERS: Final = 100
"""Filters per rule set; the web app's Add filter stops there too."""


class RuleSetFields(BaseModel):
    name: Name
    folder: list[Segment] = Field(min_length=1, max_length=32)
    """Path segments, as ``GET /accounts/{id}/folders`` lists them."""
    trigger: Trigger
    interval_minutes: IntervalMinutes | None = None
    """``scheduled`` only, and required there."""
    starred_only: bool = False
    enabled: bool = False


class RuleSetPatch(BaseModel):
    """Only the fields present change. Leaving ``scheduled`` drops the interval."""

    name: Name | None = None
    folder: list[Segment] | None = Field(default=None, min_length=1, max_length=32)
    trigger: Trigger | None = None
    interval_minutes: IntervalMinutes | None = None
    starred_only: bool | None = None
    enabled: bool | None = None


class FilterOut(BaseModel):
    id: int
    rule_set_id: int
    position: int
    name: str
    enabled: bool
    spec: Spec
    created_at: datetime
    updated_at: datetime


class RuleSetOut(BaseModel):
    id: int
    account_id: str
    folder: list[str]
    name: str
    trigger: Trigger
    interval_minutes: int | None
    starred_only: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class RuleSetDetail(RuleSetOut):
    filters: list[FilterOut]
    """In order, disabled ones too."""


class FilterFields(BaseModel):
    name: Name
    enabled: bool = True
    spec: Spec
    """An imap-thingy filter spec (``GET /filters/schema``); kept as imap-thingy serialises it."""


class FilterPatch(BaseModel):
    name: Name | None = None
    enabled: bool | None = None
    spec: Spec | None = None


class FilterOrder(BaseModel):
    filter_ids: list[int] = Field(max_length=1000)
    """Every filter of the rule set, each once, in the new order."""


class RunOut(BaseModel):
    id: int
    rule_set_id: int
    trigger: Trigger
    dry_run: bool
    created_at: datetime
    started_at: datetime | None
    """None while queued."""
    finished_at: datetime | None
    result: dict[str, Any] | None
    error: str | None


def _rule_set_out(rule_set: RuleSet) -> RuleSetOut:
    return RuleSetOut.model_validate(rule_set, from_attributes=True)


def _filter_out(row: Filter) -> FilterOut:
    return FilterOut.model_validate(row, from_attributes=True)


def _run_out(run: FilterRun) -> RunOut:
    return RunOut.model_validate(run, from_attributes=True)


def _owned_rule_set(db: Session, user: User, rule_set_id: int, *, lock: bool = False) -> RuleSet:
    """The user's rule set, or 404. ``lock``: hold its row until commit (filter order changes)."""
    statement = select(RuleSet).where(RuleSet.id == rule_set_id, RuleSet.user_id == user.id)
    if lock:
        statement = statement.with_for_update()
    rule_set = db.scalar(statement)
    if rule_set is None:
        raise HTTPException(status_code=404, detail="No such rule set")
    return rule_set


def _owned_filter(db: Session, user: User, filter_id: int) -> tuple[Filter, RuleSet]:
    """The user's filter and its rule set, the latter locked; or 404."""
    rule_set_id = db.scalar(
        select(Filter.rule_set_id)
        .join(RuleSet, RuleSet.id == Filter.rule_set_id)
        .where(Filter.id == filter_id, RuleSet.user_id == user.id)
    )
    if rule_set_id is None:
        raise HTTPException(status_code=404, detail="No such filter")
    rule_set = _owned_rule_set(db, user, rule_set_id, lock=True)
    row = db.get(Filter, filter_id)
    if row is None:  # removed meanwhile
        raise HTTPException(status_code=404, detail="No such filter")
    return row, rule_set


def _filters_of(db: Session, rule_set_id: int) -> list[Filter]:
    return list(
        db.scalars(
            select(Filter).where(Filter.rule_set_id == rule_set_id).order_by(Filter.position)
        )
    )


def _check_schedule(trigger: str, interval_minutes: int | None) -> None:
    if (trigger == "scheduled") != (interval_minutes is not None):
        msg = "A scheduled rule set needs interval_minutes; other triggers take none."
        raise refused(400, "invalid_rule_set", msg)


def _spec(spec: Spec) -> Spec:
    try:
        return normalized_spec(spec)
    except InvalidSpecError as exc:
        raise refused(400, "invalid_spec", str(exc)) from None


def _log(event: str, user: User, **extra: object) -> None:
    logger.info(event, extra={"event": event, "user_id": user.id, **extra})


@filters_router.get("/filters/schema")
def filter_schema() -> dict[str, Any]:
    """imap-thingy's JSON Schema (draft 2020-12) of a filter spec."""
    return json_schema()


@filters_router.get("/accounts/{account_id}/rule-sets")
def rule_sets(
    account_id: AccountId, store: Store, db: DbSession, user: CurrentUser
) -> list[RuleSetOut]:
    checked_account(store, account_id)
    rows = db.scalars(
        select(RuleSet)
        .where(RuleSet.user_id == user.id, RuleSet.account_id == account_id)
        .order_by(RuleSet.id)
    )
    return [_rule_set_out(row) for row in rows]


@filters_router.post("/accounts/{account_id}/rule-sets", status_code=201)
def add_rule_set(
    account_id: AccountId, body: RuleSetFields, store: Store, db: DbSession, user: CurrentUser
) -> RuleSetDetail:
    checked_account(store, account_id)
    _check_schedule(body.trigger, body.interval_minutes)
    rule_set = RuleSet(user_id=user.id, account_id=account_id, **body.model_dump())
    db.add(rule_set)
    db.commit()
    db.refresh(rule_set)
    _log("rule_set_added", user, account_id=account_id, rule_set_id=rule_set.id)
    return RuleSetDetail(**_rule_set_out(rule_set).model_dump(), filters=[])


@filters_router.get("/rule-sets/{rule_set_id}")
def rule_set_detail(rule_set_id: RowId, db: DbSession, user: CurrentUser) -> RuleSetDetail:
    rule_set = _owned_rule_set(db, user, rule_set_id)
    filters = [_filter_out(row) for row in _filters_of(db, rule_set.id)]
    return RuleSetDetail(**_rule_set_out(rule_set).model_dump(), filters=filters)


@filters_router.patch("/rule-sets/{rule_set_id}")
def change_rule_set(
    rule_set_id: RowId, body: RuleSetPatch, db: DbSession, user: CurrentUser
) -> RuleSetOut:
    rule_set = _owned_rule_set(db, user, rule_set_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    for field in ("name", "folder", "trigger", "starred_only", "enabled"):
        if changes.get(field, None) is None:
            changes.pop(field, None)
    trigger = changes.get("trigger", rule_set.trigger)
    if trigger != "scheduled" and "interval_minutes" not in changes:
        changes["interval_minutes"] = None
    _check_schedule(trigger, changes.get("interval_minutes", rule_set.interval_minutes))
    for field, value in changes.items():
        setattr(rule_set, field, value)
    db.commit()
    db.refresh(rule_set)
    _log(
        "rule_set_changed",
        user,
        account_id=rule_set.account_id,
        rule_set_id=rule_set.id,
    )
    return _rule_set_out(rule_set)


@filters_router.delete("/rule-sets/{rule_set_id}", status_code=204)
def delete_rule_set(rule_set_id: RowId, db: DbSession, user: CurrentUser) -> None:
    rule_set = _owned_rule_set(db, user, rule_set_id, lock=True)
    db.delete(rule_set)
    db.commit()
    _log("rule_set_removed", user, account_id=rule_set.account_id, rule_set_id=rule_set_id)


@filters_router.post("/rule-sets/{rule_set_id}/filters", status_code=201)
def add_filter(
    rule_set_id: RowId, body: FilterFields, db: DbSession, user: CurrentUser
) -> FilterOut:
    rule_set = _owned_rule_set(db, user, rule_set_id, lock=True)
    spec = _spec(body.spec)
    count, last = db.execute(
        select(func.count(), func.max(Filter.position)).where(Filter.rule_set_id == rule_set.id)
    ).one()
    if count >= MAX_FILTERS:
        msg = f"A rule set holds {MAX_FILTERS} filters at most."
        raise refused(400, "too_many_filters", msg)
    row = Filter(
        rule_set_id=rule_set.id,
        position=0 if last is None else last + 1,
        name=body.name,
        enabled=body.enabled,
        spec=spec,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _log("filter_added", user, rule_set_id=rule_set.id, filter_id=row.id)
    return _filter_out(row)


@filters_router.patch("/filters/{filter_id}")
def change_filter(
    filter_id: RowId, body: FilterPatch, db: DbSession, user: CurrentUser
) -> FilterOut:
    row, rule_set = _owned_filter(db, user, filter_id)
    if body.name is not None:
        row.name = body.name
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.spec is not None:
        row.spec = _spec(body.spec)
    db.commit()
    db.refresh(row)
    _log("filter_changed", user, rule_set_id=rule_set.id, filter_id=row.id)
    return _filter_out(row)


@filters_router.delete("/filters/{filter_id}", status_code=204)
def delete_filter(filter_id: RowId, db: DbSession, user: CurrentUser) -> None:
    row, rule_set = _owned_filter(db, user, filter_id)
    db.delete(row)
    db.flush()
    for position, rest in enumerate(_filters_of(db, rule_set.id)):
        rest.position = position
    db.commit()
    _log("filter_removed", user, rule_set_id=rule_set.id, filter_id=filter_id)


@filters_router.put("/rule-sets/{rule_set_id}/order")
def reorder_filters(
    rule_set_id: RowId, body: FilterOrder, db: DbSession, user: CurrentUser
) -> list[FilterOut]:
    rule_set = _owned_rule_set(db, user, rule_set_id, lock=True)
    rows = {row.id: row for row in _filters_of(db, rule_set.id)}
    if len(body.filter_ids) != len(rows) or set(body.filter_ids) != rows.keys():
        msg = "List every filter of the rule set, each once."
        raise refused(400, "invalid_order", msg)
    for position, filter_id in enumerate(body.filter_ids):
        rows[filter_id].position = position
    db.commit()
    _log("filters_reordered", user, rule_set_id=rule_set.id)
    return [_filter_out(row) for row in _filters_of(db, rule_set.id)]


def _record_run(db: Session, run: FilterRun) -> None:
    db.add(run)
    db.flush()
    prune_runs(db, run.rule_set_id)
    db.commit()


@filters_router.post("/rule-sets/{rule_set_id}/dry-run")
def dry_run_rule_set(
    rule_set_id: RowId,
    request: Request,
    store: Store,
    subkey: Subkey,
    db: DbSession,
    user: CurrentUser,
) -> RunOut:
    """Run the enabled filters without acting, on the folder's newest messages, briefly; the run
    joins the history. Mailbox failures answer as a folder listing does, 504 on time out; one
    dry run per user at a time (429 ``dry_run_busy``, not recorded)."""
    rule_set = _owned_rule_set(db, user, rule_set_id)
    filters = enabled_filters(db, rule_set.id)
    # Not idle in a transaction while the mailbox answers.
    db.commit()
    try:
        with dry_run_slot(request.app.state.database_engine, user.id):
            return _dry_run(request, store, subkey, db, user, rule_set, filters)
    except DryRunBusy:
        msg = "Another dry run of yours is still going: wait for it to finish."
        raise refused(429, "dry_run_busy", msg) from None


def _dry_run(
    request: Request,
    store: AccountStore,
    subkey: SecretsSubkey | None,
    db: Session,
    user: User,
    rule_set: RuleSet,
    filters: list[Filter],
) -> RunOut:
    run = FilterRun(
        rule_set_id=rule_set.id,
        trigger="manual",
        dry_run=True,
        started_at=datetime.now(UTC),
    )
    ssl_context: ssl.SSLContext = request.app.state.mail_ssl_context
    try:
        run.result = dry_run_result(store, subkey, ssl_context, rule_set, filters)
    except RunRefused as refusal:
        run.error = refusal.error
        run.finished_at = datetime.now(UTC)
        _record_run(db, run)
        _log("dry_run", user, rule_set_id=rule_set.id, run_id=run.id, outcome=refusal.code)
        status = _DRY_RUN_STATUS.get(refusal.code) or mail_status(refusal.code)
        raise refused(status, refusal.code, refusal.message) from None
    run.finished_at = datetime.now(UTC)
    _record_run(db, run)
    _log("dry_run", user, rule_set_id=rule_set.id, run_id=run.id, outcome="ok")
    return _run_out(run)


_DRY_RUN_STATUS: Final = {
    "account_not_found": 404,
    "invalid_spec": 400,
    "dry_run_timeout": 504,
    "match_timeout": 400,
    "run_failed": 500,
}
"""The status of a dry run's refusal; any other code is a mailbox failure's (``mail_status``)."""


@filters_router.post("/rule-sets/{rule_set_id}/run", status_code=202)
def run_rule_set_now(rule_set_id: RowId, db: DbSession, user: CurrentUser) -> RunOut:
    """Queue a run for the worker. Refused while the account's Run filters is off, for a
    disabled rule set, and while a run of it is already waiting."""
    rule_set = _owned_rule_set(db, user, rule_set_id, lock=True)
    if not run_filters_on(db, user.id, rule_set.account_id):
        msg = "Run filters is off for this account: turn it on to run its rule sets."
        raise refused(400, "run_filters_off", msg)
    if not rule_set.enabled:
        raise refused(400, "rule_set_disabled", "Enable the rule set to run it.")
    run_id = queue_run(db, rule_set, "manual")
    if run_id is None:
        raise refused(409, "run_pending", "A run of this rule set is already waiting.")
    db.commit()
    run = db.get_one(FilterRun, run_id)
    db.refresh(run)
    _log("run_requested", user, rule_set_id=rule_set.id, run_id=run.id)
    return _run_out(run)


@filters_router.get("/rule-sets/{rule_set_id}/runs")
def rule_set_runs(
    rule_set_id: RowId,
    db: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RunOut]:
    """Newest first."""
    rule_set = _owned_rule_set(db, user, rule_set_id)
    return [_run_out(run) for run in runs_of(db, rule_set.id, limit)]
