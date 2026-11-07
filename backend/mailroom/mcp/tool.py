"""How a tool is defined and called. Every tool:

- takes strict arguments (a ``pydantic`` model refusing unknown fields, with the REST API's
  bounds), checked before it runs;
- answers JSON, or its own content (a mail tool answers text, an attachment an image or a file);
- is async, and does its database and mailbox work in a worker thread (``ToolCall.db``,
  ``ToolCall.thread``), so a slow mail server never holds up the event loop;
- a ``Refusal`` becomes a tool error with its fixed message, anything unexpected
  "Internal error" with the details in the log only;
- runs only while its user has fewer than ``UserSlots.per_user`` calls running in this process;
  one more is refused at once as busy, so one user cannot hold every worker thread;
- leaves one ``mcp_tool_called`` line (``mailroom.mcp.audit``).
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import anyio.to_thread
from mcp import types
from pydantic import BaseModel, ConfigDict, ValidationError

from mailroom.errors import Refusal
from mailroom.mcp import audit
from mailroom.mcp.verifier import grant_id_of, user_id_of

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Mapping

    from anyio import CapacityLimiter
    from mcp.server.auth.provider import AccessToken
    from sqlalchemy.orm import Session

    from mailroom.context import AppContext
    from mailroom.mcp.groups import GroupName

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]
ToolResult = JsonObject | types.CallToolResult
"""What a tool answers: a JSON object (also sent as text), or its own content (the mail tools
answer text, an attachment an image or a file)."""

INTERNAL_ERROR: Final = "Internal error; the server log has the details."
CALLS_PER_USER: Final = 2
"""Tool calls one user may have running at once, in one API process."""
BUSY_MESSAGE: Final = "Too many of your tool calls are still running: wait for one, then retry."
_MAX_LOGGED_KEYS: Final = 32
_MAX_KEY_LENGTH: Final = 64


class Arguments(BaseModel):
    """A tool's arguments: unknown ones refused, no type coercion (``"5"`` is not a number)."""

    model_config = ConfigDict(extra="forbid", strict=True)


@dataclass(frozen=True, slots=True)
class Hints:
    """MCP tool annotations, for a client deciding whether to ask the user first. Each is set
    explicitly: a client reads a missing ``destructiveHint`` as true."""

    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False


READ: Final = Hints(read_only=True, idempotent=True)
MAILBOX_READ: Final = Hints(read_only=True, idempotent=True, open_world=True)
"""A read of the mail server, whose answer carries what third parties wrote (as ``dry_run``)."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """What a running tool gets: the app's context and the user the token speaks for."""

    ctx: AppContext
    user_id: int
    limiter: CapacityLimiter | None = None

    async def thread[T](self, work: Callable[[], T]) -> T:
        """``work`` in a worker thread."""
        return await anyio.to_thread.run_sync(work, limiter=self.limiter)

    async def db[T](self, work: Callable[[Session], T]) -> T:
        """``work`` with a database session of its own, in a worker thread."""

        def run() -> T:
            with self.ctx.session_maker() as db:
                return work(db)

        return await self.thread(run)


class UserSlots:
    """The tool calls each user has running, at most ``per_user`` at once. Held on the event
    loop only (no lock needed), and per process: each API process counts its own."""

    def __init__(self, per_user: int = CALLS_PER_USER) -> None:
        self.per_user = per_user
        self._running: dict[int, int] = {}

    @contextmanager
    def slot(self, user_id: int) -> Iterator[None]:
        """One of ``user_id``'s slots for the block. Raises a ``busy`` ``Refusal``
        (``tool_calls_busy``) when they are all taken."""
        running = self._running.get(user_id, 0)
        if running >= self.per_user:
            raise Refusal("busy", "tool_calls_busy", BUSY_MESSAGE)
        self._running[user_id] = running + 1
        try:
            yield
        finally:
            left = self._running[user_id] - 1
            if left:
                self._running[user_id] = left
            else:
                del self._running[user_id]


