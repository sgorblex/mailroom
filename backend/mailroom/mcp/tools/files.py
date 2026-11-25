"""New files an agent attaches, passed in the call itself: text, or any bytes in base64. Each is
decoded strictly and its type checked against its bytes (``given_type``), so a file is never
labelled as something it is not; how many and how much in total is the service's to check, with
the parts of messages attached beside them. What an agent attaches goes no further than anything
else it writes: the account's send mode decides."""

from __future__ import annotations

import binascii
from typing import TYPE_CHECKING, Annotated, Final

from pydantic import AfterValidator, Field, model_validator

from mailroom.accounts.service import ATTACHMENT_MAX_MB
from mailroom.errors import mail_refusal
from mailroom.mcp.tool import Arguments
from mailroom_core.mail import MAX_UPLOADS, OutgoingAttachment, given_type
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mailroom.mcp.tool import ToolCall

MAX_FILE_NAME: Final = 200
"""As much of a name as a message carries."""
_UNKNOWN: Final = "application/octet-stream"
_MAX_BYTES: Final = int(ATTACHMENT_MAX_MB * 1024 * 1024)
_MAX_BASE64: Final = (_MAX_BYTES + 2) // 3 * 4


def _file_name(name: str) -> str:
    """``name`` as the message carries it: on one line, no character a reader cannot see."""
    cleaned = clean_header(name, MAX_FILE_NAME)
    if cleaned in ("", ".", "..") or "/" in cleaned or "\\" in cleaned:
        raise ValueError("not a file name: give one without a folder")
    return cleaned


FileName = Annotated[str, Field(min_length=1, max_length=MAX_FILE_NAME), AfterValidator(_file_name)]


class NewFile(Arguments):
    name: FileName = Field(description="File name, with its extension, without a folder.")
    content_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Media type, like `application/pdf`. Checked against the content: a type the "
            "content contradicts is refused. Left out, it is found from the content and name."
        ),
    )
    text: str | None = Field(
        default=None,
        min_length=1,
        max_length=_MAX_BYTES,
        description="The content of a text file, kept as UTF-8. Give text or base64.",
    )
    base64: str | None = Field(
        default=None,
        min_length=1,
        max_length=_MAX_BASE64,
        description="The content of any file in standard base64, without line breaks or a prefix.",
    )

    @model_validator(mode="after")
    def _one_content(self) -> NewFile:
        if (self.text is None) == (self.base64 is None):
            raise ValueError("give the content as text or as base64, one of them")
        return self


Files = Annotated[
    list[NewFile],
    Field(
        max_length=MAX_UPLOADS,
        description=(
            f"New files to attach, with their content; at most {MAX_UPLOADS} attachments per "
            "message, within the user's size limit. Attach only what the user asked for; for a "
            "file already in their mail use attachments."
        ),
    ),
]


def _content(file: NewFile, number: int) -> bytes:
    if file.base64 is None:
        return (file.text or "").encode()
    try:
        return binascii.a2b_base64(file.base64, strict_mode=True)
    except ValueError:  # not base64 (``binascii.Error``), or not ASCII
        raise mail_refusal(
            "invalid_argument",
            f"File {number}: its base64 is not valid: give standard base64 with its padding, and "
            "no line breaks, spaces or data: prefix.",
        ) from None


def _upload(file: NewFile, number: int) -> OutgoingAttachment:
    content = _content(file, number)
    kind = given_type(content, file.content_type, file.name)
    if kind is None:
        raise mail_refusal(
            "invalid_argument",
            f"File {number}: its content is not of its content_type: give the right one, or leave "
            "it out.",
        )
    text = file.text is not None and "\x00" not in file.text
    if text and file.content_type is None and kind == _UNKNOWN:
        kind = "text/plain"
    return OutgoingAttachment(file.name, kind, content, given=True)


async def uploads_of(call: ToolCall, files: Sequence[NewFile] | None) -> list[OutgoingAttachment]:
    """``files`` decoded and checked, in a worker thread."""
    given = files or ()
    if not given:
        return []
    return await call.thread(lambda: [_upload(file, n) for n, file in enumerate(given, 1)])


def check_count(new: Sequence[OutgoingAttachment], kept: int) -> None:
    """Refuses new files that, with ``kept`` parts, are more than a message may attach."""
    if new and len(new) + kept > MAX_UPLOADS:
        raise mail_refusal("invalid_argument", f"At most {MAX_UPLOADS} attachments per message.")
