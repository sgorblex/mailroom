"""A user's filters as every front end handles them: the folders and Run filters switch of their
accounts, rule sets per account folder, their ordered filters, dry runs, Run now and the run
history. Only the user's own: another user's id is ``not_found``. Raises
``mailroom.errors.Refusal``.

Specification: ``docs/requirements/filters.md``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from mailroom.accounts import app_data
from mailroom.accounts.mail import MailFailure, list_folders
from mailroom.accounts.service import Via, imap_account, require_account
from mailroom.errors import Refusal, mail_refusal
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
from mailroom.models import Filter, FilterRun, RuleSet
from mailroom.worker.tasks import queue_run

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.context import AppContext
    from mailroom.errors import RefusalKind

logger = logging.getLogger(__name__)

Trigger = Literal["new_mail", "scheduled", "manual"]
Segment = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")]
Name = Annotated[str, Field(min_length=1, max_length=200)]
IntervalMinutes = Annotated[int, Field(ge=1, le=7 * 24 * 60)]
Spec = dict[str, Any]

MAX_FILTERS: Final = 100
"""Filters per rule set; the web app's Add filter stops there too."""


class RuleSetFields(BaseModel):
    name: Name
    folder: list[Segment] = Field(min_length=1, max_length=32)
    """Path segments, as the REST folder picker lists them (``account_folders``, imap-thingy's)
    and MCP's ``folders_list`` gives them as ``segments`` (mailroom-core's LIST through
    ``mailroom.mail.service``)."""
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
    """An imap-thingy filter spec (``imap_thingy.spec.json_schema``); kept as imap-thingy
    serialises it."""


class FilterPatch(BaseModel):
    name: Name | None = None
    enabled: bool | None = None
    spec: Spec | None = None


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


def _detail(db: Session, rule_set: RuleSet) -> RuleSetDetail:
    filters = [_filter_out(row) for row in _filters_of(db, rule_set.id)]
    return RuleSetDetail(**_rule_set_out(rule_set).model_dump(), filters=filters)


def _log(event: str, user_id: int, via: Via, **extra: object) -> None:
    logger.info(event, extra={"event": event, "user_id": user_id, "via": via, **extra})


def owned_rule_set(db: Session, user_id: int, rule_set_id: int, *, lock: bool = False) -> RuleSet:
    """The user's rule set, or ``not_found``. ``lock``: hold its row until commit (any change to
    it or its filters)."""
    statement = select(RuleSet).where(RuleSet.id == rule_set_id, RuleSet.user_id == user_id)
    if lock:
        statement = statement.with_for_update()
    rule_set = db.scalar(statement)
    if rule_set is None:
        raise Refusal("not_found", "rule_set_not_found", "No such rule set")
    return rule_set


def owned_filter(db: Session, user_id: int, filter_id: int) -> tuple[Filter, RuleSet]:
    """The user's filter and its rule set, the latter locked; or ``not_found``."""
    rule_set_id = db.scalar(
        select(Filter.rule_set_id)
        .join(RuleSet, RuleSet.id == Filter.rule_set_id)
        .where(Filter.id == filter_id, RuleSet.user_id == user_id)
    )
    if rule_set_id is None:
        raise _filter_not_found()
    rule_set = owned_rule_set(db, user_id, rule_set_id, lock=True)
    row = db.get(Filter, filter_id)
    if row is None:  # removed meanwhile
        raise _filter_not_found()
    return row, rule_set


def _filter_not_found() -> Refusal:
    return Refusal("not_found", "filter_not_found", "No such filter")


def refuse_enabled(rule_set: RuleSet) -> None:
    """Refuse a change to an enabled rule set: the way to change one is disable, edit, dry run,
    enable. For front ends that must not change what may be running (the web app may)."""
    if rule_set.enabled:
        raise Refusal("conflict", "rule_set_enabled", "Disable the rule set first")


def _owned_for_change(
    db: Session, user_id: int, rule_set_id: int, *, disabled_only: bool
) -> RuleSet:
    rule_set = owned_rule_set(db, user_id, rule_set_id, lock=True)
    if disabled_only:
        refuse_enabled(rule_set)
    return rule_set


