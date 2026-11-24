"""New files in a tool call without a mailbox: what their schema and decoding refuse, and what a
request may hold (``RequestLimit``). What the tools do with them is in ``test_mcp``."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

import anyio
import pytest
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from pydantic import ValidationError

from mailroom.errors import Refusal
from mailroom.mcp.app import MAX_ATTACHING_BYTES, MAX_REQUEST_BYTES, RequestLimit
from mailroom.mcp.tool import CALLS_PER_USER
from mailroom.mcp.tools.files import NewFile, _upload, check_count
from mailroom.mcp.tools.outgoing import AttachmentRef
from mailroom_core.mail import MAX_HTML_BYTES, OutgoingAttachment

if TYPE_CHECKING:
    from starlette.types import Message, Receive, Scope, Send

PDF = b"%PDF-1.4\n%%EOF\n"
PDF64 = base64.b64encode(PDF).decode()


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "a.txt"},
        {"name": "a.txt", "text": "a", "base64": "YQ=="},
        {"name": "a.txt", "text": ""},
        {"name": "a.txt", "base64": ""},
        {"name": "a.txt", "text": 5},
        {"name": "a.txt", "text": "a", "path": "/etc/passwd"},
        {"name": "", "text": "a"},
        {"name": " \u200b ", "text": "a"},
        {"name": "..", "text": "a"},
        {"name": "../a.txt", "text": "a"},
        {"name": "a\\b.txt", "text": "a"},
        {"name": "a\ud800.txt", "text": "a"},
        {"name": "a.txt", "text": "a\ud800"},
        {"name": "x" * 201, "text": "a"},
        {"name": "a.txt", "text": "a", "content_type": ""},
    ],
)
def test_a_file_needs_a_plain_name_and_one_content(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        NewFile.model_validate(fields)


def test_a_name_is_cleaned_to_one_line_a_reader_sees_whole() -> None:
    named = NewFile.model_validate({"name": "in\r\nvoice\u202efdp.exe", "text": "a"})
    assert named.name == "in voicefdp.exe"
    kept = AttachmentRef.model_validate({"ref": "x", "part": "2", "name": "new\u200b name.pdf"})
    assert kept.name == "new name.pdf"
    with pytest.raises(ValidationError):
        AttachmentRef.model_validate({"ref": "x", "part": "2", "name": "a/b"})


def uploaded(**fields: Any) -> OutgoingAttachment:
    return _upload(NewFile.model_validate(fields), 1)


def test_content_is_decoded_and_its_type_found_or_checked() -> None:
    found = uploaded(name="a.bin", base64=PDF64)
    assert (found.filename, found.content_type, found.content) == ("a.bin", "application/pdf", PDF)
    assert found.given
    assert uploaded(name="a.csv", text="é,1").content_type == "text/csv"
    assert uploaded(name="a.csv", text="é,1").content == "é,1".encode()
    assert uploaded(name="LICENSE", text="free").content_type == "text/plain"
    assert uploaded(name="a.html", text="<p>", content_type="text/html").content_type == "text/html"
    unknown = uploaded(name="a.bin", base64="AAE=")
    assert unknown.content_type == "application/octet-stream"
    for binary in ({"base64": "TVqQAAMA"}, {"text": "MZ\x00"}):
        assert uploaded(name="a.txt", **binary).content_type == "application/octet-stream"


@pytest.mark.parametrize(
    "broken",
    [
        "YQ",
        "YQ=",
        "YQ===",
        "YQ==YQ==",
        "Y Q==",
        "YWFh\nYWFh",
        "-_-_",
        "é===",
        "data:text/plain;base64,YQ==",
    ],
)
def test_base64_is_read_strictly(broken: str) -> None:
    with pytest.raises(Refusal, match="File 1: its base64 is not valid") as refused:
        uploaded(name="a.bin", base64=broken)
    assert refused.value.code == "invalid_argument"


def test_contradicted_types_are_refused() -> None:
    for fields in (
        {"base64": PDF64, "content_type": "image/png"},
        {"text": "not a pdf", "content_type": "application/pdf"},
        {"base64": "TVqQAAMA", "content_type": "text/plain"},
        {"text": "a", "content_type": "nonsense"},
    ):
        with pytest.raises(Refusal, match="not of its content_type"):
            uploaded(name="a", **fields)


def test_new_files_count_with_the_kept_parts() -> None:
    new = [uploaded(name="a", text="a")]
    check_count(new, 9)
    check_count([], 40)
    with pytest.raises(Refusal, match="At most 10"):
        check_count(new, 10)


def test_the_largest_request_holds_the_largest_attachments_beside_the_largest_html() -> None:
    largest = 25 * 1024 * 1024
    assert (largest + 2) // 3 * 4 + MAX_HTML_BYTES + MAX_REQUEST_BYTES <= MAX_ATTACHING_BYTES


class Inner:
    """The MCP server in ``RequestLimit``: reads the body, then holds the request if told to."""

    def __init__(self) -> None:
        self.bodies: list[int] = []
        self.entered = 0
        self.release: anyio.Event | None = None

    async def __call__(self, _scope: Scope, receive: Receive, send: Send) -> None:
        self.entered += 1
        size, more = 0, True
        while more:
            message = await receive()
            size += len(message.get("body", b""))
            more = message.get("more_body", False)
        self.bodies.append(size)
        if self.release is not None:
            await self.release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})


class Asked:
    def __init__(self) -> None:
        self.reads = 0
        self.status = 0


async def ask(
    app: RequestLimit,
    size: int,
    *,
    groups: list[str] | None = None,
    subject: str = "1",
    declared: bool = True,
    length: str | None = None,
) -> Asked:
    claims = None if groups is None else {"tool_groups": groups}
    token = AccessToken(token="t", client_id="c", scopes=[], subject=subject, claims=claims)
    sent = str(size) if length is None else length
    headers = [(b"content-length", sent.encode())] if declared else []
    scope = {"type": "http", "headers": headers, "user": AuthenticatedUser(token)}
    asked = Asked()

    async def receive() -> Message:
        asked.reads += 1
        return {"type": "http.request", "body": b"x" * size, "more_body": False}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            asked.status = message["status"]

    await app(scope, receive, send)
    return asked


SERVED = frozenset({"mail_read", "mail_draft", "mail_send"})
LARGE = MAX_REQUEST_BYTES + 1


@pytest.mark.parametrize(
    ("served", "groups", "status"),
    [
        (SERVED, ["mail_draft"], 200),
        (SERVED, ["mail_send"], 200),
        (SERVED, None, 200),
        (SERVED, ["mail_read"], 413),
        (SERVED, [], 413),
        (frozenset({"mail_read"}), ["mail_draft"], 413),
    ],
)
def test_only_a_caller_who_may_attach_files_sends_a_large_body(
    served: frozenset[str], groups: list[str] | None, status: int
) -> None:
    inner = Inner()
    asked = anyio.run(lambda: ask(RequestLimit(inner, served), LARGE, groups=groups))
    assert asked.status == status
    assert inner.bodies == ([LARGE] if status == 200 else [])
    # A refused one is refused before any of its body is read.
    assert asked.reads == (1 if status == 200 else 0)


def test_a_body_past_the_ceiling_or_of_no_declared_length_is_refused() -> None:
    inner = Inner()
    app = RequestLimit(inner, SERVED)
    past = anyio.run(lambda: ask(app, 1, length=str(MAX_ATTACHING_BYTES + 1)))
    assert (past.status, past.reads) == (413, 0)
    undeclared = anyio.run(lambda: ask(app, LARGE, declared=False))
    assert undeclared.status == 413
    assert anyio.run(lambda: ask(app, 10, declared=False)).status == 200


def test_a_user_has_as_many_large_requests_at_once_as_tool_calls() -> None:
    inner = Inner()
    app = RequestLimit(inner, SERVED)

    async def run() -> None:
        inner.release = anyio.Event()
        async with anyio.create_task_group() as group:
            for _ in range(CALLS_PER_USER):
                group.start_soon(ask, app, LARGE)
            while inner.entered < CALLS_PER_USER:
                await anyio.sleep(0)
            busy = await ask(app, LARGE)
            assert (busy.status, busy.reads) == (429, 0)
            inner.release.set()
            # Small requests and another user's are never held up by them.
            assert (await ask(app, 10)).status == 200
            assert (await ask(app, LARGE, subject="2")).status == 200
        assert (await ask(app, LARGE)).status == 200

    anyio.run(run)
