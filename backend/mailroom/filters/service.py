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

from pydantic import BaseModel, BeforeValidator, Field, field_validator
from sqlalchemy import func, or_, select

from mailroom.accounts import app_data
from mailroom.accounts.handled import handled_mark
from mailroom.accounts.service import Via, require_account
from mailroom.errors import Refusal, mail_refusal
from mailroom.filters.providers import covers_public_provider, names_public_provider
from mailroom.filters.runs import (
    DryRunBusy,
    RunRefused,
    dry_run_result,
    dry_run_slot,
    enabled_filters,
    matches_counted,
    prune_runs,
    run_filters_on,
    runs_of,
)
from mailroom.filters.spec import InvalidSpecError, hidden_criterion, normalized_spec
from mailroom.filters.transfers import check_rule_set_targets, check_targets, moving_beyond
from mailroom.mail import service as mail_service
from mailroom.models import Filter, FilterRun, RuleSet
from mailroom.worker.tasks import queue_run

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from mailroom.context import AppContext
    from mailroom.errors import RefusalKind
    from mailroom_core.mail import HandledMark

logger = logging.getLogger(__name__)

Trigger = Literal["new_mail", "scheduled", "manual"]
Marker = Annotated[
    Literal["none", "starred", "handled"],
    BeforeValidator(lambda value: "starred" if value == "star" else value),
]
"""The mark a rule set limits itself to, cleared once its filters acted (``RuleSet.marker``);
``star`` is what ``starred`` was called, taken from a client that has not read the new names."""
Segment = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")]
Name = Annotated[str, Field(min_length=1, max_length=200)]
IntervalMinutes = Annotated[int, Field(ge=1, le=7 * 24 * 60)]
Spec = dict[str, Any]

MAX_FILTERS: Final = 100
"""Filters per rule set; the web app's Add filter stops there too."""

ALL_ACCOUNTS: Final = "all"
"""In place of an account id: a rule set for every account of the user's, run on each one whose
Run filters is on, on INBOX only (``RuleSet.account_id`` null)."""
Accounts = int | Literal["all"]
"""An account id, or ``ALL_ACCOUNTS``."""
INBOX: Final = ("INBOX",)


class RuleSetFields(BaseModel):
    name: Name
    folder: list[Segment] = Field(min_length=1, max_length=32)
    """Path segments, as the REST folder picker lists them (``account_folders``) and MCP's
    ``folders_list`` gives them as ``segments``: mailroom-core's LIST through
    ``mailroom.mail.service``, split on the server's delimiter."""
    trigger: Trigger
    interval_minutes: IntervalMinutes | None = None
    """``scheduled`` only, and required there."""
    marker: Marker = "none"
    """``handled`` only where the account's server can keep its mark (``handled_unsupported``)."""
    enabled: bool = False


class RuleSetPatch(BaseModel):
    """Only the fields present change. Leaving ``scheduled`` drops the interval."""

    name: Name | None = None
    folder: list[Segment] | None = Field(default=None, min_length=1, max_length=32)
    trigger: Trigger | None = None
    interval_minutes: IntervalMinutes | None = None
    marker: Marker | None = None
    enabled: bool | None = None


class FilterRowOut(BaseModel):
    id: int
    rule_set_id: int
    position: int
    name: str
    enabled: bool
    spec: Spec
    created_at: datetime
    updated_at: datetime
    """The user's last edit of its name, spec or switch; a reorder leaves it."""


class FilterOut(FilterRowOut):
    matches_30d: int
    """Messages it acted on in real runs over the last 30 days (UTC), whatever its spec was then:
    an edit keeps the count."""


class RuleSetOut(BaseModel):
    id: int
    account_id: Accounts
    """``all`` for a rule set run on every account whose Run filters is on."""
    folder: list[str]
    name: str
    trigger: Trigger
    interval_minutes: int | None
    marker: Marker
    enabled: bool
    disabled_reason: str | None
    """Why the app turned it off (``<code>: <message>``: ``copied_not_removed``,
    ``in_both_accounts``, ``move_target_removed``), until the user turns it on."""
    created_at: datetime
    updated_at: datetime

    @field_validator("account_id", mode="before")
    @classmethod
    def _all_accounts(cls, value: object) -> object:
        return ALL_ACCOUNTS if value is None else value