def _filters_of(db: Session, rule_set_id: int) -> list[Filter]:
    return list(
        db.scalars(
            select(Filter).where(Filter.rule_set_id == rule_set_id).order_by(Filter.position)
        )
    )


def check_schedule(trigger: str, interval_minutes: int | None) -> None:
    """An ``interval_minutes`` exactly when ``scheduled``."""
    if (trigger == "scheduled") != (interval_minutes is not None):
        msg = "A scheduled rule set needs interval_minutes; other triggers take none."
        raise Refusal("invalid", "invalid_rule_set", msg)


def checked_spec(spec: Spec) -> Spec:
    """``spec`` as imap-thingy serialises it, or ``invalid_spec``."""
    try:
        return normalized_spec(spec)
    except InvalidSpecError as exc:
        raise Refusal("invalid", "invalid_spec", str(exc)) from None


def account_folders(
    ctx: AppContext, user_id: int, account_id: str, *, seconds: float | None = None
) -> list[list[str]]:
    """The account's folders, each as its path segments (``["Archive", "2026"]``), within
    ``seconds`` when given (``mailroom.accounts.mail.list_folders``)."""
    try:
        return list_folders(imap_account(ctx, user_id, account_id), seconds=seconds)
    except MailFailure as failure:
        raise mail_refusal(failure.code, failure.message) from None


def run_filters(ctx: AppContext, db: Session, user_id: int, account_id: str) -> bool:
    """The account's Run filters switch (off until turned on)."""
    require_account(ctx.store(user_id), account_id)
    return app_data.run_filters_by_account(db, user_id).get(account_id, False)


def set_run_filters(
    ctx: AppContext,
    db: Session,
    user_id: int,
    account_id: str,
    *,
    on: bool,
    via: Via = "web",
) -> bool:
    """Turn the account's Run filters switch ``on`` or off; the new state."""
    require_account(ctx.store(user_id), account_id)
    app_data.set_run_filters(db, user_id, account_id, run_filters=on)
    _log("run_filters_changed", user_id, via, account_id=account_id, run_filters=on)
    return on


def rule_sets(ctx: AppContext, db: Session, user_id: int, account_id: str) -> list[RuleSetOut]:
    """The account's rule sets, oldest first, without their filters."""
    require_account(ctx.store(user_id), account_id)
    rows = db.scalars(
        select(RuleSet)
        .where(RuleSet.user_id == user_id, RuleSet.account_id == account_id)
        .order_by(RuleSet.id)
    )
    return [_rule_set_out(row) for row in rows]


def add_rule_set(
    ctx: AppContext,
    db: Session,
    user_id: int,
    account_id: str,
    fields: RuleSetFields,
    *,
    via: Via = "web",
) -> RuleSetDetail:
    require_account(ctx.store(user_id), account_id)
    check_schedule(fields.trigger, fields.interval_minutes)
    rule_set = RuleSet(user_id=user_id, account_id=account_id, **fields.model_dump())
    db.add(rule_set)
    db.commit()
    db.refresh(rule_set)
    _log("rule_set_added", user_id, via, account_id=account_id, rule_set_id=rule_set.id)
    return RuleSetDetail(**_rule_set_out(rule_set).model_dump(), filters=[])


def rule_set_detail(db: Session, user_id: int, rule_set_id: int) -> RuleSetDetail:
    """The rule set with its filters, in order."""
    return _detail(db, owned_rule_set(db, user_id, rule_set_id))


