"""The user's rule sets and filters, their dry runs and runs, through ``mailroom.filters.service``.

Safety (``docs/requirements/mcp.md``): a rule set created here starts disabled; the write tools
change only a disabled rule set ("disable it first"); turning anything on (``rule_set_enable``,
``run_now``, ``account_run_filters``) is a separate, destructive call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

from pydantic import Field

from mailroom.filters import service
from mailroom.filters.service import (
    MAX_FILTERS,
    FilterFields,
    FilterPatch,
    IntervalMinutes,
    Marker,
    Name,
    RuleSetFields,
    RuleSetPatch,
    Segment,
    Spec,
    Trigger,
)
from mailroom.filters.spec import offered_schema
from mailroom.mcp.envelope import wrapped_result
from mailroom.mcp.tool import READ, Arguments, Hints, JsonObject, Tool
from mailroom.mcp.tool import AccountId as BareAccountId
from mailroom.mcp.tools.accounts import NoArguments

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.filters.service import RunOut
    from mailroom.mcp.tool import ToolCall

RuleSetId = Annotated[int, Field(ge=1, description="A rule set id, from rule_sets_list.")]
FilterId = Annotated[int, Field(ge=1, description="A filter id, from rule_set_get.")]
Folder = Annotated[
    list[Segment],
    Field(
        min_length=1,
        max_length=32,
        description="The folder's path segments, as folders_list gives them.",
    ),
]
Accounts = Annotated[
    BareAccountId | Literal["all"],
    Field(
        description=(
            "An account id, from accounts_list, or all: a rule set run on INBOX of every account "
            "whose Run filters is on."
        )
    ),
]
RunAccount = Annotated[
    BareAccountId | None,
    Field(
        description=(
            "For a rule set for all accounts (account_id all): the account id to run it on, from "
            "accounts_list."
        )
    ),
]
FilterSpec = Annotated[
    Spec, Field(description="A filter spec: criterion and action (filter_schema).")
]

_INTERVAL = "Minutes between runs: for trigger scheduled only, and required there."
_MARKER = (
    "Limit the rule set to marked messages, whose mark the run clears once its filters have "
    "acted: none (every message), star, or to_sort (the To sort mark, only where accounts_list "
    "gives the account keywords true). For 'only marked' pick to_sort where the account has "
    "keywords, else star."
)
_TRIGGER = (
    "new_mail: on each new message in the folder; scheduled: every interval_minutes; "
    "manual: only on run_now."
)

RUN_FILTERS_OFF_NOTE: Final = (
    "Run filters is off for this account, so the rule set will not run until it is turned on "
    "(account_run_filters)."
)
RUN_FILTERS_OFF_EVERYWHERE_NOTE: Final = (
    "Run filters is off for every account, so the rule set will not run until it is turned on "
    "for one (account_run_filters)."
)


class RuleSetArguments(Arguments):
    rule_set_id: RuleSetId


class RuleSetRunArguments(RuleSetArguments):
    account_id: RunAccount = None


class RuleSetsListArguments(Arguments):
    account_id: Accounts


class FilterArguments(Arguments):
    filter_id: FilterId


class RuleSetCreateArguments(Arguments):
    account_id: Accounts
    name: Name
    folder: Folder
    trigger: Trigger = Field(description=_TRIGGER)
    interval_minutes: IntervalMinutes | None = Field(default=None, description=_INTERVAL)
    marker: Marker = Field(default="none", description=_MARKER)


class RuleSetUpdateArguments(Arguments):
    """Only the fields given change. Leaving ``scheduled`` drops the interval."""

    rule_set_id: RuleSetId
    name: Name | None = None
    folder: Folder | None = None
    trigger: Trigger | None = Field(default=None, description=_TRIGGER)
    interval_minutes: IntervalMinutes | None = Field(default=None, description=_INTERVAL)
    marker: Marker | None = Field(default=None, description=_MARKER)


class FilterAddArguments(Arguments):
    rule_set_id: RuleSetId
    name: Name
    enabled: bool = True
    spec: FilterSpec


class FilterUpdateArguments(Arguments):
    """Only the fields given change."""

    filter_id: FilterId
    name: Name | None = None
    enabled: bool | None = None
    spec: FilterSpec | None = None


class FiltersReorderArguments(Arguments):
    rule_set_id: RuleSetId
    filter_ids: list[FilterId] = Field(
        max_length=1000,
        description="Every filter of the rule set, each once, in the new order.",
    )


class RunsListArguments(Arguments):
    rule_set_id: RuleSetId
    limit: int = Field(default=20, ge=1, le=100, description="How many runs, newest first.")


def _run(run: RunOut) -> JsonObject:
    """A run as a tool answers it: the mail's senders and subjects in the envelope."""
    out = run.model_dump(mode="json")
    out["result"] = wrapped_result(out["result"])
    return out


