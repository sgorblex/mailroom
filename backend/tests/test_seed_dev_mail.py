"""The dev seed's sample mail: the HTML samples, and topping up an INBOX that has mail."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.utils import parseaddr
from typing import TYPE_CHECKING, Any, cast

from mailroom.dev.seed_dev_mail import (
    _HTML_SAMPLES,
    _SAMPLES,
    _html_message,
    _sample_id,
    _seeded,
)

if TYPE_CHECKING:
    from email.message import EmailMessage

    from mailroom_core.mail import MailboxContext


@dataclass
class _FakeClient:
    present: set[str] = field(default_factory=set)
    """Message-IDs the INBOX already holds."""
    appended: list[tuple[bytes, tuple[bytes, ...]]] = field(default_factory=list)

    def search(self, criteria: list[str]) -> list[int]:
        assert criteria[:2] == ["HEADER", "Message-ID"]
        return [1] if criteria[2] in self.present else []

    def append(self, folder: str, raw: bytes, flags: tuple[bytes, ...], msg_time: datetime) -> None:
        assert folder == "INBOX"
        _ = msg_time
        self.appended.append((raw, flags))


@dataclass
class _FakeContext:
    client: _FakeClient
    exists: int


def _parsed(raw: bytes) -> EmailMessage:
    return cast("EmailMessage", message_from_bytes(raw, policy=policy.default))


def _seed(context: _FakeContext) -> int:
    return _seeded("dev", cast("MailboxContext", cast("Any", context)))


def test_an_empty_inbox_gets_every_sample() -> None:
    client = _FakeClient()
    assert _seed(_FakeContext(client, exists=0)) == len(_SAMPLES) + len(_HTML_SAMPLES)
    assert len(client.appended) == len(_SAMPLES) + len(_HTML_SAMPLES)
    assert sum(1 for _raw, flags in client.appended if flags == (b"\\Flagged",)) == 1


def test_an_inbox_with_mail_gets_only_the_html_samples_it_lacks() -> None:
    have = _HTML_SAMPLES[0]
    client = _FakeClient(present={_sample_id(have, "dev")})
    assert _seed(_FakeContext(client, exists=12)) == len(_HTML_SAMPLES) - 1
    subjects = {_parsed(raw)["Subject"] for raw, _flags in client.appended}
    assert subjects == {sample.subject for sample in _HTML_SAMPLES[1:]}


def test_an_inbox_with_every_sample_is_left_alone() -> None:
    present = {_sample_id(s, "dev") for s in _HTML_SAMPLES}
    client = _FakeClient(present=present)
    assert _seed(_FakeContext(client, exists=20)) == 0
    assert client.appended == []


def test_html_samples_have_text_and_html_and_one_sender_twice() -> None:
    date = datetime(2026, 11, 1, tzinfo=UTC)
    senders: list[str] = []
    for sample in _HTML_SAMPLES:
        message = _parsed(_html_message(sample, "dev", date))
        assert message.get_content_type() == "multipart/alternative"
        text = message.get_body(("plain",))
        html = message.get_body(("html",))
        assert text is not None
        assert text.get_content().strip()
        assert html is not None
        assert "<html>" in html.get_content()
        assert message["Message-ID"] == f"<{sample.key}.dev@mailroom.test>"
        senders.append(parseaddr(str(message["From"]))[1])
    assert any(senders.count(address) == 2 for address in senders)


def test_the_coloured_sample_shows_its_own_inline_png() -> None:
    sample = next(s for s in _HTML_SAMPLES if s.image is not None)
    message = _parsed(_html_message(sample, "dev", datetime(2026, 11, 1, tzinfo=UTC)))
    related = next(p for p in message.walk() if p.get_content_type() == "multipart/related")
    html, image = list(related.iter_parts())
    assert f'src="cid:{sample.image}"' in html.get_content()
    assert "background-color" in html.get_content()
    assert image.get_content_type() == "image/png"
    assert image["Content-ID"] == f"<{sample.image}>"
    data = image.get_content()
    assert isinstance(data, bytes)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
