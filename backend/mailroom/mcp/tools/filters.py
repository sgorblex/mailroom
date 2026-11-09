"""The user's rule sets and filters, their dry runs and runs, through ``mailroom.filters.service``.

Safety (``docs/requirements/mcp.md``): a rule set created here starts disabled; the write tools
change only a disabled rule set ("disable it first"); turning anything on (``rule_set_enable``,
``run_now``, ``account_run_filters``) is a separate, destructive call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Final

from imap_thingy.spec import json_schema
from pydantic import Field

from mailroom.filters import service
from mailroom.filters.service import (
    MAX_FILTERS,
    FilterFields,
    FilterPatch,
    IntervalMinutes,
    Name,
    RuleSetFields,
    RuleSetPatch,
    Segment,
    Spec,
    Trigger,
)
from mailroom.mcp.envelope import wrapped_result
from mailroom.mcp.tool import READ, Arguments, Hints, JsonObject, Tool
from mailroom.mcp.tools.accounts import AccountArguments, AccountId, NoArguments

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
FilterSpec = Annotated[
    Spec, Field(description="An imap-thingy filter spec: criterion and action (filter_schema).")
]

_INTERVAL = "Minutes between runs: for trigger scheduled only, and required there."
_TRIGGER = (
    "new_mail: on each new message in the folder; scheduled: every interval_minutes; "
    "manual: only on run_now."
)

RUN_FILTERS_OFF_NOTE: Final = (
    "Run filters is off for this account, so the rule set will not run until it is turned on "
    "(account_run_filters)."
)


class RuleSetArguments(Arguments):
    rule_set_id: RuleSetId


class FilterArguments(Arguments):
    filter_id: FilterId


class RuleSetCreateArguments(Arguments):
    account_id: AccountId
    name: Name
    folder: Folder
    trigger: Trigger = Field(description=_TRIGGER)
    interval_minutes: IntervalMinutes | None = Field(default=None, description=_INTERVAL)
    starred_only: bool = Field(
        default=False,
        description="Only starred messages, which the run unstars once its filters have acted.",
    )


class RuleSetUpdateArguments(Arguments):
    """Only the fields given change. Leaving ``scheduled`` drops the interval."""

    rule_set_id: RuleSetId
    name: Name | None = None
    folder: Folder | None = None
    trigger: Trigger | None = Field(default=None, description=_TRIGGER)
    interval_minutes: IntervalMinutes | None = Field(default=None, description=_INTERVAL)
    starred_only: bool | None = None


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
    return json_schema()


async def _rule_sets_list(args: AccountArguments, call: ToolCall) -> JsonObject:
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
            db, call.user_id, args.rule_set_id, patch, disabled_only=True, via="mcp"
        )
    )
    return changed.model_dump(mode="json")


async def _rule_set_disable(args: RuleSetArguments, call: ToolCall) -> JsonObject:
    changed = await call.db(
        lambda db: service.change_rule_set(
            db, call.user_id, args.rule_set_id, RuleSetPatch(enabled=False), via="mcp"
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
            db, call.user_id, args.rule_set_id, fields, disabled_only=True, via="mcp"
        )
    )
    return added.model_dump(mode="json")


async def _filter_update(args: FilterUpdateArguments, call: ToolCall) -> JsonObject:
    patch = FilterPatch.model_validate(args.model_dump(exclude_unset=True, exclude={"filter_id"}))
    changed = await call.db(
        lambda db: service.change_filter(
            db, call.user_id, args.filter_id, patch, disabled_only=True, via="mcp"
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


async def _dry_run(args: RuleSetArguments, call: ToolCall) -> JsonObject:
    run = await call.db(
        lambda db: service.dry_run(call.ctx, db, call.user_id, args.rule_set_id, via="mcp")
    )
    return _run(run)


async def _rule_set_enable(args: RuleSetArguments, call: ToolCall) -> JsonObject:
    def enable(db: Session) -> JsonObject:
        changed = service.change_rule_set(
            db, call.user_id, args.rule_set_id, RuleSetPatch(enabled=True), via="mcp"
        )
        out: JsonObject = {"rule_set": changed.model_dump(mode="json")}
        if not service.run_filters(call.ctx, db, call.user_id, changed.account_id):
            out["note"] = RUN_FILTERS_OFF_NOTE
        return out

    return await call.db(enable)


async def _run_now(args: RuleSetArguments, call: ToolCall) -> JsonObject:
    run = await call.db(lambda db: service.run_now(db, call.user_id, args.rule_set_id, via="mcp"))
    return _run(run)


_DISABLED_ONLY = " Only on a disabled rule set: rule_set_disable it first."

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
            "An account's rule sets, oldest first, without their filters. A rule set runs its "
            "enabled filters, in order, on one folder, on its trigger."
        ),
        group="filters_read",
        arguments=AccountArguments,
        hints=READ,
        run=_rule_sets_list,
    ),
    Tool(
        name="rule_set_get",
        title="Get a rule set",
        description="A rule set with all its filters, in order, disabled ones too.",
        group="filters_read",
        arguments=RuleSetArguments,
        hints=READ,
        run=_rule_set_get,
    ),
    Tool(
        name="runs_list",
        title="List runs",
        description=(
            "A rule set's runs, newest first, dry runs too: when, what each filter matched, and "
            "any error. Senders and subjects come from mail, in untrusted_email tags."
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
            "dry_run it, then rule_set_enable it."
        ),
        group="filters_write",
        arguments=RuleSetCreateArguments,
        hints=Hints(),
        run=_rule_set_create,
    ),
    Tool(
        name="rule_set_update",
        title="Update a rule set",
        description="Change a rule set's name, folder, trigger or starred only." + _DISABLED_ONLY,
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
            "checked as filter_schema describes it." + _DISABLED_ONLY
        ),
        group="filters_write",
        arguments=FilterAddArguments,
        hints=Hints(),
        run=_filter_add,
    ),
    Tool(
        name="filter_update",
        title="Update a filter",
        description="Change a filter's name, enabled or spec." + _DISABLED_ONLY,
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
            "and report what each would match; enabled or not, nothing changes. One dry run at a "
            "time per user. Senders and subjects come from mail, in untrusted_email tags."
        ),
        group="filters_run",
        arguments=RuleSetArguments,
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
            "Queue a real run of an enabled rule set: its filters act on the folder's mail. "
            "Refused while the account's Run filters is off, for a disabled rule set, and while "
            "a run of it is already waiting. Confirm with the user first."
        ),
        group="filters_run",
        arguments=RuleSetArguments,
        hints=Hints(destructive=True),
        run=_run_now,
    ),
)