async def _filter_schema(_args: NoArguments, _call: ToolCall) -> JsonObject:
    return offered_schema()


async def _rule_sets_list(args: RuleSetsListArguments, call: ToolCall) -> JsonObject:
    listed = await call.db(
        lambda db: service.rule_sets(call.ctx, db, call.user_id, args.account_id)
    )
    return {"rule_sets": [rule_set.model_dump(mode="json") for rule_set in listed]}


async def _rule_set_get(args: RuleSetArguments, call: ToolCall) -> JsonObject:
    detail = await call.db(lambda db: service.rule_set_detail(db, call.user_id, args.rule_set_id))
    return detail.model_dump(mode="json")


async def _runs_list(args: RunsListArguments, call: ToolCall) -> JsonObject:
    listed = await call.db(lambda db: service.runs(db, call.user_id, args.rule_set_id, args.limit))
    return {"runs": [_run(run) for run in listed]}


async def _rule_set_create(args: RuleSetCreateArguments, call: ToolCall) -> JsonObject:
    fields = RuleSetFields(
        **args.model_dump(exclude={"account_id"}),
        enabled=False,
    )
    detail = await call.db(
        lambda db: service.add_rule_set(
            call.ctx, db, call.user_id, args.account_id, fields, via="mcp"
        )
    )
    return detail.model_dump(mode="json")


async def _rule_set_update(args: RuleSetUpdateArguments, call: ToolCall) -> JsonObject:
    patch = RuleSetPatch.model_validate(
        args.model_dump(exclude_unset=True, exclude={"rule_set_id"})
    )
    changed = await call.db(
        lambda db: service.change_rule_set(
            call.ctx, db, call.user_id, args.rule_set_id, patch, disabled_only=True, via="mcp"
        )
    )
    return changed.model_dump(mode="json")


async def _rule_set_disable(args: RuleSetArguments, call: ToolCall) -> JsonObject:
    changed = await call.db(
        lambda db: service.change_rule_set(
            call.ctx, db, call.user_id, args.rule_set_id, RuleSetPatch(enabled=False), via="mcp"
        )
    )
    return changed.model_dump(mode="json")


async def _rule_set_delete(args: RuleSetArguments, call: ToolCall) -> JsonObject:
    await call.db(
        lambda db: service.remove_rule_set(
            db, call.user_id, args.rule_set_id, disabled_only=True, via="mcp"
        )
    )
    return {"deleted": True, "rule_set_id": args.rule_set_id}


async def _filter_add(args: FilterAddArguments, call: ToolCall) -> JsonObject:
    fields = FilterFields(name=args.name, enabled=args.enabled, spec=args.spec)
    added = await call.db(
        lambda db: service.add_filter(
            call.ctx, db, call.user_id, args.rule_set_id, fields, disabled_only=True, via="mcp"
        )
    )
    return added.model_dump(mode="json")


async def _filter_update(args: FilterUpdateArguments, call: ToolCall) -> JsonObject:
    patch = FilterPatch.model_validate(args.model_dump(exclude_unset=True, exclude={"filter_id"}))
    changed = await call.db(
        lambda db: service.change_filter(
            call.ctx, db, call.user_id, args.filter_id, patch, disabled_only=True, via="mcp"
        )
    )
    return changed.model_dump(mode="json")