def change_rule_set(
    db: Session,
    user_id: int,
    rule_set_id: int,
    patch: RuleSetPatch,
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> RuleSetOut:
    """Change the fields ``patch`` sets (a None one stays, but for ``interval_minutes``).
    ``disabled_only``: refuse an enabled rule set (``refuse_enabled``)."""
    rule_set = _owned_for_change(db, user_id, rule_set_id, disabled_only=disabled_only)
    changes = patch.model_dump(exclude_unset=True)
    for field in ("name", "folder", "trigger", "starred_only", "enabled"):
        if changes.get(field, None) is None:
            changes.pop(field, None)
    trigger = changes.get("trigger", rule_set.trigger)
    if trigger != "scheduled" and "interval_minutes" not in changes:
        changes["interval_minutes"] = None
    check_schedule(trigger, changes.get("interval_minutes", rule_set.interval_minutes))
    for field, value in changes.items():
        setattr(rule_set, field, value)
    db.commit()
    db.refresh(rule_set)
    _log(
        "rule_set_changed",
        user_id,
        via,
        account_id=rule_set.account_id,
        rule_set_id=rule_set.id,
    )
    return _rule_set_out(rule_set)


def remove_rule_set(
    db: Session,
    user_id: int,
    rule_set_id: int,
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> None:
    """Remove the rule set with its filters and runs."""
    rule_set = _owned_for_change(db, user_id, rule_set_id, disabled_only=disabled_only)
    db.delete(rule_set)
    db.commit()
    _log("rule_set_removed", user_id, via, account_id=rule_set.account_id, rule_set_id=rule_set_id)


def add_filter(
    db: Session,
    user_id: int,
    rule_set_id: int,
    fields: FilterFields,
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> FilterOut:
    """Add a filter after the rule set's last one."""
    rule_set = _owned_for_change(db, user_id, rule_set_id, disabled_only=disabled_only)
    spec = checked_spec(fields.spec)
    count, last = db.execute(
        select(func.count(), func.max(Filter.position)).where(Filter.rule_set_id == rule_set.id)
    ).one()
    if count >= MAX_FILTERS:
        msg = f"A rule set holds {MAX_FILTERS} filters at most."
        raise Refusal("invalid", "too_many_filters", msg)
    row = Filter(
        rule_set_id=rule_set.id,
        position=0 if last is None else last + 1,
        name=fields.name,
        enabled=fields.enabled,
        spec=spec,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _log("filter_added", user_id, via, rule_set_id=rule_set.id, filter_id=row.id)
    return _filter_out(row)


def change_filter(
    db: Session,
    user_id: int,
    filter_id: int,
    patch: FilterPatch,
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> FilterOut:
    """Change the fields ``patch`` sets (a None one stays)."""
    row, rule_set = owned_filter(db, user_id, filter_id)
    if disabled_only:
        refuse_enabled(rule_set)
    if patch.name is not None:
        row.name = patch.name
    if patch.enabled is not None:
        row.enabled = patch.enabled
    if patch.spec is not None:
        row.spec = checked_spec(patch.spec)
    db.commit()
    db.refresh(row)
    _log("filter_changed", user_id, via, rule_set_id=rule_set.id, filter_id=row.id)
    return _filter_out(row)


def remove_filter(
    db: Session,
    user_id: int,
    filter_id: int,
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> None:
    """Remove the filter; the rest close up."""
    row, rule_set = owned_filter(db, user_id, filter_id)
    if disabled_only:
        refuse_enabled(rule_set)
    db.delete(row)
    db.flush()
    for position, rest in enumerate(_filters_of(db, rule_set.id)):
        rest.position = position
    db.commit()
    _log("filter_removed", user_id, via, rule_set_id=rule_set.id, filter_id=filter_id)


def reorder_filters(
    db: Session,
    user_id: int,
    rule_set_id: int,
    filter_ids: list[int],
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> list[FilterOut]:
    """Put the rule set's filters in the order of ``filter_ids``: every one of them, each once."""
    rule_set = _owned_for_change(db, user_id, rule_set_id, disabled_only=disabled_only)
    rows = {row.id: row for row in _filters_of(db, rule_set.id)}
    if len(filter_ids) != len(rows) or set(filter_ids) != rows.keys():
        msg = "List every filter of the rule set, each once."
        raise Refusal("invalid", "invalid_order", msg)
    for position, filter_id in enumerate(filter_ids):
        rows[filter_id].position = position
    db.commit()
    _log("filters_reordered", user_id, via, rule_set_id=rule_set.id)
    return [_filter_out(row) for row in _filters_of(db, rule_set.id)]


_DRY_RUN_KIND: Final[dict[str, RefusalKind]] = {
    "account_not_found": "not_found",
    "invalid_spec": "invalid",
    "match_timeout": "invalid",
    "dry_run_timeout": "timeout",
    "run_failed": "failed",
}
"""The kind of a dry run's refusal; any other code is a mailbox failure's (``mail_refusal``)."""


def _dry_run_refusal(refusal: RunRefused) -> Refusal:
    kind = _DRY_RUN_KIND.get(refusal.code)
    if kind is None:
        return mail_refusal(refusal.code, refusal.message)
    # A dry run's missing account (it went after the rule set was found) keeps its code.
    return Refusal(kind, refusal.code, refusal.message, coded=kind == "not_found")


def dry_run(
    ctx: AppContext, db: Session, user_id: int, rule_set_id: int, *, via: Via = "web"
) -> RunOut:
    """Run the enabled filters without acting, on the folder's newest messages, briefly
    (``mailroom.filters.runs.dry_run_result``); the run joins the history, refused or not. One dry
    run per user at a time: ``busy`` (``dry_run_busy``, not recorded) while another holds the
    slot."""
    rule_set = owned_rule_set(db, user_id, rule_set_id)
    filters = enabled_filters(db, rule_set.id)
    # Not idle in a transaction while the mailbox answers.
    db.commit()
    try:
        with dry_run_slot(ctx.engine, user_id):
            return _dry_run(ctx, db, user_id, rule_set, filters, via)
    except DryRunBusy:
        msg = "Another dry run of yours is still going: wait for it to finish."
        raise Refusal("busy", "dry_run_busy", msg) from None


def _dry_run(
    ctx: AppContext,
    db: Session,
    user_id: int,
    rule_set: RuleSet,
    filters: list[Filter],
    via: Via,
) -> RunOut:
    # Saved only once it has finished, so it is created when it starts, not when it is saved.
    started = datetime.now(UTC)
    run = FilterRun(
        rule_set_id=rule_set.id,
        trigger="manual",
        dry_run=True,
        created_at=started,
        started_at=started,
    )
    try:
        run.result = dry_run_result(
            ctx.store(user_id), ctx.subkey, ctx.ssl_context, rule_set, filters
        )
    except RunRefused as refusal:
        run.error = refusal.error
        run.finished_at = datetime.now(UTC)
        _record_run(db, run)
        _log("dry_run", user_id, via, rule_set_id=rule_set.id, run_id=run.id, outcome=refusal.code)
        raise _dry_run_refusal(refusal) from None
    run.finished_at = datetime.now(UTC)
    _record_run(db, run)
    _log("dry_run", user_id, via, rule_set_id=rule_set.id, run_id=run.id, outcome="ok")
    return _run_out(run)


def _record_run(db: Session, run: FilterRun) -> None:
    db.add(run)
    db.flush()
    prune_runs(db, run.rule_set_id)
    db.commit()


def run_now(db: Session, user_id: int, rule_set_id: int, *, via: Via = "web") -> RunOut:
    """Queue a run for the worker. Refused while the account's Run filters is off, for a
    disabled rule set, and while a run of it is already waiting."""
    rule_set = owned_rule_set(db, user_id, rule_set_id, lock=True)
    if not run_filters_on(db, user_id, rule_set.account_id):
        msg = "Run filters is off for this account: turn it on to run its rule sets."
        raise Refusal("invalid", "run_filters_off", msg)
    if not rule_set.enabled:
        raise Refusal("invalid", "rule_set_disabled", "Enable the rule set to run it.")
    run_id = queue_run(db, rule_set, "manual")
    if run_id is None:
        raise Refusal("conflict", "run_pending", "A run of this rule set is already waiting.")
    db.commit()
    run = db.get_one(FilterRun, run_id)
    db.refresh(run)
    _log("run_requested", user_id, via, rule_set_id=rule_set.id, run_id=run.id)
    return _run_out(run)


def runs(db: Session, user_id: int, rule_set_id: int, limit: int) -> list[RunOut]:
    """The rule set's newest ``limit`` runs, newest first."""
    rule_set = owned_rule_set(db, user_id, rule_set_id)
    return [_run_out(run) for run in runs_of(db, rule_set.id, limit)]