class RuleSetDetail(RuleSetOut):
    filters: list[FilterOut]
    """In order, disabled ones too."""


class FilterFields(BaseModel):
    name: Name
    enabled: bool = True
    spec: Spec
    """A filter spec (``mailroom_core.filters.json_schema``); kept as the filter engine
    serialises it."""


class FilterPatch(BaseModel):
    name: Name | None = None
    enabled: bool | None = None
    spec: Spec | None = None


class RunOut(BaseModel):
    id: int
    rule_set_id: int
    account_id: int
    """The account it ran on: its rule set's, or one of an all-accounts rule set's."""
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


def _filters_out(db: Session, rows: Sequence[Filter]) -> list[FilterOut]:
    counted = matches_counted(db, [row.id for row in rows])
    return [
        FilterOut(
            **FilterRowOut.model_validate(row, from_attributes=True).model_dump(),
            matches_30d=counted.get(row.id, 0),
        )
        for row in rows
    ]


def _filter_out(db: Session, row: Filter) -> FilterOut:
    return _filters_out(db, [row])[0]


def _run_out(run: FilterRun) -> RunOut:
    return RunOut.model_validate(run, from_attributes=True)


def _detail(db: Session, rule_set: RuleSet) -> RuleSetDetail:
    filters = _filters_out(db, _filters_of(db, rule_set.id))
    return RuleSetDetail(**_rule_set_out(rule_set).model_dump(), filters=filters)


def _log(event: str, user_id: int, via: Via, **extra: object) -> None:
    logger.info(event, extra={"event": event, "user_id": user_id, "via": via, **extra})


def owned_rule_set(
    ctx: AppContext, db: Session, user_id: int, rule_set_id: int, *, lock: bool = False
) -> RuleSet:
    """The user's rule set, or ``not_found``; their hidden ones (``mailroom.filters.mute``) are
    not found either, nor those beyond the caller's reach (one for all accounts, when it leaves
    accounts out, or one moving mail to an account it leaves out). ``lock``: hold its row until
    commit (any change to it or its filters)."""
    statement = select(RuleSet).where(
        RuleSet.id == rule_set_id,
        RuleSet.user_id == user_id,
        RuleSet.kind == "user",
        ctx.reach.covers(RuleSet.account_id),
    )
    if lock:
        statement = statement.with_for_update()
    rule_set = db.scalar(statement)
    if rule_set is None or moving_beyond(db, ctx.reach, [rule_set.id]):
        raise Refusal("not_found", "rule_set_not_found", "No such rule set")
    return rule_set


def owned_filter(
    ctx: AppContext, db: Session, user_id: int, filter_id: int
) -> tuple[Filter, RuleSet]:
    """The user's filter and its rule set, the latter locked; or ``not_found``."""
    rule_set_id = db.scalar(
        select(Filter.rule_set_id)
        .join(RuleSet, RuleSet.id == Filter.rule_set_id)
        .where(Filter.id == filter_id, RuleSet.user_id == user_id, RuleSet.kind == "user")
    )
    if rule_set_id is None:
        raise _filter_not_found()
    rule_set = owned_rule_set(ctx, db, user_id, rule_set_id, lock=True)
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
    ctx: AppContext, db: Session, user_id: int, rule_set_id: int, *, disabled_only: bool
) -> RuleSet:
    rule_set = owned_rule_set(ctx, db, user_id, rule_set_id, lock=True)
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


def check_mark(ctx: AppContext, user_id: int, account_id: int, mark: HandledMark, via: Via) -> None:
    """Refuse (``handled_unsupported``) a Handled mark the account's server cannot keep; a mailbox
    failure while asking is refused as the mail client's. The star is kept by every server."""
    if mark.kind == "keyword" and not mail_service.keywords(ctx, user_id, account_id, via=via):
        msg = "This account's server keeps no keywords, so it cannot keep Handled as one."
        raise Refusal("invalid", "handled_unsupported", msg)
    if mark.kind == "label" and not mail_service.gmail(ctx, user_id, account_id, via=via):
        msg = "Only Gmail keeps labels, so this account cannot keep Handled as one."
        raise Refusal("invalid", "handled_unsupported", msg)


