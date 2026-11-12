"""``audit_list``: the user's audit trail, read-only (``mailroom.audit.listing``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import Field

from mailroom.audit import listing
from mailroom.mcp.envelope import untrusted
from mailroom.mcp.tool import READ, Arguments, JsonObject, Tool

if TYPE_CHECKING:
    from mailroom.audit.listing import ListedRecord
    from mailroom.mcp.tool import ToolCall

_FROM_MAIL: Final = ("from", "to", "cc", "bcc", "message_id", "rejected")
"""A record's fields that come from mail written by others."""


class AuditListArguments(Arguments):
    cursor: str | None = Field(
        default=None, max_length=20, description="next_cursor from the previous page."
    )
    limit: int = Field(
        default=listing.PAGE_SIZE, ge=1, le=listing.MAX_PAGE_SIZE, description="Records a page."
    )
    account_id: str | None = Field(default=None, min_length=1, max_length=64)
    action: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "move, archive, trash, flag, draft_save, draft_remove, send, folder_create, "
            "folder_rename, folder_delete, or a filter's."
        ),
    )
    actor: Literal["user", "agent", "filter"] | None = Field(
        default=None, description="user (the web), agent (MCP) or filter (a rule set's run)."
    )
    result: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description='"ok", an error code, or "failed" for every result but ok, refusals included.',
    )


def _record(record: ListedRecord) -> JsonObject:
    out = record.model_dump(mode="json")
    detail: dict[str, Any] = out["detail"]
    for key in _FROM_MAIL:
        value = detail.get(key)
        if isinstance(value, list):
            detail[key] = [untrusted(str(item)) for item in value]
        elif value:
            detail[key] = untrusted(str(value))
    return out


async def _audit_list(args: AuditListArguments, call: ToolCall) -> JsonObject:
    page = await call.db(
        lambda db: listing.records(
            db,
            call.user_id,
            cursor=args.cursor,
            limit=args.limit,
            account=args.account_id,
            action=args.action,
            actor=args.actor,
            result=args.result,
        )
    )
    listed = [_record(record) for record in page.records]
    return {"records": listed, "next_cursor": page.next_cursor}


AUDIT_TOOLS: Final[tuple[Tool[Any], ...]] = (
    Tool(
        name="audit_list",
        title="List the audit trail",
        description=(
            "What was done to the user's mail, newest first: each message a move, trash, flag, "
            "draft, send or filter run acted on or tried to, and each folder created, renamed "
            "or deleted; who acted (the user, an agent and its app, or a rule set) and the "
            "result. Subjects only as a hash. Addresses come from mail, in untrusted_email "
            "tags. Continue with next_cursor."
        ),
        group="audit_read",
        arguments=AuditListArguments,
        hints=READ,
        run=_audit_list,
    ),
)
