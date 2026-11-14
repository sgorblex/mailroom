"""The tools, by module; ``TOOLS`` lists every one, whatever group it is in."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from mailroom.mcp.tools.accounts import ACCOUNT_TOOLS
from mailroom.mcp.tools.audit import AUDIT_TOOLS
from mailroom.mcp.tools.filters import FILTER_TOOLS
from mailroom.mcp.tools.mail_draft import MAIL_DRAFT_TOOLS
from mailroom.mcp.tools.mail_folders import MAIL_FOLDERS_TOOLS
from mailroom.mcp.tools.mail_organize import MAIL_ORGANIZE_TOOLS
from mailroom.mcp.tools.mail_read import MAIL_READ_TOOLS
from mailroom.mcp.tools.mail_send import MAIL_SEND_TOOLS
from mailroom.mcp.tools.mail_transfer import MAIL_TRANSFER_TOOLS

if TYPE_CHECKING:
    from mailroom.mcp.tool import Tool

TOOLS: Final[tuple[Tool[Any], ...]] = (
    *ACCOUNT_TOOLS,
    *FILTER_TOOLS,
    *MAIL_READ_TOOLS,
    *MAIL_ORGANIZE_TOOLS,
    *MAIL_TRANSFER_TOOLS,
    *MAIL_FOLDERS_TOOLS,
    *MAIL_DRAFT_TOOLS,
    *MAIL_SEND_TOOLS,
    *AUDIT_TOOLS,
)