async def _filter_delete(args: FilterArguments, call: ToolCall) -> JsonObject:
    await call.db(
        lambda db: service.remove_filter(
            db, call.user_id, args.filter_id, disabled_only=True, via="mcp"
        )
    )
    return {"deleted": True, "filter_id": args.filter_id}


async def _filters_reorder(args: FiltersReorderArguments, call: ToolCall) -> JsonObject:
    ordered = await call.db(
        lambda db: service.reorder_filters(
            db, call.user_id, args.rule_set_id, args.filter_ids, disabled_only=True, via="mcp"
        )
    )
    return {"filters": [row.model_dump(mode="json") for row in ordered]}


async def _dry_run(args: RuleSetRunArguments, call: ToolCall) -> JsonObject:
    run = await call.db(
        lambda db: service.dry_run(
            call.ctx, db, call.user_id, args.rule_set_id, args.account_id, via="mcp"
        )
    )
    return _run(run)


async def _rule_set_enable(args: RuleSetArguments, call: ToolCall) -> JsonObject:
    def enable(db: Session) -> JsonObject:
        changed = service.change_rule_set(
            call.ctx, db, call.user_id, args.rule_set_id, RuleSetPatch(enabled=True), via="mcp"
        )
        out: JsonObject = {"rule_set": changed.model_dump(mode="json")}
        if isinstance(changed.account_id, int):
            if not service.run_filters(call.ctx, db, call.user_id, changed.account_id):
                out["note"] = RUN_FILTERS_OFF_NOTE
        elif not service.running_accounts(db, call.user_id):
            out["note"] = RUN_FILTERS_OFF_EVERYWHERE_NOTE
        return out

    return await call.db(enable)


async def _run_now(args: RuleSetRunArguments, call: ToolCall) -> JsonObject:
    queued = await call.db(
        lambda db: service.run_now(
            call.ctx, db, call.user_id, args.rule_set_id, args.account_id, via="mcp"
        )
    )
    if isinstance(queued, list):
        return {"runs": [_run(run) for run in queued]}
    return _run(queued)


_DISABLED_ONLY = " Only on a disabled rule set: rule_set_disable it first."
_PUBLIC_PROVIDERS = (
    " A public mail provider's whole domain (gmail.com, outlook.com…) is refused in from_domain, "
    "from_contains and from_matches: filter its sender with from_is."
)

