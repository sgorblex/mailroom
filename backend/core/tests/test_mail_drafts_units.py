"""What needs no server: reading APPEND's raw response, and refusing before anything is opened."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from mailroom_core.mail import (
    Mailbox,
    MailConnections,
    MailError,
    MessageInput,
    OutgoingAttachment,
    appended_uid,
    check_sendable,
    find_drafts,
    save_draft,
)
from mailroom_core.mail.compose import is_own_draft

if TYPE_CHECKING:
    from mailroom_core.config import AccountConfig


@pytest.mark.parametrize(
    ("response", "uid"),
    [
        (b"[APPENDUID 1730000000 5] Append completed (0.002 + 0.000 secs).", 5),
        (b"[appenduid 1730000000 4294967295] done", 4294967295),
        (b"Append completed.", None),
        (b"", None),
        (None, None),
        (b"[APPENDUID 1 5] another folder's UIDVALIDITY", None),
        (b"[APPENDUID 1730000000 0] no such UID", None),
        (b"[APPENDUID 1730000000 4294967296] past 32 bits", None),
        (b"[APPENDUID 1730000000 5:7] a set, as MULTIAPPEND has", None),
        (b"Saved [APPENDUID 1730000000 5] in the text, not a code", None),
    ],
)
def test_reads_the_uid_of_appenduid(response: bytes | None, uid: int | None) -> None:
    assert appended_uid(response, 1730000000) == uid


def nothing_opens() -> MailConnections:
    """Connections that fail the test on any use."""
    return cast("MailConnections", SimpleNamespace())


def input_message(**fields: object) -> MessageInput:
    return MessageInput(sender=Mailbox("me@example.org"), subject="s", text="t", **fields)  # type: ignore[arg-type]


def account(mode: str = "direct") -> AccountConfig:
    return cast("AccountConfig", SimpleNamespace(id="acc", send_mode=mode))


def test_an_account_with_sending_off_is_refused() -> None:
    with pytest.raises(MailError) as raised:
        check_sendable(account("off"))
    assert raised.value.code == "send_disabled"
    check_sendable(account("drafts"))

    with pytest.raises(MailError) as raised:
        save_draft(nothing_opens(), account("off"), input_message(), cap=10, note=print)
    assert raised.value.code == "send_disabled"


@pytest.mark.parametrize(
    "compose_id", ["", "with space", 'quo"te', "a\r\nb", "<x@y>", "ü", "x" * 65]
)
def test_a_compose_id_goes_nowhere_near_a_search_or_a_header_unless_plain(compose_id: str) -> None:
    with pytest.raises(MailError, match="compose id"):
        find_drafts(nothing_opens(), "acc", compose_id)
    with pytest.raises(MailError, match="compose id"):
        save_draft(
            nothing_opens(), account(), input_message(), cap=10, note=print, compose_id=compose_id
        )


def test_uploads_over_the_cap_are_refused_first() -> None:
    upload = OutgoingAttachment("a", "text/plain", b"x" * 11)
    with pytest.raises(MailError) as raised:
        save_draft(
            nothing_opens(), account(), input_message(attachments=(upload,)), cap=10, note=print
        )
    assert raised.value.code == "too_large"


def test_a_foreign_header_is_not_a_mark() -> None:
    assert not is_own_draft(b"X-Mailroom-Draft: maybe\r\n")
    assert is_own_draft(b"X-MAILROOM-DRAFT: 1\r\n")