def check_handled_kept(
    ctx: AppContext, db: Session, user_id: int, account_id: int | None, via: Via
) -> None:
    """Refuse (``check_mark``) Handled, as a marker or an action, on an account whose server cannot
    keep it as the account does. One for all accounts takes it: where a server cannot keep it,
    it matches nothing there, and a filter marking it fails there."""
    if account_id is None:
        return
    mark = handled_mark(db, user_id, account_id)
    # Not idle in a transaction, a row locked, while the mailbox answers.
    db.commit()
    check_mark(ctx, user_id, account_id, mark, via)


def check_to_sort(ctx: AppContext, user_id: int, account_id: int, via: Via) -> None:
    """Refuse a To sort action (``to_sort_unsupported``) on an account whose server keeps no
    keywords; a mailbox failure while asking is refused as the mail client's."""
    if not mail_service.keywords(ctx, user_id, account_id, via=via):
        msg = "This account's server cannot keep the To sort keyword: use Handled instead."
        raise Refusal("invalid", "to_sort_unsupported", msg)


_TO_SORT_ACTIONS: Final = frozenset({"mark_to_sort", "unmark_to_sort"})
_HANDLED_ACTIONS: Final = frozenset({"mark_handled", "unmark_handled"})


def has_node(node: object, types: frozenset[str]) -> bool:
    """Whether a node of one of ``types`` is anywhere in ``node``, a filter spec or a part."""
    if isinstance(node, list):
        return any(has_node(each, types) for each in node)
    if isinstance(node, dict):
        return node.get("type") in types or any(has_node(v, types) for v in node.values())
    return False


def _marking(spec: Spec, stored: Spec | None = None) -> frozenset[str]:
    """Those of the actions that mark To sort or Handled which ``spec`` has and ``stored``, the
    filter's own spec before, did not: refused, as a Handled marker is, on an account whose
    server cannot keep them (their criteria only match nothing there)."""
    before = {} if stored is None else stored.get("action")
    return frozenset(
        kind
        for kinds in (_TO_SORT_ACTIONS, _HANDLED_ACTIONS)
        if has_node(spec.get("action"), kinds) and not has_node(before, kinds)
        for kind in kinds
    )


def _check_marking(
    ctx: AppContext,
    db: Session,
    user_id: int,
    account_id: int | None,
    marking: frozenset[str],
    via: Via,
) -> None:
    if marking & _HANDLED_ACTIONS:
        check_handled_kept(ctx, db, user_id, account_id, via)
    if marking & _TO_SORT_ACTIONS:
        if account_id is None:
            raise _to_sort_all_accounts()
        db.commit()  # as above
        check_to_sort(ctx, user_id, account_id, via)


def _to_sort_all_accounts() -> Refusal:
    msg = "To sort is kept per account: a rule set for all accounts takes Handled instead."
    return Refusal("invalid", "to_sort_unsupported", msg)


def _inbox_only(folder: Sequence[str]) -> list[str]:
    """An all-accounts rule set's folder: INBOX, or ``all_accounts_inbox_only``."""
    if len(folder) != 1 or folder[0].upper() != "INBOX":
        msg = "A rule set for all accounts runs on INBOX only."
        raise Refusal("invalid", "all_accounts_inbox_only", msg)
    return list(INBOX)


PUBLIC_PROVIDER_TEXT: Final = (
    "A sender filter cannot name a public mail provider's whole domain, whose addresses are "
    "anyone's: use from_is with the address."
)


def _provider_leaf(criterion: Spec) -> bool:
    """Whether ``criterion``, a leaf, catches a public mail provider's whole domain: a
    ``from_domain`` on one, a ``from_contains`` or ``from_matches`` naming one
    (``mailroom.filters.providers``)."""
    args = criterion.get("args")
    if not isinstance(args, dict):
        return False
    match criterion.get("type"), args.get("domain"), args.get("addr"), args.get("pattern"):
        case "from_domain", str(domain), _, _:
            subdomains = args.get("incl_subdomains") is True
            return covers_public_provider(domain, subdomains=subdomains)
        case "from_contains", _, str(text), _:
            return names_public_provider(text, pattern=False)
        case "from_matches", _, _, str(pattern):
            return names_public_provider(pattern, pattern=True)
    return False


def _provider_domains(node: object) -> bool:
    """Whether a leaf anywhere in ``node`` catches a public mail provider (``_provider_leaf``)."""
    if isinstance(node, list):
        return any(_provider_domains(item) for item in node)
    if not isinstance(node, dict):
        return False
    return _provider_leaf(node) or any(_provider_domains(value) for value in node.values())


