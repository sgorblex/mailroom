"""Reading mail: list, search, read, thread and attachment, over ``mailroom.mail.service`` (never
over mailroom-core directly). The behaviour and the text are mailroom-mcp's ``list``, ``search``,
``read``, ``thread`` and ``attachment`` (mailroom-core's ``mail.text``): one line per message,
every mail-derived string inside the untrusted-content envelope. Nothing here changes a mailbox,
``\\Seen`` included.

A mail refusal's message is mailroom-core's fixed one, never the server's words.
"""

from __future__ import annotations

import base64
from datetime import date
from typing import TYPE_CHECKING, Annotated, Any, Final

from mailroom_core.config import time_zone_name
from mailroom_core.mail import (
    ATTACHMENT_FILE_NOTE,
    DISPLAYED_IMAGES,
    SearchQuery,
    ToolNames,
    attachment_label,
    attachment_text,
    empty_folder_text,
    format_ref,
    is_text_type,
    page_text,
    parse_ref,
    read_text,
    thread_text,
)
from mailroom_core.render import default_marker, untrusted
from mcp import types
from pydantic import AfterValidator, AnyUrl, Field, StrictBool

from mailroom.errors import Refusal
from mailroom.mail import service
from mailroom.mcp.tool import MAILBOX_READ, Arguments, Tool, content_result

if TYPE_CHECKING:
    from mailroom.mcp.tool import ToolCall

TIME_ZONE: Final = time_zone_name("UTC")
"""The zone of the dates the model reads: the app keeps no per-user zone yet."""
TOOL_NAMES: Final = ToolNames(
    read="message_read", thread="thread_read", attachment="attachment_read"
)
"""The tools mailroom-core's hints point to, as this server names them."""
NO_ACCOUNT: Final = "No mail account yet: the user adds one in Mailroom (Accounts)."

_ACCOUNT_ID = Field(
    max_length=64,
    min_length=1,
    description="An account id, from accounts_list (optional with a single account).",
)
Limit = Annotated[int, Field(ge=1, le=50, description="Messages per page (default 20, max 50).")]
Cursor = Annotated[
    str,
    Field(
        min_length=1,
        max_length=service.MAX_CURSOR,
        description="`next_cursor` from the previous page, to continue.",
    ),
]
RefText = Annotated[str, Field(min_length=1, max_length=service.MAX_REF_LENGTH)]
"""A message ref as every mail tool takes one (``mail_organize`` too)."""
Ref = Annotated[
    RefText, Field(description="Message ref from messages_list, messages_search or thread_read.")
]
Criterion = Annotated[str, Field(min_length=1, max_length=service.MAX_TERM)]
FolderPath = Annotated[str, Field(min_length=1, max_length=service.MAX_FOLDER)]


def _calendar_date(text: str) -> str:
    """A real calendar date: ``2026-02-30`` is refused rather than rolled over."""
    try:
        date.fromisoformat(text)
    except ValueError:
        message = "not a calendar date"
        raise ValueError(message) from None
    return text


IsoDate = Annotated[
    str,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD."),
    AfterValidator(_calendar_date),
]


class MessagesListArguments(Arguments):
    account_id: Annotated[str, _ACCOUNT_ID] | None = None
    folder: FolderPath = Field(
        default="INBOX",
        description="Folder path, as folders_list gives it in `path` (default INBOX).",
    )
    limit: Limit = 20
    cursor: Cursor | None = None


