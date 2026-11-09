"""`src/send/gate.test.ts`, on a real send log and with SMTP and IMAP stubbed, plus what the Python
gate adds: who authorised a send, the `Admitted` token, and retries by key."""

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from mailroom_core.config import AccountConfig, Limits
from mailroom_core.mail import (
    Capabilities,
    Draft,
    Envelope,
    Mailbox,
    MailboxContext,
    MailConnections,
    MailError,
    MessageInput,
    MessageRef,
    compose,
    recipient_list,
)
from mailroom_core.send import (
    DRAFT_KEPT,
    DRAFT_NOT_REMOVED,
    ORIGINAL_NOT_MARKED,
    Admitted,
    Delivery,
    Drafted,
    Earlier,
    GateDeps,
    GateResult,
    Prepared,
    SmtpSender,
    SmtpTimeouts,
    Transfer,
    admit,
    deliver,
    file_sent,
    mark_original,
    prepare_draft,
    send_through_gate,
    sends_now,
    settle_draft,
)
from mailroom_core.send import gate as gate_module
from mailroom_core.store import SEND_CEILING, AccountStore, SendRecord
from mailroom_core.testing import STALL, SmtpStub

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sqlalchemy import Engine

    from mailroom_core.mail import AuditItem
    from mailroom_core.store import SendLog


@dataclass
class StubClient:
    """What an APPEND touches, with no read-back: the ref comes out None."""

    appended: list[tuple[str, bytes, tuple[str, ...]]]
    fails: bool = False

    def append(self, folder: str, raw: bytes, flags: tuple[str, ...]) -> bytes:
        if self.fails:
            raise MailError("server_error")
        self.appended.append((folder, raw, flags))
        return b"done"

    def noop(self) -> None:
        pass

    def search(self, _criteria: object) -> list[int]:
        return []

    def add_flags(self, _uids: object, _flags: object, *, silent: bool) -> None:
        raise MailError("server_error")


@dataclass
class StubConnections:
    gmail: bool = False
    capabilities_fail: bool = False
    append_fails: bool = False
    appended: list[tuple[str, bytes, tuple[str, ...]]] = field(default_factory=list)

    def capabilities(self, _account: str) -> Capabilities:
        if self.capabilities_fail:
            raise MailError("unreachable")
        return Capabilities(
            gmail=self.gmail,
            thread=False,
            move=True,
            uidplus=True,
            condstore=False,
            special_use=True,
        )

    def special_folder(self, _account: str, role: str) -> SimpleNamespace:
        return SimpleNamespace(path=role.title())

    def with_mailbox[T](
        self, _account: str, folder: str, read_only: bool, fn: Callable[[MailboxContext], T]
    ) -> T:
        client = StubClient(self.appended, self.append_fails)
        return fn(
            MailboxContext(
                client=cast("Any", client),
                path=folder,
                read_only=read_only,
                uid_validity=1,
                exists=1,
                uid_next=7,
            )
        )


class StubSmtp:
    def __init__(self, outcome: Callable[[], Delivery] = Delivery) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, Envelope, bytes]] = []
        self._lock = threading.Lock()

    def send(
        self, account_id: str, envelope: Envelope, raw: bytes, transfer: Transfer | None = None
    ) -> Delivery:
        with self._lock:
            self.calls.append((account_id, envelope, raw))
        return self.outcome()


def raises(error: Exception) -> Callable[[], Delivery]:
    def outcome() -> Delivery:
        raise error

    return outcome


def in_turn(*outcomes: Callable[[], Delivery]) -> Callable[[], Delivery]:
    left = iter(outcomes)

    def outcome() -> Delivery:
        return next(left)()

    return outcome


@dataclass
class Gate:
    deps: GateDeps
    log: SendLog
    smtp: StubSmtp
    connections: StubConnections
    items: list[AuditItem]

    def run(
        self,
        account: AccountConfig,
        message: MessageInput,
        *,
        by: str = "agent",
        prepared: Prepared | None = None,
        key: str | None = None,
        confirm_resend: bool = False,
    ) -> GateResult:
        return send_through_gate(
            self.deps,
            account,
            message,
            self.items.append,
            by=by,  # type: ignore[arg-type]
            prepared=prepared,
            key=key,
            confirm_resend=confirm_resend,
        )

    def admit(self, account: AccountConfig, message: MessageInput, **options: Any) -> Any:
        return admit(self.deps, account, message, self.items.append, **options)