def _same_spec(normalized: Spec, stored: Spec | None) -> bool:
    if stored is None:
        return False
    try:
        return normalized == normalized_spec(stored)
    except InvalidSpecError:
        return False


def checked_spec(spec: Spec, *, stored: Spec | None = None) -> Spec:
    """``spec`` as the filter engine serialises it, or ``invalid_spec``; ``hidden_criterion`` for
    a criterion the app keeps for its own rule sets (``mailroom.filters.spec.HIDDEN_CRITERIA``);
    ``public_provider_domain`` for a sender criterion on a public mail provider's whole domain
    (``_provider_leaf``), unless ``spec`` is ``stored``, the filter's own spec an edit keeps."""
    try:
        normalized = normalized_spec(spec)
    except InvalidSpecError as exc:
        raise Refusal("invalid", "invalid_spec", str(exc)) from None
    hidden = hidden_criterion(normalized.get("criterion"))
    if hidden is not None:
        msg = f"{hidden} is kept for the app's own rule sets: a filter cannot use it."
        raise Refusal("invalid", "hidden_criterion", msg)
    if _provider_domains(normalized.get("criterion")) and not _same_spec(normalized, stored):
        raise Refusal("invalid", "public_provider_domain", PUBLIC_PROVIDER_TEXT)
    return normalized


def account_folders(ctx: AppContext, user_id: int, account_id: int) -> list[list[str]]:
    """The account's selectable folders, each as its path segments split on the server's
    delimiter (``["Archive", "2026"]``), in mailroom-core's order: ``mailroom.mail.service``'s
    listing, under the mail pool's gates and deadline, as MCP's ``folders_list`` gives them;
    without the label Handled is kept as."""
    require_account(ctx, user_id, account_id)  # a plain 404, as the filters' other routes
    listed = mail_service.folders(ctx, user_id, account_id, counts=False)
    with ctx.session_maker() as db:
        mark = handled_mark(db, user_id, account_id)
    label = mark.name if mark.kind == "label" else None
    return [mail_service.folder_segments(folder) for folder in listed if folder.path != label]


def run_filters(ctx: AppContext, db: Session, user_id: int, account_id: int) -> bool:
    """The account's Run filters switch (off until turned on)."""
    require_account(ctx, user_id, account_id)
    return app_data.run_filters_by_account(db, user_id).get(account_id, False)


def set_run_filters(
    ctx: AppContext,
    db: Session,
    user_id: int,
    account_id: int,
    *,
    on: bool,
    via: Via = "web",
) -> bool:
    """Turn the account's Run filters switch ``on`` or off; the new state. Turning it on is refused
    to a caller beyond whose reach it would move mail (``run_filters_out_of_reach``)."""
    require_account(ctx, user_id, account_id)
    if on:
        _refuse_runs_beyond(ctx, db, user_id, account_id)
    app_data.set_run_filters(db, user_id, account_id, run_filters=on)
    _log("run_filters_changed", user_id, via, account_id=account_id, run_filters=on)
    return on


def _refuse_runs_beyond(ctx: AppContext, db: Session, user_id: int, account_id: int) -> None:
    """Refuse to start the enabled rule sets that run on the account, those for all accounts too,
    while one moves mail to an account beyond the caller's reach."""
    if not ctx.reach.narrowed:
        return
    running = db.scalars(
        select(RuleSet.id).where(
            RuleSet.user_id == user_id,
            RuleSet.enabled,
            or_(RuleSet.account_id == account_id, RuleSet.account_id.is_(None)),
        )
    )
    if moving_beyond(db, ctx.reach, list(running)):
        msg = (
            "Rule sets running on this account move mail to an account beyond this app: only "
            "the user can turn Run filters on."
        )
        raise Refusal("invalid", "run_filters_out_of_reach", msg)


def _require_every_account(ctx: AppContext) -> None:
    """Refuse the rule sets for all accounts to a caller that reaches only some of them."""
    if ctx.reach.narrowed:
        msg = "Rule sets for all accounts are beyond this app: it has only some of the accounts."
        raise Refusal("invalid", "all_accounts_out_of_reach", msg)


def running_accounts(db: Session, user_id: int) -> list[int]:
    """The user's accounts whose Run filters is on, in id order: where an all-accounts rule set
    runs."""
    on = app_data.run_filters_by_account(db, user_id)
    return sorted(account for account, running in on.items() if running)


