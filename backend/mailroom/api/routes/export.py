"""The signed-in user's data as files to download: their rule sets (with the filters) and their
audit trail (mounted where ``/me/audit`` is)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse

from mailroom.api.deps import Context, CurrentCaller, DbSession
from mailroom.api.token_access import open_to_tokens
from mailroom.audit.export import day_bounds, trail_csv
from mailroom.auth.users import time_zone_of
from mailroom.filters.export import RuleSetsExport, rule_sets_export

if TYPE_CHECKING:
    from mailroom.api.deps import Caller

logger = logging.getLogger(__name__)

rule_sets_export_router = APIRouter(prefix="/me/export")
audit_export_router = APIRouter(prefix="/me/export")


def _headers(name: str, zone: str) -> dict[str, str]:
    """A download of ``name`` with today's date in ``zone``, kept out of caches."""
    today = datetime.now(ZoneInfo(zone)).date().isoformat()
    stem, _, suffix = name.partition(".")
    return {
        "Cache-Control": "private, no-store",
        "Content-Disposition": f'attachment; filename="mailroom-{stem}-{today}.{suffix}"',
        "X-Content-Type-Options": "nosniff",
    }


def _log(caller: Caller, what: str) -> None:
    logger.info(
        "data_exported",
        extra={
            "event": "data_exported",
            "user_id": caller.user.id,
            "token_id": caller.origin.token_id,
            "what": what,
        },
    )


@rule_sets_export_router.get(
    "/rule-sets",
    response_class=Response,
    responses={200: {"model": RuleSetsExport}},
)
@open_to_tokens("filters_read")
def export_rule_sets(ctx: Context, db: DbSession, caller: CurrentCaller) -> Response:
    """Every rule set of the user's with its filters, as JSON."""
    user_id = caller.user.id
    exported = rule_sets_export(ctx, db, user_id)
    _log(caller, "rule_sets")
    return Response(
        exported.model_dump_json(indent=2),
        media_type="application/json",
        headers=_headers("rule-sets.json", time_zone_of(db, user_id)),
    )


@audit_export_router.get("/audit.csv", response_class=StreamingResponse)
@open_to_tokens("audit_read")
def export_audit(
    ctx: Context,
    db: DbSession,
    caller: CurrentCaller,
    since: Annotated[date | None, Query(description="First day, in the user's time zone.")] = None,
    until: Annotated[date | None, Query(description="Last day, in the user's time zone.")] = None,
) -> StreamingResponse:
    """The user's audit trail as CSV, newest first, of the days from ``since`` to ``until``, both
    included. 400 ``invalid_range``."""
    user_id = caller.user.id
    zone = time_zone_of(db, user_id)
    start, end = day_bounds(since, until, zone)
    logins = {account.id: account.login for account in ctx.accounts(user_id).settings.account}
    # Not idle in a transaction while the browser takes what follows.
    db.commit()
    _log(caller, "audit")
    return StreamingResponse(
        trail_csv(db, user_id, reach=ctx.reach, logins=logins, since=start, until=end),
        media_type="text/csv; charset=utf-8",
        headers=_headers("audit.csv", zone),
    )
