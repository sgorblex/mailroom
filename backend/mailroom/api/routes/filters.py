"""Each user's filters: the folders and Run filters switch of their accounts, rule sets per account
folder, their ordered filters, dry runs, Run now and the run history. Only the signed-in user's
own: another user's id is a 404.

Specification: ``docs/requirements/filters.md``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query
from imap_thingy.spec import json_schema
from pydantic import BaseModel, Field

from mailroom.api.deps import Context, CurrentUser, DbSession
from mailroom.filters import service
from mailroom.filters.service import (
    FilterFields,
    FilterOut,
    FilterPatch,
    RuleSetDetail,
    RuleSetFields,
    RuleSetOut,
    RuleSetPatch,
    RunOut,
)

filters_router = APIRouter()

AccountId = Annotated[str, Path(min_length=1, max_length=64)]
RowId = Annotated[int, Path(ge=1)]


class Folder(BaseModel):
    path: list[str]
    """Segments, split on the server's delimiter: ``["INBOX"]``, ``["Archive", "2026"]``."""


class FilterSettings(BaseModel):
    run_filters: bool


class FilterOrder(BaseModel):
    filter_ids: list[int] = Field(max_length=1000)
    """Every filter of the rule set, each once, in the new order."""


@filters_router.get("/filters/schema")
def filter_schema() -> dict[str, Any]:
    """imap-thingy's JSON Schema (draft 2020-12) of a filter spec."""
    return json_schema()


@filters_router.get("/accounts/{account_id}/folders")
def folders(account_id: AccountId, ctx: Context, user: CurrentUser) -> list[Folder]:
    return [Folder(path=path) for path in service.account_folders(ctx, user.id, account_id)]


@filters_router.get("/accounts/{account_id}/filter-settings")
def filter_settings(
    account_id: AccountId, ctx: Context, db: DbSession, user: CurrentUser
) -> FilterSettings:
    return FilterSettings(run_filters=service.run_filters(ctx, db, user.id, account_id))


@filters_router.put("/accounts/{account_id}/filter-settings")
def change_filter_settings(
    account_id: AccountId, body: FilterSettings, ctx: Context, db: DbSession, user: CurrentUser
) -> FilterSettings:
    on = service.set_run_filters(ctx, db, user.id, account_id, on=body.run_filters)
    return FilterSettings(run_filters=on)


@filters_router.get("/accounts/{account_id}/rule-sets")
def rule_sets(
    account_id: AccountId, ctx: Context, db: DbSession, user: CurrentUser
) -> list[RuleSetOut]:
    return service.rule_sets(ctx, db, user.id, account_id)


@filters_router.post("/accounts/{account_id}/rule-sets", status_code=201)
def add_rule_set(
    account_id: AccountId, body: RuleSetFields, ctx: Context, db: DbSession, user: CurrentUser
) -> RuleSetDetail:
    return service.add_rule_set(ctx, db, user.id, account_id, body)


@filters_router.get("/rule-sets/{rule_set_id}")
def rule_set_detail(rule_set_id: RowId, db: DbSession, user: CurrentUser) -> RuleSetDetail:
    return service.rule_set_detail(db, user.id, rule_set_id)


@filters_router.patch("/rule-sets/{rule_set_id}")
def change_rule_set(
    rule_set_id: RowId, body: RuleSetPatch, db: DbSession, user: CurrentUser
) -> RuleSetOut:
    return service.change_rule_set(db, user.id, rule_set_id, body)


@filters_router.delete("/rule-sets/{rule_set_id}", status_code=204)
def delete_rule_set(rule_set_id: RowId, db: DbSession, user: CurrentUser) -> None:
    service.remove_rule_set(db, user.id, rule_set_id)


@filters_router.post("/rule-sets/{rule_set_id}/filters", status_code=201)
def add_filter(
    rule_set_id: RowId, body: FilterFields, db: DbSession, user: CurrentUser
) -> FilterOut:
    return service.add_filter(db, user.id, rule_set_id, body)


@filters_router.patch("/filters/{filter_id}")
def change_filter(
    filter_id: RowId, body: FilterPatch, db: DbSession, user: CurrentUser
) -> FilterOut:
    return service.change_filter(db, user.id, filter_id, body)


@filters_router.delete("/filters/{filter_id}", status_code=204)
def delete_filter(filter_id: RowId, db: DbSession, user: CurrentUser) -> None:
    service.remove_filter(db, user.id, filter_id)


@filters_router.put("/rule-sets/{rule_set_id}/order")
def reorder_filters(
    rule_set_id: RowId, body: FilterOrder, db: DbSession, user: CurrentUser
) -> list[FilterOut]:
    return service.reorder_filters(db, user.id, rule_set_id, body.filter_ids)


@filters_router.post("/rule-sets/{rule_set_id}/dry-run")
def dry_run_rule_set(rule_set_id: RowId, ctx: Context, db: DbSession, user: CurrentUser) -> RunOut:
    """Run the enabled filters without acting, on the folder's newest messages, briefly; the run
    joins the history. Mailbox failures answer as a folder listing does, 504 on time out; one
    dry run per user at a time (429 ``dry_run_busy``, not recorded)."""
    return service.dry_run(ctx, db, user.id, rule_set_id)


@filters_router.post("/rule-sets/{rule_set_id}/run", status_code=202)
def run_rule_set_now(rule_set_id: RowId, db: DbSession, user: CurrentUser) -> RunOut:
    """Queue a run for the worker. Refused while the account's Run filters is off, for a
    disabled rule set, and while a run of it is already waiting."""
    return service.run_now(db, user.id, rule_set_id)


@filters_router.get("/rule-sets/{rule_set_id}/runs")
def rule_set_runs(
    rule_set_id: RowId,
    db: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RunOut]:
    """Newest first."""
    return service.runs(db, user.id, rule_set_id, limit)