class MessagesSearchArguments(Arguments):
    account_id: Annotated[str, _ACCOUNT_ID] | None = Field(
        default=None,
        description='An account id, or "*" for every account (optional with a single account).',
    )
    folder: FolderPath = Field(
        default="INBOX",
        description='Folder path as folders_list gives it, or "*" for all folders (default INBOX).',
    )
    sender: Criterion | None = Field(
        default=None, alias="from", description="Sender address or name contains this."
    )
    to: Criterion | None = Field(
        default=None, description="Recipient address or name contains this."
    )
    subject: Criterion | None = None
    text: Criterion | None = Field(default=None, description="Headers or body contain this.")
    since: IsoDate | None = Field(default=None, description="On or after this date, YYYY-MM-DD.")
    before: IsoDate | None = Field(default=None, description="Before this date, YYYY-MM-DD.")
    unread: StrictBool | None = None
    flagged: StrictBool | None = None
    has_attachment: StrictBool | None = Field(
        default=None,
        description=(
            "Messages with an attachment: Gmail's has:attachment on Gmail, elsewhere "
            "approximate (sent as multipart/mixed)."
        ),
    )
    gmail_query: Criterion | None = Field(
        default=None, description="Gmail search syntax (X-GM-RAW), Gmail accounts only."
    )
    limit: Limit = 20
    cursor: Cursor | None = None


class MessageReadArguments(Arguments):
    ref: Ref
    offset: int = Field(
        default=0,
        ge=0,
        le=100_000_000,
        description="Continue a long body from this offset (given at the end of the last call).",
    )
    include_quoted: StrictBool = Field(
        default=False, description="Include quoted replies and signatures."
    )
    raw_headers: StrictBool = Field(
        default=False, description="Also show every header as received."
    )


class ThreadReadArguments(Arguments):
    ref: Annotated[Ref, Field(description="Ref of any message in the conversation.")]
    limit: int = Field(
        default=30,
        ge=1,
        le=50,
        description="Most messages to show, newest first (default 30, max 50).",
    )


class AttachmentReadArguments(Arguments):
    ref: Ref
    part: str = Field(
        pattern=r"^\d+(\.\d+)*$",
        max_length=64,
        description='Attachment part number from message_read, like "2" or "1.2".',
    )


def _text(text: str) -> types.CallToolResult:
    return content_result(types.TextContent(type="text", text=text))


async def _named(account_id: str | None, call: ToolCall, *, every: bool = False) -> str:
    """``account_id``, or the user's only account when none is given."""
    if account_id is not None:
        if account_id == service.ALL_ACCOUNTS and not every:
            message = 'Name one account, not "*".'
            raise Refusal("invalid", "invalid_argument", message)
        return account_id
    ids = await call.thread(lambda: list(service.account_ids(call.ctx, call.user_id)))
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise Refusal("not_found", "not_found", NO_ACCOUNT, coded=True)
    options = ", ".join(ids)
    star = ' (or "*" for every account)' if every else ""
    raise Refusal("invalid", "invalid_argument", f"Name an account{star}: {options}.")


async def _messages_list(args: MessagesListArguments, call: ToolCall) -> types.CallToolResult:
    account = await _named(args.account_id, call)
    page = await call.thread(
        lambda: service.list_messages(
            call.ctx,
            call.user_id,
            account,
            args.folder,
            limit=args.limit,
            cursor=args.cursor,
            via="mcp",
        )
    )
    return _text(page_text(page, empty_folder_text(args.folder), TIME_ZONE, tools=TOOL_NAMES))


async def _messages_search(args: MessagesSearchArguments, call: ToolCall) -> types.CallToolResult:
    account = await _named(args.account_id, call, every=True)
    query = SearchQuery(
        sender=args.sender,
        to=args.to,
        subject=args.subject,
        text=args.text,
        since=None if args.since is None else date.fromisoformat(args.since),
        before=None if args.before is None else date.fromisoformat(args.before),
        unread=args.unread,
        flagged=args.flagged,
        has_attachment=args.has_attachment,
        gmail_query=args.gmail_query,
    )
    page = await call.thread(
        lambda: service.search(
            call.ctx,
            call.user_id,
            account,
            args.folder,
            query,
            limit=args.limit,
            cursor=args.cursor,
            via="mcp",
        )
    )
    return _text(page_text(page, "No matching messages.", TIME_ZONE, tools=TOOL_NAMES))