def rule_sets(ctx: AppContext, db: Session, user_id: int, account: Accounts) -> list[RuleSetOut]:
    """The account's rule sets, or the all-accounts ones (``ALL_ACCOUNTS``), oldest first, without
    their filters; never the hidden ones."""
    if isinstance(account, int):
        require_account(ctx, user_id, account)
        of_account = RuleSet.account_id == account
    else:
        _require_every_account(ctx)
        of_account = RuleSet.account_id.is_(None)
    rows = db.scalars(
        select(RuleSet)
        .where(RuleSet.user_id == user_id, of_account, RuleSet.kind == "user")
        .order_by(RuleSet.id)
    )
    listed = list(rows)
    beyond = moving_beyond(db, ctx.reach, [row.id for row in listed])
    return [_rule_set_out(row) for row in listed if row.id not in beyond]


def add_rule_set(
    ctx: AppContext,
    db: Session,
    user_id: int,
    account: Accounts,
    fields: RuleSetFields,
    *,
    via: Via = "web",
) -> RuleSetDetail:
    """A rule set on a folder of the account, or on INBOX of every account (``ALL_ACCOUNTS``:
    ``all_accounts_inbox_only`` for another folder). A ``handled`` marker is checked
    (``check_handled_kept``)."""
    account_id = account if isinstance(account, int) else None
    if account_id is not None:
        require_account(ctx, user_id, account_id)
    else:
        _require_every_account(ctx)
    check_schedule(fields.trigger, fields.interval_minutes)
    values = fields.model_dump()
    if account_id is None:
        values["folder"] = _inbox_only(fields.folder)
    if fields.marker == "handled":
        check_handled_kept(ctx, db, user_id, account_id, via)
    rule_set = RuleSet(user_id=user_id, account_id=account_id, **values)
    db.add(rule_set)
    db.commit()
    db.refresh(rule_set)
    _log("rule_set_added", user_id, via, account_id=account_id, rule_set_id=rule_set.id)
    return RuleSetDetail(**_rule_set_out(rule_set).model_dump(), filters=[])


def rule_set_detail(ctx: AppContext, db: Session, user_id: int, rule_set_id: int) -> RuleSetDetail:
    """The rule set with its filters, in order."""
    return _detail(db, owned_rule_set(ctx, db, user_id, rule_set_id))