def account(mode: str = "direct", auto_approve: Iterable[str] = ()) -> AccountConfig:
    return AccountConfig.model_validate(
        {
            "id": "acc",
            "login": "me",
            "send_mode": mode,
            "auth": {"type": "password"},
            "imap": {"host": "127.0.0.1", "port": 9, "tls": "implicit"},
            "smtp": {"host": "127.0.0.1", "port": 9, "tls": "implicit"},
            "identities": [{"address": "me@example.test", "default": True}],
            "auto_approve": list(auto_approve),
        }
    )


def message(to: Iterable[str], **fields: Any) -> MessageInput:
    return MessageInput(
        sender=Mailbox("me@example.test"),
        to=recipient_list(to),
        subject="Hello",
        text="Hi",
        **fields,
    )


@pytest.fixture
def make_gate(core_engine: Engine) -> Callable[..., Gate]:
    def make(
        *,
        gmail: bool = False,
        per_hour: int = 20,
        smtp: Callable[[], Delivery] = Delivery,
        log: SendLog | None = None,
        **connections: bool,
    ) -> Gate:
        stub = StubConnections(gmail=gmail, **connections)
        sender = StubSmtp(smtp)
        store = log or AccountStore(core_engine, "user-1")
        deps = GateDeps(
            connections=cast("MailConnections", stub),
            smtp=sender,
            send_log=store,
            limits=Limits(send_per_hour=per_hour, max_recipients=3),
        )
        return Gate(deps, store, sender, stub, [])

    return make


# The cases of gate.test.ts.