@dataclass(frozen=True, slots=True)
class Tool[A: Arguments]:
    name: str
    title: str
    description: str
    group: GroupName
    arguments: type[A]
    hints: Hints
    run: Callable[[A, ToolCall], Awaitable[ToolResult]]
    also_in: tuple[GroupName, ...] = ()
    """Other groups that need the tool: it is registered when any of its groups is on."""

    @property
    def groups(self) -> tuple[GroupName, ...]:
        return (self.group, *self.also_in)

    def definition(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            title=self.title,
            description=self.description,
            inputSchema=self.arguments.model_json_schema(),
            annotations=types.ToolAnnotations(
                title=self.title,
                readOnlyHint=self.hints.read_only,
                destructiveHint=self.hints.destructive,
                idempotentHint=self.hints.idempotent,
                openWorldHint=self.hints.open_world,
            ),
        )


def _result(payload: ToolResult) -> types.CallToolResult:
    if isinstance(payload, types.CallToolResult):
        return payload
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], structuredContent=payload
    )


def content_result(*content: types.ContentBlock) -> types.CallToolResult:
    """A tool answer made of its own content blocks."""
    return types.CallToolResult(content=list(content))


def text_result(text: str) -> types.CallToolResult:
    """A tool answer of one text block."""
    return content_result(types.TextContent(type="text", text=text))


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], isError=True
    )


def _invalid_arguments(error: ValidationError) -> str:
    """The checks that failed, by field, without the values given."""
    issues = [
        f"{'.'.join(str(part) for part in issue['loc']) or 'arguments'}: {issue['msg']}"
        for issue in error.errors(include_url=False, include_input=False, include_context=False)
    ]
    return "Invalid arguments: " + "; ".join(issues)


def _logged_keys(arguments: Mapping[str, Any]) -> list[str]:
    return [str(key)[:_MAX_KEY_LENGTH] for key in list(arguments)[:_MAX_LOGGED_KEYS]]


async def call_tool(
    tools: Mapping[str, Tool[Any]],
    name: str,
    arguments: Mapping[str, Any],
    *,
    token: AccessToken | None,
    context: Callable[[], AppContext | None],
    limiter: CapacityLimiter | None = None,
    slots: UserSlots | None = None,
) -> types.CallToolResult:
    """Run tool ``name`` for the user ``token`` speaks for, with ``arguments`` as the client sent
    them, in one of the user's ``slots`` (no cap without)."""
    started = time.perf_counter()
    user_id = None if token is None else user_id_of(token)
    outcome: audit.Outcome = "failed"
    code: str | None = "aborted"
    try:
        tool = tools.get(name)
        if tool is None:
            outcome, code = "unknown_tool", None
            return _error(f"Unknown tool: {name[:_MAX_KEY_LENGTH]}")
        if user_id is None:
            outcome, code = "refused", "not_authenticated"
            return _error("The token names no Mailroom user.")
        try:
            parsed = tool.arguments.model_validate(dict(arguments))
        except ValidationError as exc:
            outcome, code = "invalid_arguments", None
            return _error(_invalid_arguments(exc))
        ctx = context()
        if ctx is None:
            outcome, code = "refused", "database_unavailable"
            return _error("The database is unavailable: try again later.")
        if slots is None:
            result = await tool.run(parsed, ToolCall(ctx, user_id, limiter))
        else:
            with slots.slot(user_id):
                result = await tool.run(parsed, ToolCall(ctx, user_id, limiter))
        outcome, code = "ok", None
        return _result(result)
    except Refusal as refusal:
        outcome, code = "refused", refusal.code
        return _error(refusal.message)
    except Exception:
        outcome, code = "failed", "internal"
        logger.exception("mcp_tool_failed", extra={"event": "mcp_tool_failed", "tool": name})
        return _error(INTERNAL_ERROR)
    finally:
        audit.log_tool_call(
            tool=name[:_MAX_KEY_LENGTH],
            user_id=user_id,
            client_id=None if token is None else token.client_id,
            grant_id=None if token is None else grant_id_of(token),
            argument_keys=_logged_keys(arguments),
            outcome=outcome,
            code=code,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