def change_rule_set(
    ctx: AppContext,
    db: Session,
    user_id: int,
    rule_set_id: int,
    patch: RuleSetPatch,
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> RuleSetOut:
    """Change the fields ``patch`` sets (a None one stays, but for ``interval_minutes``).
    ``disabled_only``: refuse an enabled rule set (``refuse_enabled``). A marker changed to
    ``handled``, and an all-accounts rule set's folder, are checked as a new rule set's are;
    turning it on checks its filters' moves to another account again (``check_targets``) and
    clears its ``disabled_reason``."""
    if patch.marker == "handled":
        current = _owned_for_change(ctx, db, user_id, rule_set_id, disabled_only=disabled_only)
        account_id, marker = current.account_id, current.marker
        if marker != "handled":
            check_handled_kept(ctx, db, user_id, account_id, via)
    rule_set = _owned_for_change(ctx, db, user_id, rule_set_id, disabled_only=disabled_only)
    changes = patch.model_dump(exclude_unset=True)
    for field in ("name", "folder", "trigger", "marker", "enabled"):
        if changes.get(field, None) is None:
            changes.pop(field, None)
    if rule_set.account_id is None and "folder" in changes:
        changes["folder"] = _inbox_only(changes["folder"])
    if changes.get("enabled") and not rule_set.enabled:
        check_rule_set_targets(ctx.accounts(user_id), rule_set, enabled_filters(db, rule_set.id))
        changes["disabled_reason"] = None
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
    ctx: AppContext,
    db: Session,
    user_id: int,
    rule_set_id: int,
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> None:
    """Remove the rule set with its filters and runs."""
    rule_set = _owned_for_change(ctx, db, user_id, rule_set_id, disabled_only=disabled_only)
    db.delete(rule_set)
    db.commit()
    _log("rule_set_removed", user_id, via, account_id=rule_set.account_id, rule_set_id=rule_set_id)


def add_filter(
    ctx: AppContext,
    db: Session,
    user_id: int,
    rule_set_id: int,
    fields: FilterFields,
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> FilterOut:
    """Add a filter after the rule set's last one. One that marks or unmarks Handled is checked
    as a Handled marker is (``check_handled_kept``), To sort likewise; one that moves to another
    account, its target (``check_targets``)."""
    rule_set = _owned_for_change(ctx, db, user_id, rule_set_id, disabled_only=disabled_only)
    spec = checked_spec(fields.spec)
    check_targets(ctx.accounts(user_id), spec, rule_set.account_id)
    marking = _marking(spec)
    if marking:
        _check_marking(ctx, db, user_id, rule_set.account_id, marking, via)
        rule_set = _owned_for_change(ctx, db, user_id, rule_set_id, disabled_only=disabled_only)
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
    return _filter_out(db, row)


def change_filter(
    ctx: AppContext,
    db: Session,
    user_id: int,
    filter_id: int,
    patch: FilterPatch,
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> FilterOut:
    """Change the fields ``patch`` sets (a None one stays). A spec changed to mark or unmark
    Handled or To sort, or to move to another account, is checked as a new filter's is; so is the
    spec of a filter turned on, whose target may have gone while it was off."""
    row, rule_set = owned_filter(ctx, db, user_id, filter_id)
    if disabled_only:
        refuse_enabled(rule_set)
    spec = None if patch.spec is None else checked_spec(patch.spec, stored=row.spec)
    if spec is not None or (patch.enabled and not row.enabled):
        check_targets(ctx.accounts(user_id), spec or row.spec, rule_set.account_id)
    marking = frozenset() if spec is None else _marking(spec, row.spec)
    if marking:
        _check_marking(ctx, db, user_id, rule_set.account_id, marking, via)
        row, rule_set = owned_filter(ctx, db, user_id, filter_id)
        if disabled_only:
            refuse_enabled(rule_set)
    if patch.name is not None:
        row.name = patch.name
    if patch.enabled is not None:
        row.enabled = patch.enabled
    if spec is not None:
        row.spec = spec
    if db.is_modified(row):
        row.updated_at = func.clock_timestamp()
    db.commit()
    db.refresh(row)
    _log("filter_changed", user_id, via, rule_set_id=rule_set.id, filter_id=row.id)
    return _filter_out(db, row)


def remove_filter(
    ctx: AppContext,
    db: Session,
    user_id: int,
    filter_id: int,
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> None:
    """Remove the filter; the rest close up."""
    row, rule_set = owned_filter(ctx, db, user_id, filter_id)
    if disabled_only:
        refuse_enabled(rule_set)
    db.delete(row)
    db.flush()
    for position, rest in enumerate(_filters_of(db, rule_set.id)):
        rest.position = position
    db.commit()
    _log("filter_removed", user_id, via, rule_set_id=rule_set.id, filter_id=filter_id)


def reorder_filters(
    ctx: AppContext,
    db: Session,
    user_id: int,
    rule_set_id: int,
    filter_ids: list[int],
    *,
    disabled_only: bool = False,
    via: Via = "web",
) -> list[FilterOut]:
    """Put the rule set's filters in the order of ``filter_ids``: every one of them, each once."""
    rule_set = _owned_for_change(ctx, db, user_id, rule_set_id, disabled_only=disabled_only)
    rows = {row.id: row for row in _filters_of(db, rule_set.id)}
    if len(filter_ids) != len(rows) or set(filter_ids) != rows.keys():
        msg = "List every filter of the rule set, each once."
        raise Refusal("invalid", "invalid_order", msg)
    for position, filter_id in enumerate(filter_ids):
        rows[filter_id].position = position
    db.commit()
    _log("filters_reordered", user_id, via, rule_set_id=rule_set.id)
    return _filters_out(db, _filters_of(db, rule_set.id))


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


def _account_of_run(
    ctx: AppContext, user_id: int, rule_set: RuleSet, account_id: int | None
) -> int:
    """The account a run of ``rule_set`` acts on: its own (``account_id`` None or the same, else
    ``wrong_account``), or for an all-accounts one ``account_id``, one of the user's
    (``account_required`` when None)."""
    if rule_set.account_id is not None:
        if account_id not in (None, rule_set.account_id):
            msg = "The rule set runs on another account."
            raise Refusal("invalid", "wrong_account", msg)
        return rule_set.account_id
    if account_id is None:
        msg = "Name the account to run this rule set for all accounts on."
        raise Refusal("invalid", "account_required", msg)
    require_account(ctx, user_id, account_id)
    return account_id


def dry_run(
    ctx: AppContext,
    db: Session,
    user_id: int,
    rule_set_id: int,
    account_id: int | None = None,
    *,
    via: Via = "web",
) -> RunOut:
    """Run the enabled filters without acting, on the folder's newest messages, briefly
    (``mailroom.filters.runs.dry_run_result``); the run joins the history, refused or not. An
    all-accounts rule set's on ``account_id`` (``_account_of_run``). One dry run per user at a
    time: ``busy`` (``dry_run_busy``, not recorded) while another holds the slot."""
    rule_set = owned_rule_set(ctx, db, user_id, rule_set_id)
    target = _account_of_run(ctx, user_id, rule_set, account_id)
    filters = enabled_filters(db, rule_set.id)
    # Not idle in a transaction while the mailbox answers.
    db.commit()
    try:
        with dry_run_slot(ctx.engine, user_id):
            return _dry_run(ctx, db, user_id, rule_set, target, filters, via)
    except DryRunBusy:
        msg = "Another dry run of yours is still going: wait for it to finish."
        raise Refusal("busy", "dry_run_busy", msg) from None


def _dry_run(
    ctx: AppContext,
    db: Session,
    user_id: int,
    rule_set: RuleSet,
    account_id: int,
    filters: list[Filter],
    via: Via,
) -> RunOut:
    # Saved only once it has finished, so it is created when it starts, not when it is saved.
    started = datetime.now(UTC)
    run = FilterRun(
        rule_set_id=rule_set.id,
        account_id=account_id,
        trigger="manual",
        dry_run=True,
        created_at=started,
        started_at=started,
    )
    try:
        run.result = dry_run_result(ctx, user_id, rule_set, filters, account_id)
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
    prune_runs(db, run.rule_set_id, run.account_id)
    db.commit()


def run_now(
    ctx: AppContext,
    db: Session,
    user_id: int,
    rule_set_id: int,
    account_id: int | None = None,
    *,
    via: Via = "web",
) -> RunOut | list[RunOut]:
    """Queue a run for the worker: on the rule set's account, or for an all-accounts rule set on
    ``account_id``, else on each account whose Run filters is on (``_account_of_run`` for the
    account named). Refused while that Run filters is off (every one, for all accounts), for a
    disabled rule set, and while a run of it is already waiting on each account. The run
    queued; for an all-accounts rule set, the list of the runs queued."""
    rule_set = owned_rule_set(ctx, db, user_id, rule_set_id, lock=True)
    if rule_set.account_id is None and account_id is None:
        accounts = running_accounts(db, user_id)
        if not accounts:
            msg = "Run filters is off for every account: turn it on for one to run this rule set."
            raise Refusal("invalid", "run_filters_off", msg)
    else:
        target = _account_of_run(ctx, user_id, rule_set, account_id)
        if not run_filters_on(db, user_id, target):
            msg = "Run filters is off for this account: turn it on to run its rule sets."
            raise Refusal("invalid", "run_filters_off", msg)
        accounts = [target]
    if not rule_set.enabled:
        raise Refusal("invalid", "rule_set_disabled", "Enable the rule set to run it.")
    queued = [queue_run(db, rule_set, account, "manual") for account in accounts]
    run_ids = [run_id for run_id in queued if run_id is not None]
    if not run_ids:
        raise Refusal("conflict", "run_pending", "A run of this rule set is already waiting.")
    db.commit()
    rows = db.scalars(select(FilterRun).where(FilterRun.id.in_(run_ids)).order_by(FilterRun.id))
    out = [_run_out(run) for run in rows]
    for run in out:
        _log(
            "run_requested",
            user_id,
            via,
            rule_set_id=rule_set.id,
            account_id=run.account_id,
            run_id=run.id,
        )
    return out if rule_set.account_id is None else out[0]


def runs(ctx: AppContext, db: Session, user_id: int, rule_set_id: int, limit: int) -> list[RunOut]:
    """The rule set's newest ``limit`` runs, newest first."""
    rule_set = owned_rule_set(ctx, db, user_id, rule_set_id)
    return [_run_out(run) for run in runs_of(db, rule_set.id, limit)]