async def _message_read(args: MessageReadArguments, call: ToolCall) -> types.CallToolResult:
    view = await call.thread(
        lambda: service.read_message(
            call.ctx,
            call.user_id,
            args.ref,
            offset=args.offset,
            include_quoted=args.include_quoted,
            raw_headers=args.raw_headers,
            marker=default_marker,
            via="mcp",
        )
    )
    return _text(read_text(view, offset=args.offset, time_zone=TIME_ZONE, tools=TOOL_NAMES))


async def _thread_read(args: ThreadReadArguments, call: ToolCall) -> types.CallToolResult:
    page = await call.thread(
        lambda: service.thread(call.ctx, call.user_id, args.ref, limit=args.limit, via="mcp")
    )
    return _text(thread_text(page, TIME_ZONE))


async def _attachment_read(args: AttachmentReadArguments, call: ToolCall) -> types.CallToolResult:
    found = await call.thread(
        lambda: service.attachment(call.ctx, call.user_id, args.ref, args.part, via="mcp")
    )
    if is_text_type(found.type):
        return _text(attachment_text(found))
    label = untrusted(attachment_label(found))
    data = base64.b64encode(found.data).decode("ascii")
    if found.type in DISPLAYED_IMAGES:
        return content_result(
            types.TextContent(type="text", text=label),
            types.ImageContent(type="image", data=data, mimeType=found.type),
        )
    return content_result(
        types.TextContent(type="text", text=f"{label}\n{ATTACHMENT_FILE_NOTE}"),
        types.EmbeddedResource(
            type="resource",
            resource=types.BlobResourceContents(
                # Named by the canonical ref: the service read the message, so it parses.
                uri=AnyUrl(f"mailroom://attachment/{format_ref(parse_ref(args.ref))}/{args.part}"),
                mimeType=found.type,
                blob=data,
            ),
        ),
    )


MAIL_READ_TOOLS: Final[tuple[Tool[Any], ...]] = (
    Tool(
        name="messages_list",
        title="List messages",
        description=(
            "The newest messages in one folder, one line each, newest first. Reading never "
            "marks mail as read."
        ),
        group="mail_read",
        arguments=MessagesListArguments,
        hints=MAILBOX_READ,
        run=_messages_list,
    ),
    Tool(
        name="messages_search",
        title="Search messages",
        description=(
            'Search one account or all ("*"), in one folder or all ("*": every folder except '
            "Junk and Trash; All Mail on Gmail). All given criteria must match. Results newest "
            "first, one line each."
        ),
        group="mail_read",
        arguments=MessagesSearchArguments,
        hints=MAILBOX_READ,
        run=_messages_search,
    ),
    Tool(
        name="message_read",
        title="Read a message",
        description=(
            "One message: its headers, attachment list and body as text. Quoted history and "
            "signatures are collapsed (include_quoted: true shows everything). Long bodies "
            "continue with `offset`. Never marks the message as read."
        ),
        group="mail_read",
        arguments=MessageReadArguments,
        hints=MAILBOX_READ,
        run=_message_read,
    ),
    Tool(
        name="thread_read",
        title="Show a conversation",
        description=(
            "The conversation a message belongs to, one line per message, newest first: its "
            "folder plus Sent (Gmail: the whole Gmail thread). Read any of them with "
            "message_read."
        ),
        group="mail_read",
        arguments=ThreadReadArguments,
        hints=MAILBOX_READ,
        run=_thread_read,
    ),
    Tool(
        name="attachment_read",
        title="Open an attachment",
        description=(
            "The content of one attachment, by message ref and part number (as message_read "
            "lists them). Text comes back as text, images as images, anything else (PDF, office "
            "files…) as an embedded file. Size-capped."
        ),
        group="mail_read",
        arguments=AttachmentReadArguments,
        hints=MAILBOX_READ,
        run=_attachment_read,
    ),
)