FILTER_TOOLS: Final[tuple[Tool[Any], ...]] = (
    Tool(
        name="filter_schema",
        title="Filter spec schema",
        description=(
            "The JSON Schema (draft 2020-12) of a filter spec: a criterion (from, subject, "
            "flags, dates…, combined with and/or/not) and an action (move_to, star, trash…)."
        ),
        group="filters_read",
        arguments=NoArguments,
        hints=READ,
        run=_filter_schema,
    ),
    Tool(
        name="rule_sets_list",
        title="List rule sets",
        description=(
            "An account's rule sets, oldest first, without their filters; account_id all lists "
            "the rule sets for all accounts. A rule set runs its enabled filters, in order, on "
            "one folder, on its trigger."
        ),
        group="filters_read",
        arguments=RuleSetsListArguments,
        hints=READ,
        run=_rule_sets_list,
    ),
    Tool(
        name="rule_set_get",
        title="Get a rule set",
        description=(
            "A rule set (its folder, trigger, marker and whether it is enabled) with all its "
            "filters, in order, disabled ones too. Each filter's matches_30d counts the messages "
            "it acted on in real runs over the last 30 days, kept across edits; updated_at is "
            "its last edit."
        ),
        group="filters_read",
        arguments=RuleSetArguments,
        hints=READ,
        run=_rule_set_get,
    ),
    Tool(
        name="runs_list",
        title="List runs",
        description=(
            "A rule set's runs, newest first, dry runs too: the account each ran on, when, what "
            "each filter matched, and any error. Senders and subjects come from mail, in "
            "untrusted_email tags."
        ),
        group="filters_read",
        arguments=RunsListArguments,
        hints=READ,
        run=_runs_list,
    ),
    Tool(
        name="rule_set_create",
        title="Create a rule set",
        description=(
            "Create a rule set on an account folder. It always starts disabled: add filters, "
            "dry_run it, then rule_set_enable it. With account_id all it runs on INBOX of every "
            "account whose Run filters is on, each on its own: a move to a named folder fails "
            "on an account without that folder (no folder is created); trash and junk without "
            "a folder go to each account's own. It takes no to_sort marker."
        ),
        group="filters_write",
        arguments=RuleSetCreateArguments,
        hints=Hints(),
        run=_rule_set_create,
    ),
    Tool(
        name="rule_set_update",
        title="Update a rule set",
        description="Change a rule set's name, folder, trigger or marker." + _DISABLED_ONLY,
        group="filters_write",
        arguments=RuleSetUpdateArguments,
        hints=Hints(),
        run=_rule_set_update,
    ),
    Tool(
        name="rule_set_disable",
        title="Disable a rule set",
        description="Disable a rule set: it stops running, and can then be edited.",
        group="filters_write",
        arguments=RuleSetArguments,
        hints=Hints(idempotent=True),
        run=_rule_set_disable,
    ),
    Tool(
        name="rule_set_delete",
        title="Delete a rule set",
        description="Delete a rule set with its filters and run history." + _DISABLED_ONLY,
        group="filters_write",
        arguments=RuleSetArguments,
        hints=Hints(destructive=True),
        run=_rule_set_delete,
    ),
    Tool(
        name="filter_add",
        title="Add a filter",
        description=(
            f"Add a filter after the rule set's last one ({MAX_FILTERS} at most). The spec is "
            "checked as filter_schema describes it." + _PUBLIC_PROVIDERS + _DISABLED_ONLY
        ),
        group="filters_write",
        arguments=FilterAddArguments,
        hints=Hints(),
        run=_filter_add,
    ),
    Tool(
        name="filter_update",
        title="Update a filter",
        description="Change a filter's name, enabled or spec." + _PUBLIC_PROVIDERS + _DISABLED_ONLY,
        group="filters_write",
        arguments=FilterUpdateArguments,
        hints=Hints(),
        run=_filter_update,
    ),
    Tool(
        name="filter_delete",
        title="Delete a filter",
        description="Delete a filter; the rest close up." + _DISABLED_ONLY,
        group="filters_write",
        arguments=FilterArguments,
        hints=Hints(destructive=True),
        run=_filter_delete,
    ),
    Tool(
        name="filters_reorder",
        title="Reorder filters",
        description=(
            "Put a rule set's filters in a new order, listing every one of them once."
            + _DISABLED_ONLY
        ),
        group="filters_write",
        arguments=FiltersReorderArguments,
        hints=Hints(),
        run=_filters_reorder,
    ),
    Tool(
        name="dry_run",
        title="Dry run a rule set",
        description=(
            "Run a rule set's enabled filters without acting, on its folder's newest messages, "
            "and report what each would match; enabled or not, nothing changes. A rule set for "
            "all accounts needs account_id. One dry run at a time per user. Senders and subjects "
            "come from mail, in untrusted_email tags."
        ),
        group="filters_run",
        arguments=RuleSetRunArguments,
        hints=Hints(read_only=True, open_world=True),
        run=_dry_run,
    ),
    Tool(
        name="rule_set_enable",
        title="Enable a rule set",
        description=(
            "Enable a rule set: from now on it acts on the account's mail on its trigger, while "
            "the account's Run filters is on (the result says when it is off). Dry run it and "
            "confirm with the user first."
        ),
        group="filters_run",
        arguments=RuleSetArguments,
        hints=Hints(destructive=True, idempotent=True),
        run=_rule_set_enable,
    ),
    Tool(
        name="run_now",
        title="Run a rule set now",
        description=(
            "Queue a real run of an enabled rule set: its filters act on the folder's mail. A "
            "rule set for all accounts runs on account_id, or without it on every account whose "
            "Run filters is on, a run each, and answers {runs: [...]}; any other answers its "
            "run. Refused while the account's Run filters is off, for "
            "a disabled rule set, and while a run of it is already waiting. Confirm with the "
            "user first."
        ),
        group="filters_run",
        arguments=RuleSetRunArguments,
        hints=Hints(destructive=True),
        run=_run_now,
    ),
)
