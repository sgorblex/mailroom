"""Each user's filters: the folders and Run filters switch of their accounts, rule sets per account
folder, their ordered filters, dry runs, Run now and the run history. Only the signed-in user's
own: another user's id is a 404.

Specification: ``docs/requirements/filters.md``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field

from mailroom.api.deps import AccountId, Context, CurrentUser, DbSession
from mailroom.api.routes.mail._shared import MatchRequest
from mailroom.filters import service
from mailroom.filters.drafts import SearchDraft, search_draft
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
from mailroom.filters.spec import offered_schema
from mailroom_core.config import MAX_SAFE_INTEGER

filters_router = APIRouter()

RowId = Annotated[int, Path(ge=1)]
Accounts = Annotated[
    Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)] | Literal["all"],
    Path(description="An account id, or all: the rule sets run on INBOX of every account."),
]
RunAccount = Annotated[
    int | None,
    Query(ge=1, le=MAX_SAFE_INTEGER, description="For a rule set for all accounts: which one."),
]


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
    """The JSON Schema (draft 2020-12) of a filter spec a user writes (``offered_schema``)."""
    return offered_schema()


@filters_router.post("/filters/draft-from-search")
def draft_from_search(body: MatchRequest, _user: CurrentUser) -> SearchDraft:
    """A filter the editor starts from, made from a search's body as ``POST /mail/search`` takes
    it; its account, folder and ``subfolders`` are the search's scope, not criteria."""
    return search_draft(body.query())


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
    account_id: Accounts, ctx: Context, db: DbSession, user: CurrentUser
) -> list[RuleSetOut]:
    """The account's rule sets; ``all``: the rule sets for all accounts."""
    return service.rule_sets(ctx, db, user.id, account_id)


@filters_router.post("/accounts/{account_id}/rule-sets", status_code=201)
def add_rule_set(
    account_id: Accounts, body: RuleSetFields, ctx: Context, db: DbSession, user: CurrentUser
) -> RuleSetDetail:
    """``all``: a rule set on INBOX of every account whose Run filters is on."""
    return service.add_rule_set(ctx, db, user.id, account_id, body)


@filters_router.get("/rule-sets/{rule_set_id}")
def rule_set_detail(
    rule_set_id: RowId, ctx: Context, db: DbSession, user: CurrentUser
) -> RuleSetDetail:
    return service.rule_set_detail(ctx, db, user.id, rule_set_id)


@filters_router.patch("/rule-sets/{rule_set_id}")
def change_rule_set(
    rule_set_id: RowId, body: RuleSetPatch, ctx: Context, db: DbSession, user: CurrentUser
) -> RuleSetOut:
    return service.change_rule_set(ctx, db, user.id, rule_set_id, body)


@filters_router.delete("/rule-sets/{rule_set_id}", status_code=204)
def delete_rule_set(rule_set_id: RowId, ctx: Context, db: DbSession, user: CurrentUser) -> None:
    service.remove_rule_set(ctx, db, user.id, rule_set_id)


@filters_router.post("/rule-sets/{rule_set_id}/filters", status_code=201)
def add_filter(
    rule_set_id: RowId, body: FilterFields, ctx: Context, db: DbSession, user: CurrentUser
) -> FilterOut:
    return service.add_filter(ctx, db, user.id, rule_set_id, body)


@filters_router.patch("/filters/{filter_id}")
def change_filter(
    filter_id: RowId, body: FilterPatch, ctx: Context, db: DbSession, user: CurrentUser
) -> FilterOut:
    return service.change_filter(ctx, db, user.id, filter_id, body)


@filters_router.delete("/filters/{filter_id}", status_code=204)
def delete_filter(filter_id: RowId, ctx: Context, db: DbSession, user: CurrentUser) -> None:
    service.remove_filter(ctx, db, user.id, filter_id)


@filters_router.put("/rule-sets/{rule_set_id}/order")
def reorder_filters(
    rule_set_id: RowId, body: FilterOrder, ctx: Context, db: DbSession, user: CurrentUser
) -> list[FilterOut]:
    return service.reorder_filters(ctx, db, user.id, rule_set_id, body.filter_ids)


@filters_router.post("/rule-sets/{rule_set_id}/dry-run")
def dry_run_rule_set(
    rule_set_id: RowId,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
    account_id: RunAccount = None,
) -> RunOut:
    """Run the enabled filters without acting, on the folder's newest messages, briefly; the run
    joins the history. A rule set for all accounts needs ``account_id``. Mailbox failures answer
    as a folder listing does, 504 on time out; one dry run per user at a time (429
    ``dry_run_busy``, not recorded)."""
    return service.dry_run(ctx, db, user.id, rule_set_id, account_id)


@filters_router.post("/rule-sets/{rule_set_id}/run", status_code=202)
def run_rule_set_now(
    rule_set_id: RowId,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
    account_id: RunAccount = None,
) -> RunOut | list[RunOut]:
    """Queue a run for the worker: the run queued; for a rule set for all accounts the list of
    the runs queued, one per account without ``account_id``. Refused while the account's Run
    filters is off, for a disabled rule set, and while a run of it is already waiting."""
    return service.run_now(ctx, db, user.id, rule_set_id, account_id)


@filters_router.get("/rule-sets/{rule_set_id}/runs")
def rule_set_runs(
    rule_set_id: RowId,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RunOut]:
    """Newest first."""
    return service.runs(ctx, db, user.id, rule_set_id, limit)