def test_sends_in_direct_mode_files_a_copy_in_sent_and_audits_it(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate()
    result = gate.run(account(), message(["ana@x.org"]))
    assert (result.outcome, result.filed, result.rejected) == ("sent", True, ())
    assert len(gate.smtp.calls) == 1
    assert [(folder, flags) for folder, _raw, flags in gate.connections.appended] == [
        ("Sent", ("\\Seen",))
    ]
    [item] = gate.items
    assert (item.delivery, item.mode, item.result, item.filed) == ("sent", "direct", "ok", True)
    assert item.recipients is not None
    assert item.recipients.to == ("ana@x.org",)
    assert item.message_id is not None


def test_leaves_sent_to_gmail_which_files_it_itself(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate(gmail=True)
    assert gate.run(account(), message(["ana@x.org"])).filed is True
    assert gate.connections.appended == []


def test_drafts_instead_of_sending_in_drafts_mode(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate()
    result = gate.run(account("drafts"), message(["ana@x.org"]))
    assert result.outcome == "drafted"
    assert gate.smtp.calls == []
    [(folder, raw, flags)] = gate.connections.appended
    assert folder == "Drafts"
    assert flags == ("\\Draft", "\\Seen")
    assert b"X-Mailroom-Draft: 1\r\n" in raw
    [item] = gate.items
    assert (item.delivery, item.mode, item.folder) == ("drafted", "drafts", "Drafts")


def test_sends_in_approval_mode_only_when_every_recipient_is_auto_approved(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate()
    approving = account("approval", ["@team.example", "bo@x.org"])
    assert gate.run(approving, message(["ana@team.example", "bo@x.org"])).outcome == "sent"
    hidden = message(["ana@team.example"], bcc=recipient_list(["eve@x.org"]))
    assert gate.run(approving, hidden).outcome == "drafted"
    assert gate.run(approving, message(["eve@x.org"])).outcome == "drafted"
    assert len(gate.smtp.calls) == 1


def test_refuses_when_off_over_the_cap_or_with_nobody_to_send_to(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate()
    with pytest.raises(MailError) as off:
        gate.run(account("off"), message(["ana@x.org"]))
    assert off.value.code == "send_disabled"
    with pytest.raises(MailError, match="over the limit of 3"):
        gate.run(account(), message(["a@x.org", "b@x.org", "c@x.org", "d@x.org"]))
    with pytest.raises(MailError, match="at least one"):
        gate.run(account(), message([]))
    assert gate.smtp.calls == []


def test_holds_the_hourly_limit_against_parallel_sends(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate(per_hour=2)

    def attempt(_index: int) -> str:
        try:
            return gate.run(account(), message(["a@x.org"])).outcome
        except MailError as error:
            return error.code

    with ThreadPoolExecutor(10) as pool:
        outcomes = list(pool.map(attempt, range(10)))
    assert sorted(outcomes) == ["rate_limited"] * 8 + ["sent"] * 2
    assert len(gate.smtp.calls) == 2


def test_audits_refusals_with_the_recipients_they_were_aimed_at(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate(per_hour=1)
    gate.run(account(), message(["a@x.org"]))
    with pytest.raises(MailError):
        gate.run(account(), message(["target@evil.org"]))
    item = gate.items[-1]
    assert item.recipients is not None
    assert item.recipients.to == ("target@evil.org",)
    assert (item.delivery, item.result) == ("refused", "rate_limited")


def test_refuses_undeliverable_addresses_from_an_original_before_any_approval_match(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate()
    forged = replace(message(["ok@team.example"]), to=(Mailbox("x@evil.org@team.example"),))
    with pytest.raises(MailError, match="Not an email address"):
        gate.run(account("approval", ["@team.example"]), forged)
    assert gate.smtp.calls == []
    assert [item.result for item in gate.items] == ["invalid_argument"]


def test_reports_recipients_the_server_refused_among_the_others(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate(smtp=lambda: Delivery(("bo@x.org",)))
    result = gate.run(account(), message(["a@x.org", "bo@x.org"]))
    assert result.rejected == ("bo@x.org",)
    [item] = gate.items
    assert (item.rejected, item.result) == (("bo@x.org",), "ok")


def test_stops_at_the_hourly_limit_saying_when_the_next_send_is_possible(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate(per_hour=2)
    gate.run(account(), message(["a@x.org"]))
    gate.run(account(), message(["a@x.org"]))
    with pytest.raises(MailError, match="the next send is possible at") as raised:
        gate.run(account(), message(["a@x.org"]))
    assert raised.value.code == "rate_limited"
    assert len(gate.smtp.calls) == 2


def test_audits_a_failed_send_with_its_code_and_files_nothing(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate(smtp=raises(MailError("recipient_refused")))
    with pytest.raises(MailError) as raised:
        gate.run(account(), message(["ana@x.org"]))
    assert raised.value.code == "recipient_refused"
    assert gate.connections.appended == []
    assert [(i.delivery, i.result) for i in gate.items] == [("attempted", "recipient_refused")]


# Who authorised it.


@pytest.mark.parametrize("mode", ["drafts", "approval", "direct"])
def test_a_person_sends_whatever_the_mode_short_of_off(
    make_gate: Callable[..., Gate], mode: str
) -> None:
    gate = make_gate()
    assert gate.run(account(mode), message(["ana@x.org"]), by="user").outcome == "sent"
    assert gate.items[0].by == "user"


def test_off_refuses_a_person_too(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate()
    with pytest.raises(MailError) as raised:
        gate.run(account("off"), message(["ana@x.org"]), by="user")
    assert raised.value.code == "send_disabled"
    assert gate.smtp.calls == []


def test_the_limit_and_the_cap_bind_a_person_too(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate(per_hour=1)
    gate.run(account("drafts"), message(["a@x.org"]), by="user")
    with pytest.raises(MailError) as limited:
        gate.run(account("drafts"), message(["a@x.org"]), by="user")
    assert limited.value.code == "rate_limited"
    with pytest.raises(MailError, match="over the limit of 3"):
        gate.run(account(), message(["a@x.org", "b@x.org", "c@x.org", "d@x.org"]), by="user")
    assert len(gate.smtp.calls) == 1


def test_sends_now_reads_who_and_the_mode() -> None:
    plain = message(["a@x.org"])
    assert sends_now(account("direct"), plain, "agent")
    assert not sends_now(account("drafts"), plain, "agent")
    assert not sends_now(account("approval"), plain, "agent")
    assert sends_now(account("approval", ["@x.org"]), plain, "agent")
    assert sends_now(account("drafts"), plain, "user")
    assert not sends_now(account("off"), plain, "user")
    assert not sends_now(account("off"), plain, "agent")


def prepared(*to: str) -> Prepared:
    composed = compose(message(to, bcc=recipient_list(["hidden@x.org"])))
    return Prepared(composed, 2)


@pytest.mark.parametrize(
    ("mode", "says"), [("drafts", "send mode is drafts"), ("approval", "auto-approved")]
)
def test_a_draft_the_mode_would_only_draft_again_is_refused_for_an_agent(
    make_gate: Callable[..., Gate], mode: str, says: str
) -> None:
    gate = make_gate()
    with pytest.raises(MailError, match=says) as raised:
        gate.run(account(mode), message(["a@x.org"]), prepared=prepared("a@x.org"))
    assert "The draft stays." in raised.value.message
    assert gate.connections.appended == []
    assert gate.smtp.calls == []
    assert [(i.delivery, i.attachments) for i in gate.items] == [("refused", 2)]


def test_a_person_sends_a_draft_in_drafts_mode(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate()
    draft = prepared("a@x.org")
    gate.run(account("drafts"), message(["a@x.org"]), by="user", prepared=draft)
    [(_id, _envelope, raw)] = gate.smtp.calls
    assert raw == draft.composed.wire
    assert gate.connections.appended[0][1] == draft.composed.stored
    assert gate.items[0].message_id == draft.composed.message_id


def test_a_prepared_message_goes_only_to_the_recipients_that_were_checked(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate()
    smuggled = prepared("elsewhere@evil.org")
    gate.run(
        account("approval", ["@x.org"]),
        message(["a@x.org"], bcc=recipient_list(["b@x.org"])),
        prepared=smuggled,
    )
    [(_id, envelope, _raw)] = gate.smtp.calls
    assert envelope == Envelope("me@example.test", ("a@x.org", "b@x.org"))


def test_a_message_sent_never_carries_the_draft_marker(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate()
    gate.run(account(), replace(message(["a@x.org"]), draft=True))
    assert b"X-Mailroom-Draft" not in gate.smtp.calls[0][2]
    assert b"X-Mailroom-Draft" not in gate.connections.appended[0][1]


# Stubbed reads: imaplib rewrites line endings on APPEND, so `seed` cannot store these on Dovecot.
def stored_as(
    monkeypatch: pytest.MonkeyPatch,
    source: bytes,
    sender: tuple[Mailbox, ...] = (Mailbox("me@example.test"),),
) -> Draft:
    draft = Draft(
        ref=MessageRef("acc", "Drafts", 1, 5),
        own=False,
        message_id="<id@example.test>",
        sender=sender,
        to=(Mailbox("a@x.org"),),
    )
    monkeypatch.setattr(gate_module, "read_draft", lambda *_args: draft)
    monkeypatch.setattr(gate_module, "draft_source", lambda *_args: source)
    return draft


@pytest.mark.parametrize(
    "source",
    [
        (
            b"From: me@example.test\r\nTo: a@x.org\rBcc: secret@y.org\rX-Mailroom-Draft: 1\r\n"
            b"Subject: s\r\n\r\nbody\r\n"
        ),
        (
            b"From: me@example.test\rTo: a@x.org\rBcc: secret@y.org\rX-Mailroom-Draft: 1\r"
            b"Subject: s\r\rbody\r"
        ),
        (
            b"From: me@example.test\r\nTo: a@x.org\rBcc: secret@y.org,\r more@y.org,\r\n"
            b"\tlast@y.org\rX-Mailroom-Draft: 1;\r answers=abc\r\nSubject: s\r\n\r\nbody\r\n"
        ),
        (
            b"From: me@example.test\r\r\nTo: a@x.org\r\r\nBcc: secret@y.org\r\r\n"
            b"X-Mailroom-Draft: 1\r\r\nSubject: s\r\r\n\r\r\nbody\r\r\n"
        ),
        (
            b"From: me@example.test\n\rTo: a@x.org\n\rBcc: secret@y.org\n\r"
            b"X-Mailroom-Draft: 1\n\rSubject: s\n\r\n\rbody\n\r"
        ),
        b"From: me@example.test\rFrom: boss@x.org\r\nTo: a@x.org\r\nSubject: s\r\n\r\nbody\r\n",
        (
            b"From: me@example.test\r\nTo: a@x.org\rMessage-ID: <other@x.org>\r\n"
            b"Subject: s\r\n\r\nbody\r\n"
        ),
        b"From: me@example.test\r\nTo: a@x.org\rCc: hidden@y.org\r\nSubject: s\r\n\r\nbody\r\n",
        b"From: me@example.test\r\nTo: a@x.org\r\nSubject: s\r\r\n\r\nbody\r\n",
        b"From: me@example.test\r\nTo: a@x.org\r\nSubject: s\r",
    ],
    ids=[
        "bare CR",
        "CR only",
        "folded Bcc",
        "doubled CR",
        "LF CR",
        "second From",
        "Message-ID",
        "Cc",
        "before the blank line",
        "no blank line",
    ],
)
def test_a_bare_cr_in_a_stored_draft_s_headers_is_refused_before_any_claim(
    make_gate: Callable[..., Gate], monkeypatch: pytest.MonkeyPatch, source: bytes
) -> None:
    draft = stored_as(monkeypatch, source)
    gate = make_gate()

    def send_it() -> None:
        ready = prepare_draft(gate.deps.connections, account(), draft.ref, gate.deps.limits)
        gate.run(account(), ready.message, by="user", prepared=ready.prepared, key="stored")

    with pytest.raises(MailError) as refused:
        send_it()

    assert refused.value.code == "invalid_argument"
    assert "saving it again" in refused.value.message
    assert gate.smtp.calls == []
    assert gate.log.send_state_by_key("stored") is None


ME_AND_BOSS = (Mailbox("me@example.test"), Mailbox("boss@x.org"))


@pytest.mark.parametrize(
    ("source", "sender"),
    [
        (b"From: me@example.test, boss@x.org\r\nTo: a@x.org\r\n\r\nbody\r\n", ME_AND_BOSS),
        (
            b"From: me@example.test\r\nFrom: boss@x.org\r\nTo: a@x.org\r\n\r\nbody\r\n",
            (Mailbox("me@example.test"),),
        ),
        (
            b"From: boss@x.org\r\nFrom : me@example.test\r\nTo: a@x.org\r\n\r\nbody\r\n",
            (Mailbox("me@example.test"),),
        ),
        (b"From: boss:;\r\nTo: a@x.org\r\n\r\nbody\r\n", ()),
    ],
    ids=["two addresses", "two fields", "a field with a space", "no address"],
)
def test_a_stored_draft_goes_out_only_with_the_one_from_address_that_was_checked(
    make_gate: Callable[..., Gate],
    monkeypatch: pytest.MonkeyPatch,
    source: bytes,
    sender: tuple[Mailbox, ...],
) -> None:
    draft = stored_as(monkeypatch, source, sender)
    gate = make_gate()

    with pytest.raises(MailError) as refused:
        prepare_draft(gate.deps.connections, account(), draft.ref, gate.deps.limits)

    assert refused.value.code == "invalid_argument"
    assert "saving it again" in refused.value.message


def test_a_from_line_in_a_stored_draft_s_body_is_not_a_second_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"From: me@example.test\r\nTo: a@x.org\r\n\r\nFrom: boss@x.org\r\n"
    draft = stored_as(monkeypatch, source)
    connections = cast("MailConnections", StubConnections())

    composed = prepare_draft(connections, account(), draft.ref, Limits()).prepared.composed

    assert composed.wire.endswith(b"\r\n\r\nFrom: boss@x.org\r\n")


CRLF_HEADERS = (
    b"From: me@example.test\r\nTo: a@x.org\r\nBcc: secret@y.org\r\n"
    b"X-Mailroom-Draft: 1\r\nSubject: s"
)


@pytest.mark.parametrize(
    ("source", "body"),
    [
        (
            CRLF_HEADERS + b"\r\n\r\none\rtwo\n.three\r\n\rfour\r\r\n",
            b"one\r\ntwo\r\n.three\r\n\r\nfour\r\n",
        ),
        (CRLF_HEADERS.replace(b"\r", b"") + b"\n\none\rtwo\n", b"one\r\ntwo\r\n"),
        (CRLF_HEADERS + b"\r\n\none\rtwo\r\n", b"one\r\ntwo\r\n"),
        (CRLF_HEADERS.replace(b"\r", b"") + b"\n\r\none\rtwo\n", b"one\r\ntwo\r\n"),
    ],
    ids=["CRLF headers", "LF headers", "CR before the blank line's LF", "LF then CRLF"],
)
def test_a_stored_draft_s_body_goes_out_with_its_lines_ending_in_crlf(
    monkeypatch: pytest.MonkeyPatch, source: bytes, body: bytes
) -> None:
    draft = stored_as(monkeypatch, source)
    connections = cast("MailConnections", StubConnections())
    composed = prepare_draft(connections, account(), draft.ref, Limits()).prepared.composed

    with SmtpStub() as stub:
        on(stub, NO_TIMEOUT).send("acc", composed.envelope, composed.wire)

    assert stub.messages == [b"From: me@example.test\r\nTo: a@x.org\r\nSubject: s\r\n\r\n" + body]
    assert b"Bcc: secret@y.org" in composed.stored
    assert b"X-Mailroom-Draft" not in composed.stored


# The token.


def test_only_admit_makes_an_admitted(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate()
    admitted = gate.admit(account(), message(["a@x.org"]), by="user")
    assert isinstance(admitted, Admitted)
    with pytest.raises(TypeError, match="only admit"):
        Admitted(object(), admitted.account, admitted.composed, admitted.send, admitted.item)
    with pytest.raises(TypeError):
        replace(admitted)
    with pytest.raises(TypeError):
        copy.copy(admitted)
    with pytest.raises(TypeError):
        copy.deepcopy(admitted)


def test_an_admitted_is_good_for_one_delivery(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate()
    admitted = gate.admit(account(), message(["a@x.org"]), by="user")
    deliver(gate.deps, admitted, gate.items.append)
    with pytest.raises(ValueError, match="delivered already"):
        deliver(gate.deps, admitted, gate.items.append)
    assert len(gate.smtp.calls) == 1


def test_the_phases_taken_apart_do_what_send_through_gate_does(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate()
    admitted = gate.admit(account(), message(["a@x.org"]), by="agent", key="k")
    assert gate.items == []
    assert gate.smtp.calls == []
    assert cast("SendRecord", gate.log.send_state(admitted.send.id)).state == "sending"
    sent = deliver(gate.deps, admitted, gate.items.append)
    assert cast("SendRecord", gate.log.send_state(admitted.send.id)).state == "sent"
    assert file_sent(gate.deps, sent, gate.items.append) is True
    assert [item.delivery for item in gate.items] == ["sent"]
    # What followed is the host's to write, once it is all over.
    record = cast("SendRecord", gate.log.send_state(admitted.send.id))
    assert record.result == {"rejected": []}


def test_a_filing_that_outlives_the_host_leaves_its_final_result_alone(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate()
    admitted = gate.admit(account(), message(["a@x.org"]), by="user", key="k")
    sent = deliver(gate.deps, admitted, gate.items.append)
    final = {"rejected": [], "filed": False, "notes": ["Sent, but not filed in Sent."]}
    assert gate.log.finish_send(admitted.send, "sent", final)

    assert file_sent(gate.deps, sent, gate.items.append) is True

    assert cast("SendRecord", gate.log.send_state(admitted.send.id)).result == final


# How an attempt ends.


def state_after(gate: Gate) -> SendRecord:
    admitted = gate.admit(account(), message(["a@x.org"]), by="user")
    with pytest.raises((MailError, RuntimeError)):
        deliver(gate.deps, admitted, gate.items.append)
    return cast("SendRecord", gate.log.send_state(admitted.send.id))


@pytest.mark.parametrize(
    "code", ["recipient_refused", "server_error", "unreachable", "timeout", "auth_failed"]
)
def test_a_definite_refusal_ends_failed(make_gate: Callable[..., Gate], code: Any) -> None:
    record = state_after(make_gate(smtp=raises(MailError(code))))
    assert (record.state, record.result) == ("failed", {"code": code})


def test_delivery_unknown_ends_unknown(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate(smtp=raises(MailError("delivery_unknown")))
    record = state_after(gate)
    assert (record.state, record.result) == ("unknown", {"code": "delivery_unknown"})
    assert [(i.delivery, i.result) for i in gate.items] == [("attempted", "delivery_unknown")]
    assert gate.connections.appended == []


def test_an_error_nobody_classified_ends_unknown(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate(smtp=raises(RuntimeError("a bug")))
    assert state_after(gate).state == "unknown"
    assert [i.result for i in gate.items] == ["internal"]


def test_a_send_counts_against_the_limit_however_it_ended(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate(per_hour=1, smtp=raises(MailError("server_error")))
    with pytest.raises(MailError):
        gate.run(account(), message(["a@x.org"]))
    with pytest.raises(MailError) as raised:
        gate.run(account(), message(["a@x.org"]))
    assert raised.value.code == "rate_limited"


def test_an_attempt_queued_past_the_ceiling_is_not_sent(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate()
    admitted = claimed_seconds_ago(gate, 400, "late")
    with pytest.raises(MailError) as raised:
        deliver(gate.deps, admitted, gate.items.append)
    assert raised.value.code == "timeout"
    assert gate.smtp.calls == []
    assert [(i.delivery, i.result) for i in gate.items] == [("attempted", "timeout")]
    assert cast("SendRecord", gate.log.send_state_by_key("late")).state == "failed"


def claimed_seconds_ago(gate: Gate, seconds: float, key: str) -> Admitted:
    # `admit` writes down the clock's time, so the clock moves instead.
    past = datetime.now(UTC) - timedelta(seconds=seconds)
    original = gate.log.claim_send
    gate.log.claim_send = lambda *a, **o: original(*a, **o, now=past)  # type: ignore[method-assign]
    return cast("Admitted", gate.admit(account(), message(["a@x.org"]), by="user", key=key))


def on(stub: SmtpStub, timeouts: SmtpTimeouts) -> SmtpSender:
    served = account().model_copy(
        update={"smtp": account().smtp.model_copy(update={"host": "localhost", "port": stub.port})}
    )
    return SmtpSender(
        lambda _id: served,
        lambda _id: "password",
        ssl_context=SmtpStub.trusted_context(),
        timeouts=timeouts,
    )


NO_TIMEOUT = SmtpTimeouts(connect=30, command=30, data=30, watchdog=30)


@pytest.mark.parametrize("transfer", [None, Transfer()], ids=["its own", "the host's"])
def test_an_attempt_ends_at_its_claims_deadline_however_late_it_started(
    make_gate: Callable[..., Gate], transfer: Transfer | None
) -> None:
    gate = make_gate()
    admitted = claimed_seconds_ago(gate, SEND_CEILING.total_seconds() - 1, "late")
    started = time.monotonic()
    with SmtpStub(end_reply=STALL) as stub:
        deps = replace(gate.deps, smtp=on(stub, NO_TIMEOUT))
        with pytest.raises(MailError) as raised:
            deliver(deps, admitted, gate.items.append, transfer)
    assert raised.value.code == "delivery_unknown"
    assert time.monotonic() - started < 10
    assert transfer is None or transfer.aborted
    record = cast("SendRecord", gate.log.send_state_by_key("late"))
    assert (record.state, record.result) == ("unknown", {"code": "delivery_unknown"})


def test_a_recent_attempt_is_not_held_to_a_nearly_spent_deadline(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate()
    admitted = claimed_seconds_ago(gate, 1, "fresh")
    with SmtpStub() as stub:
        sent = deliver(replace(gate.deps, smtp=on(stub, NO_TIMEOUT)), admitted, gate.items.append)
    assert sent.rejected == ()
    assert len(stub.messages) == 1


class BrokenLog:
    """Claims through `log`, and cannot record outcomes."""

    def __init__(self, log: SendLog) -> None:
        self._log = log

    def claim_send(self, *args: Any, **options: Any) -> Any:
        return self._log.claim_send(*args, **options)

    def finish_send(self, *_args: Any) -> bool:
        raise OSError("the database went away")


def test_a_lost_database_does_not_undo_a_delivery(
    make_gate: Callable[..., Gate], core_engine: Engine
) -> None:
    gate = make_gate(log=BrokenLog(AccountStore(core_engine, "user-1")))
    result = gate.run(account(), message(["a@x.org"]))
    assert (result.outcome, result.filed) == ("sent", True)
    assert [i.delivery for i in gate.items] == ["sent"]


def test_a_lost_database_does_not_hide_why_a_send_failed(
    make_gate: Callable[..., Gate], core_engine: Engine
) -> None:
    gate = make_gate(
        log=BrokenLog(AccountStore(core_engine, "user-1")), smtp=raises(MailError("server_error"))
    )
    with pytest.raises(MailError) as raised:
        gate.run(account(), message(["a@x.org"]))
    assert raised.value.code == "server_error"


# After delivery: nothing turns it into a failure.


def test_a_failed_filing_only_says_so(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate(append_fails=True)
    result = gate.run(account(), message(["a@x.org"]), key="k")
    assert (result.outcome, result.filed) == ("sent", False)
    [item] = gate.items
    assert (item.delivery, item.filed, item.result) == ("sent", False, "ok")
    record = cast("SendRecord", gate.log.send_state_by_key("k"))
    assert (record.state, record.result) == ("sent", {"rejected": [], "filed": False})


def test_a_capabilities_failure_after_delivery_keeps_the_sent_item(
    make_gate: Callable[..., Gate],
) -> None:
    gate = make_gate(capabilities_fail=True)
    result = gate.run(account(), message(["a@x.org"]))
    assert (result.outcome, result.filed) == ("sent", False)
    assert [(i.delivery, i.filed) for i in gate.items] == [("sent", False)]


def stored_draft(answers: MessageRef | None = None) -> Draft:
    return Draft(ref=MessageRef("acc", "Drafts", 1, 5), own=True, answers=answers)


class FailingRemoval(StubConnections):
    def special_folder(self, _account: str, role: str) -> SimpleNamespace:
        raise MailError("mailbox_missing")


def test_a_draft_that_will_not_go_or_an_original_that_will_not_mark_are_only_notes() -> None:
    items: list[AuditItem] = []
    connections = cast("MailConnections", FailingRemoval())
    answered = MessageRef("acc", "INBOX", 1, 9)
    notes = settle_draft(connections, stored_draft(answered), items.append, filed=True)
    assert notes == (ORIGINAL_NOT_MARKED, DRAFT_NOT_REMOVED)
    assert [(i.folder, i.flag, i.removed, i.result) for i in items] == [
        ("INBOX", "\\Answered", None, "server_error"),
        ("Drafts", None, "failed", "mailbox_missing"),
    ]


def test_a_bug_while_removing_a_delivered_draft_is_only_a_note() -> None:
    class Broken(StubConnections):
        def special_folder(self, _account: str, role: str) -> SimpleNamespace:
            raise RuntimeError("a bug")

    items: list[AuditItem] = []
    notes = settle_draft(
        cast("MailConnections", Broken()), stored_draft(), items.append, filed=True
    )
    assert notes == (DRAFT_NOT_REMOVED,)
    assert [(i.removed, i.result) for i in items] == [("failed", "internal")]


def test_a_draft_that_was_not_filed_stays_as_the_only_copy() -> None:
    items: list[AuditItem] = []
    # Removing it would fail: nothing must try.
    connections = cast("MailConnections", FailingRemoval())
    answered = MessageRef("acc", "INBOX", 1, 9)
    notes = settle_draft(connections, stored_draft(answered), items.append, filed=False)
    assert notes == (ORIGINAL_NOT_MARKED, DRAFT_KEPT)
    assert [(i.flag, i.removed) for i in items] == [("\\Answered", None)]


def test_the_notes_have_stable_codes_a_host_can_word() -> None:
    assert [note.code for note in (DRAFT_NOT_REMOVED, DRAFT_KEPT, ORIGINAL_NOT_MARKED)] == [
        "draft_not_removed",
        "draft_kept",
        "original_not_marked",
    ]


def test_a_reply_whose_original_will_not_mark_is_only_a_note() -> None:
    items: list[AuditItem] = []
    ref = MessageRef("acc", "INBOX", 1, 9)
    note = mark_original(cast("MailConnections", StubConnections()), ref, items.append)
    assert note == ORIGINAL_NOT_MARKED
    assert [i.flag for i in items] == ["\\Answered"]


def test_a_draft_that_will_not_save_is_audited(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate(append_fails=True)
    with pytest.raises(MailError):
        gate.run(account("drafts"), message(["a@x.org"]))
    assert [(i.delivery, i.result) for i in gate.items] == [("drafted", "server_error")]


# Retries by key.


def test_a_sent_key_replays_its_result_without_sending(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate(smtp=lambda: Delivery(("bo@x.org",)))
    first = gate.run(account(), message(["a@x.org", "bo@x.org"]), key="k")
    again = gate.run(account(), message(["a@x.org", "bo@x.org"]), key="k")
    assert len(gate.smtp.calls) == 1
    assert (again.outcome, again.send_id, again.filed, again.rejected) == (
        "sent",
        first.send_id,
        True,
        ("bo@x.org",),
    )
    assert gate.items[-1].delivery == "repeated"


def test_a_key_still_sending_answers_the_same_id(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate()
    admitted = gate.admit(account(), message(["a@x.org"]), by="user", key="k")
    earlier = gate.admit(account(), message(["a@x.org"]), by="user", key="k")
    assert isinstance(earlier, Earlier)
    assert earlier.send.id == admitted.send.id
    result = gate.run(account(), message(["a@x.org"]), by="user", key="k")
    assert (result.outcome, result.send_id) == ("sending", admitted.send.id)


def test_a_failed_key_can_be_sent_again(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate(smtp=in_turn(raises(MailError("server_error")), Delivery))
    with pytest.raises(MailError):
        gate.run(account(), message(["a@x.org"]), key="k")
    assert gate.run(account(), message(["a@x.org"]), key="k").outcome == "sent"
    assert len(gate.smtp.calls) == 2


def test_an_unknown_key_waits_for_a_person_to_confirm(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate(smtp=in_turn(raises(MailError("delivery_unknown")), Delivery))
    with pytest.raises(MailError):
        gate.run(account(), message(["a@x.org"]), by="user", key="k")
    with pytest.raises(MailError) as raised:
        gate.run(account(), message(["a@x.org"]), by="user", key="k")
    assert raised.value.code == "delivery_unknown"
    assert len(gate.smtp.calls) == 1
    assert (gate.items[-1].delivery, gate.items[-1].result) == ("refused", "delivery_unknown")
    result = gate.run(account(), message(["a@x.org"]), by="user", key="k", confirm_resend=True)
    assert result.outcome == "sent"
    assert len(gate.smtp.calls) == 2


def test_a_send_without_a_key_is_never_a_retry(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate()
    gate.run(account(), message(["a@x.org"]))
    gate.run(account(), message(["a@x.org"]))
    assert len(gate.smtp.calls) == 2


def test_drafted_answers_with_its_ref(make_gate: Callable[..., Gate]) -> None:
    gate = make_gate()
    assert gate.admit(account("drafts"), message(["a@x.org"]), by="agent") == Drafted(None)
