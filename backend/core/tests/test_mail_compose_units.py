"""`src/mail/compose.test.ts`, the input caps of `src/mcp/tools/outgoing.ts`, and what the audit
records of an outgoing message."""

import email
import json
import re
import time
from dataclasses import replace
from datetime import UTC, datetime
from email import policy
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from mailroom_core.config import AccountConfig, Limits
from mailroom_core.mail import (
    DRAFT_HEADER,
    MAX_ADDRESSES,
    MAX_ORIGINAL_BYTES,
    MAX_SUBJECT_CHARS,
    MAX_TEXT_CHARS,
    MAX_UPLOADS,
    Addressed,
    AuditItem,
    Mailbox,
    MailError,
    MessageInput,
    MessageRef,
    Original,
    OutgoingAttachment,
    attachment_cap,
    audit_outcome,
    check_fields,
    check_recipient_count,
    check_uploads,
    compose,
    deliverable_address,
    draft_answers,
    envelope_mailboxes,
    format_ref,
    forward_subject,
    forwarded_original,
    from_identity,
    is_own_draft,
    mailbox_text,
    matches_rule,
    new_message_id,
    outgoing_item,
    parse_ref,
    quoted_original,
    recipient_list,
    reply_recipients,
    reply_subject,
    threading,
    without_headers,
)
from mailroom_core.mail._mime import header_values
from mailroom_core.mail.compose import crlf_lines

if TYPE_CHECKING:
    from email.message import EmailMessage

ACCOUNT = AccountConfig.model_validate(
    {
        "id": "acc",
        "login": "me",
        "send_mode": "direct",
        "auth": {"type": "password"},
        "imap": {"host": "127.0.0.1", "port": 993, "tls": "implicit"},
        "smtp": {"host": "127.0.0.1", "port": 465, "tls": "implicit"},
        "identities": [
            {"address": "me@example.test", "name": "Me", "default": True},
            {"address": "alias@example.test", "default": False},
        ],
    }
)
REF = MessageRef("acc", "INBOX", 1, 1)


def headers_of(raw: bytes) -> list[str]:
    """Header lines of a raw message, unfolded."""
    head = raw.decode().split("\r\n\r\n")[0]
    return re.sub(r"\r\n[ \t]+", " ", head).split("\r\n")


def parsed(raw: bytes) -> EmailMessage:
    return cast("EmailMessage", email.message_from_bytes(raw, policy=policy.default))


def named(*fields: str) -> tuple[Mailbox, ...]:
    return recipient_list(fields)


class TestRecipientList:
    def test_accepts_bare_and_named_addresses_lowercasing_the_address(self) -> None:
        assert recipient_list(["Ana@Example.org", "Bo Rossi <bo@example.org>"]) == (
            Mailbox("ana@example.org"),
            Mailbox("bo@example.org", "Bo Rossi"),
        )

    def test_takes_a_quoted_name_and_trims(self) -> None:
        assert named('  "Rossi, Bo"  <bo@example.org> ') == (
            Mailbox("bo@example.org", "Rossi, Bo"),
        )

    def test_punycodes_the_domain_so_header_and_envelope_agree(self) -> None:
        assert named("Ana <ana@Bücher.de>") == (Mailbox("ana@xn--bcher-kva.de", "Ana"),)

    @pytest.mark.parametrize(
        "value",
        [
            "not an address",
            "a@b",
            "ana@example.org, bo@example.org",
            "a@x.com b@y.com",
            "Ana <a@x.com> <b@y.com>",
            '"boss@corp.com" <x@evil.com>',
            "friends: ana@example.org;",
            '"victim@bank.com"@evil.com',
            "é@x.com",
            "ana@example.org\u200b",
            "Ana <ana@example.org>\r\nBcc: victim@example.org",
            "ana@example.org\n",
            "Ana <ana@example.org>\n",
            "ana@example.org\u2028",
            "ana@-bad.org",
            "ana@exa_mple.org",
            ".ana@example.org",
            "ana..bo@example.org",
            "@example.org",
            "ana@",
            "a" * 2000 + " <ana@example.org>",
            'Ana "Boss" <a@x.org>',
            "a" * 250 + "@x.org",
        ],
    )
    def test_refuses_naming_it(self, value: str) -> None:
        with pytest.raises(MailError, match="Not an email address") as raised:
            recipient_list([value])
        assert raised.value.code == "invalid_argument"
        assert "\r" not in str(raised.value)
        assert "\n" not in str(raised.value)

    @pytest.mark.parametrize("domain", ["straße.de", "ας.gr"])
    def test_refuses_a_domain_python_would_turn_into_another(self, domain: str) -> None:
        with pytest.raises(MailError, match="Not an email address"):
            deliverable_address(f"ana@{domain}")

    def test_takes_the_longest_address_a_path_can_carry(self) -> None:
        address = "a" * 248 + "@x.org"
        assert deliverable_address(address) == address

    def test_measures_the_domain_as_it_is_written_after_punycoding(self) -> None:
        domain = ".".join(["bücher"] * 35)
        assert len(f"a@{domain}.de") <= 254 < len(f"a@{domain.encode('idna').decode()}.de")
        with pytest.raises(MailError, match="Not an email address"):
            deliverable_address(f"a@{domain}.de")


class TestLimitsAndRules:
    def test_caps_the_recipients_of_one_message_and_needs_at_least_one(self) -> None:
        three = named("a@x.org", "b@x.org", "c@x.org")
        check_recipient_count(three, 3)
        with pytest.raises(MailError, match="over the limit of 2"):
            check_recipient_count(three, 2)
        with pytest.raises(MailError, match="at least one"):
            check_recipient_count((), 2)

    def test_matches_auto_approve_rules_by_exact_address_or_exact_domain(self) -> None:
        assert matches_rule("Ana@TEAM.example", ["@team.example"])
        assert not matches_rule("ana@notteam.example", ["@team.example"])
        assert not matches_rule("ana@sub.team.example", ["@team.example"])
        assert matches_rule("evil@attacker.org@team.example", ["@team.example"])
        assert matches_rule("bo@x.org", ["BO@x.org"])
        assert matches_rule("ana@xn--bcher-kva.de", ["@bücher.de"])
        assert not matches_rule("ana@x.org", [])


class TestFromIdentity:
    def test_accepts_an_explicit_identity_and_refuses_any_other(self) -> None:
        assert from_identity(ACCOUNT, "Whoever <ALIAS@example.test>") == Mailbox(
            "alias@example.test"
        )
        with pytest.raises(MailError, match=r"Use one of: me@example\.test, alias@example\.test"):
            from_identity(ACCOUNT, "boss@example.test")

    def test_answers_from_the_identity_the_original_was_sent_to_else_the_default(self) -> None:
        cc = Addressed(cc=("ALIAS@example.test",))
        assert from_identity(ACCOUNT, None, cc).address == "alias@example.test"
        delivered = Addressed(to=("list@x.org",), delivered=("alias@example.test",))
        assert from_identity(ACCOUNT, None, delivered).address == "alias@example.test"
        assert from_identity(ACCOUNT, None, Addressed(to=("list@x.org",))) == Mailbox(
            "me@example.test", "Me"
        )
        assert from_identity(ACCOUNT) == Mailbox("me@example.test", "Me")


BASE = MessageInput(
    sender=Mailbox("me@example.test", "Me"),
    to=named("ana@example.org"),
    bcc=named("hidden@example.org"),
    subject="Hello\r\nBcc: victim@example.org",
    text="Line one\nLine two",
)


class TestCompose:
    def test_keeps_one_header_per_field_whatever_the_subject_holds(self) -> None:
        headers = headers_of(compose(BASE).stored)
        assert [h for h in headers if h.lower().startswith("subject:")] == [
            "Subject: Hello Bcc: victim@example.org"
        ]
        assert [h for h in headers if h.lower().startswith("bcc:")] == ["Bcc: hidden@example.org"]

    def test_files_bcc_in_the_stored_copy_only_with_every_recipient_in_the_envelope(self) -> None:
        composed = compose(BASE)
        assert not any(h.lower().startswith("bcc:") for h in headers_of(composed.wire))
        assert composed.envelope.sender == "me@example.test"
        assert composed.envelope.recipients == ("ana@example.org", "hidden@example.org")
        assert re.fullmatch(r"<[\w-]+@example\.test>", composed.message_id)
        assert f"Message-ID: {composed.message_id}" in headers_of(composed.wire)

    def test_threads_replies_drops_ids_that_are_not_message_ids_and_marks_drafts(self) -> None:
        stored = compose(
            replace(
                BASE,
                in_reply_to="<a@x.org>",
                references=("<root@x.org>", "garbage\r\nX-Evil: 1", "<a@x.org>", "<é@x.org>"),
                draft=True,
                attachments=(OutgoingAttachment("a.txt", "text/plain", b"hi"),),
            )
        ).stored
        headers = headers_of(stored)
        assert "In-Reply-To: <a@x.org>" in headers
        assert "References: <root@x.org> <a@x.org>" in headers
        assert f"{DRAFT_HEADER}: 1" in headers
        assert not any(h.startswith("X-Evil") for h in headers)
        [attachment] = parsed(stored).iter_attachments()
        assert attachment.get_filename() == "a.txt"
        assert attachment.get_content_type() == "text/plain"

    def test_drops_an_in_reply_to_that_is_no_message_id(self) -> None:
        stored = compose(replace(BASE, in_reply_to="<a@x.org>\r\nX-Evil: 1")).stored
        assert not any(h.startswith(("In-Reply-To", "X-Evil")) for h in headers_of(stored))

    def test_never_encodes_a_message_id_however_long(self) -> None:
        long_id = f"<{'r' * 100}@x.org>"
        raw = compose(replace(BASE, references=("<a@x.org>", long_id, "<b@x.org>"))).wire
        assert parsed(raw)["References"] == f"<a@x.org> {long_id} <b@x.org>"
        assert b"=?" not in raw.split(b"\r\n\r\n")[0]
        assert all(len(line) <= 998 for line in raw.split(b"\r\n"))
        too_long = f"<{'r' * 1000}@x.org>"
        assert b"References" not in compose(replace(BASE, references=(too_long,))).wire

    def test_takes_a_stable_message_id_and_refuses_a_malformed_one(self) -> None:
        stable = new_message_id("me@example.test", "7f3a")
        assert stable == "<7f3a@example.test>"
        assert compose(BASE, stable).message_id == stable
        assert compose(replace(BASE, text="v2"), stable).message_id == stable
        for bad in ("nope", "<a b@x.org>", "<a@x.org>\r\nBcc: x@y.org", new_message_id("a@x", "<")):
            with pytest.raises(MailError, match="Not a Message-ID"):
                compose(BASE, bad)

    def test_encodes_what_is_not_ascii_and_reads_back_as_written(self) -> None:
        message = replace(
            BASE,
            sender=Mailbox("me@example.test", "Crème, Brûlée"),
            to=named('"Rossi, Bo" <bo@example.org>', "Müller <m@example.org>"),
            subject="Perché no? 😀 " + "lunga " * 60,
            text="Più testo\n.punto\nFrom here\ntrailing  \n",
        )
        raw = compose(message).wire
        head = raw.split(b"\r\n\r\n")[0]
        assert head.isascii()
        assert all(len(line) <= 78 for line in head.split(b"\r\n"))
        reread = parsed(raw)
        assert reread["Subject"] == ("Perché no? 😀 " + "lunga " * 60).strip()
        assert reread["From"] == '"Crème, Brûlée" <me@example.test>'
        assert [a.addr_spec for a in reread["To"].addresses] == ["bo@example.org", "m@example.org"]
        assert reread.get_content().replace("\r\n", "\n") == message.text
        assert reread["Content-Transfer-Encoding"] == "quoted-printable"

    @pytest.mark.parametrize(
        "evil",
        [
            "=?utf-8?q?a=0D=0ABcc:_v@y.org=0D=0A=0D=0Abody?=",
            "=?utf-8?b?YQ0KQmNjOiB2QHkub3Jn?=",
            "x =?utf-8?q?a=0D=0ABcc:_v@y.org?= y",
        ],
    )
    def test_no_encoded_word_turns_into_a_header(self, evil: str) -> None:
        raw = compose(
            replace(
                BASE,
                sender=Mailbox("me@example.test", evil),
                to=(Mailbox("a@x.org", evil),),
                subject=evil,
                attachments=(OutgoingAttachment(evil, "text/plain", b"x"),),
            )
        ).wire
        assert not any(h.lower().startswith("bcc:") for h in headers_of(raw))
        assert b"\r\nBcc" not in raw
        assert parsed(raw)["Subject"] == evil
        assert parsed(raw)["To"].addresses[0].display_name == evil
        [attachment] = parsed(raw).iter_attachments()
        assert "\n" not in (attachment.get_filename() or "")

    def test_encodes_a_word_too_long_for_a_line(self) -> None:
        subject = "see " + "x" * 200 + " there"
        raw = compose(replace(BASE, subject=subject)).wire
        assert parsed(raw)["Subject"] == subject
        assert all(len(line) <= 78 for line in raw.split(b"\r\n\r\n")[0].split(b"\r\n"))

    def test_keeps_quoted_printable_lines_within_rfc_2045(self) -> None:
        raw = compose(replace(BASE, text="é" + "x" * 500)).wire
        body = raw.split(b"\r\n\r\n", 1)[1]
        assert b"=C3=A9" in body
        assert all(len(line) <= 76 for line in body.split(b"\r\n"))

    def test_writes_crlf_lines_only(self) -> None:
        raw = compose(
            replace(
                BASE,
                text="a\nb\rc\r\nd",
                attachments=(OutgoingAttachment("a.bin", "application/x-thing", bytes(500)),),
            )
        ).stored
        assert re.search(rb"(?<!\r)\n", raw) is None
        assert re.search(rb"\r(?!\n)", raw) is None

    def test_survives_lone_surrogates_from_json(self) -> None:
        raw = compose(
            replace(
                BASE,
                subject="a\ud800b",
                text="c\udc80d",
                to=(Mailbox("ana@example.org", "e\ud800f"),),
                attachments=(OutgoingAttachment("g\ud800.txt", "text/plain", b"x"),),
            )
        ).wire
        assert parsed(raw)["Subject"] == "a�b"
        assert "c�d" in parsed(raw).get_body().get_content()  # type: ignore[union-attr]

    def test_keeps_attachments_byte_for_byte_with_any_name(self) -> None:
        data = bytes(range(256)) * 40
        names = ['r"é; x.txt"', "a" * 300, "\r\nX-Evil: 1.pdf", "", "file name; with=things.txt"]
        raw = compose(
            replace(
                BASE,
                attachments=tuple(
                    OutgoingAttachment(name, "application/pdf", data) for name in names
                ),
            )
        ).wire
        attachments = list(parsed(raw).iter_attachments())
        assert [a.get_content() for a in attachments] == [data] * 5
        filenames = [a.get_filename() for a in attachments]
        assert filenames[0] == 'r"é; x.txt"'
        assert len(filenames[1] or "") == 200
        assert filenames[2] == "X-Evil: 1.pdf"
        assert filenames[3] == "attachment"
        assert filenames[4] == "file name; with=things.txt"
        assert not any(h.startswith("X-Evil") for h in headers_of(raw))
        assert b"\r\nX-Evil" not in raw

    @pytest.mark.parametrize(
        ("declared", "sent"),
        [
            ("application/pdf", "application/pdf"),
            ("Text/CSV; charset=utf-8", "text/csv"),
            ("image/png\r\nX-Evil: 1", "application/octet-stream"),
            ("nonsense", "application/octet-stream"),
            ("multipart/mixed", "application/octet-stream"),
            ("message/rfc822", "application/octet-stream"),
        ],
    )
    def test_labels_an_attachment_only_with_a_type_it_can_carry(
        self, declared: str, sent: str
    ) -> None:
        raw = compose(
            replace(BASE, attachments=(OutgoingAttachment("a", declared, b"bytes"),))
        ).wire
        [attachment] = parsed(raw).iter_attachments()
        assert attachment.get_content_type() == sent
        assert b"X-Evil" not in raw

    def test_refuses_a_recipient_that_is_not_deliverable_wherever_it_came_from(self) -> None:
        for address in ("a b@x.org", "é@x.org", "a@x.org\r\nBcc: v@y.org"):
            with pytest.raises(MailError, match="Not an email address"):
                compose(replace(BASE, cc=(Mailbox(address),)))

    def test_cleans_a_name_that_came_from_elsewhere(self) -> None:
        wire = compose(replace(BASE, to=(Mailbox("a@x.org", "Ana\r\nBcc: v@y.org"),))).wire
        assert not any(h.startswith("Bcc") for h in headers_of(wire))
        assert parsed(wire)["To"].addresses[0].display_name == "Ana Bcc: v@y.org"

    def test_leaves_out_empty_fields(self) -> None:
        raw = compose(MessageInput(sender=Mailbox("me@example.test"), subject="", text="")).stored
        names = {h.split(":")[0] for h in headers_of(raw)}
        assert names == {
            "From",
            "Message-ID",
            "Date",
            "MIME-Version",
            "Content-Type",
            "Content-Transfer-Encoding",
        }


class TestDraftMark:
    ANSWERED = format_ref(MessageRef("acc", "INBOX", 7, 42))

    def test_carries_what_a_draft_answers_and_never_leaves_with_the_message(self) -> None:
        composed = compose(replace(BASE, draft=True, answers=self.ANSWERED))
        assert f"{DRAFT_HEADER}: 1; answers={self.ANSWERED}" in headers_of(composed.stored)
        assert is_own_draft(composed.stored)
        assert draft_answers(composed.stored) == parse_ref(self.ANSWERED)
        assert DRAFT_HEADER.encode() not in composed.wire
        assert not is_own_draft(composed.wire)

    def test_folds_a_long_mark_and_still_reads_it(self) -> None:
        long_ref = format_ref(MessageRef("acc", "Archivio/" + "x" * 200, 7, 42))
        stored = compose(replace(BASE, draft=True, answers=long_ref)).stored
        assert f"{DRAFT_HEADER}: 1;\r\n answers=".encode() in stored
        assert draft_answers(stored) == parse_ref(long_ref)

    def test_refuses_an_answers_that_is_no_ref(self) -> None:
        with pytest.raises(MailError, match="Not a message reference"):
            compose(replace(BASE, draft=True, answers="x\r\nBcc: v@y.org"))

    def test_ignores_answers_when_it_is_no_draft(self) -> None:
        composed = compose(replace(BASE, answers="not looked at"))
        assert DRAFT_HEADER.encode() not in composed.stored

    @pytest.mark.parametrize(
        "block",
        [
            f"{DRAFT_HEADER}: 1\r\n\r\n",
            f"{DRAFT_HEADER}: 1",
            "x-mailroom-draft:1\r\n",
            f"Subject: a\r\n{DRAFT_HEADER}: 1; answers={ANSWERED}\r\nTo: b\r\n",
            f"{DRAFT_HEADER}: 1;\r\n answers={ANSWERED}\r\n",
            f"{DRAFT_HEADER}: 1;\tanswers={ANSWERED}\r\n",
        ],
    )
    def test_detects_both_forms(self, block: str) -> None:
        assert is_own_draft(block)
        assert is_own_draft(block.encode())

    @pytest.mark.parametrize(
        "block",
        [
            "",
            "Subject: a\r\n",
            f"Subject: a\r\n {DRAFT_HEADER}: 1\r\n",
            f"X-{DRAFT_HEADER}: 1\r\n",
            f"{DRAFT_HEADER}: 2\r\n",
            f"{DRAFT_HEADER}: 1; other=x\r\n",
        ],
    )
    def test_does_not_take_anything_else_for_a_mark(self, block: str) -> None:
        assert not is_own_draft(block)

    def test_answers_nothing_without_a_readable_ref(self) -> None:
        assert draft_answers(f"{DRAFT_HEADER}: 1\r\n") is None
        assert draft_answers(f"{DRAFT_HEADER}: 1; answers=abc\r\n") is None
        assert draft_answers("Subject: a\r\n") is None


class TestWithoutHeaders:
    def test_drops_the_named_fields_folded_lines_included_leaving_the_body_as_it_was(self) -> None:
        raw = (
            b"From: a@x.org\r\nBcc: b@x.org,\r\n c@x.org\r\nSubject: Hi\r\n"
            b"X-Mailroom-Draft: 1\r\n\r\nBcc: not a header\r\n"
        )
        assert without_headers(raw, ["bcc", DRAFT_HEADER]) == (
            b"From: a@x.org\r\nSubject: Hi\r\n\r\nBcc: not a header\r\n"
        )

    def test_handles_the_last_field_a_bare_newline_and_no_body(self) -> None:
        assert without_headers(b"A: 1\r\nBcc: x\r\n\r\nbody", ["Bcc"]) == b"A: 1\r\n\r\nbody"
        assert without_headers(b"A: 1\nBcc: x\n y\n\nbody\n", ["Bcc"]) == b"A: 1\n\nbody\n"
        assert without_headers(b"Bcc: x\r\nA: 1", ["Bcc"]) == b"A: 1"

    def test_drops_every_occurrence_of_a_field_however_it_is_spelt(self) -> None:
        raw = b"Bcc: a\r\nA: 1\r\nBCC : b\r\n c\r\n\r\nbody"
        assert without_headers(raw, ["Bcc"]) == b"A: 1\r\n\r\nbody"

    def test_leaves_8bit_bytes_alone(self) -> None:
        raw = b"Subject: \xff\r\nBcc: \xfe\r\n\r\n\xfd"
        assert without_headers(raw, ["Bcc"]) == b"Subject: \xff\r\n\r\n\xfd"

    def test_strips_what_compose_stored_down_to_the_wire(self) -> None:
        composed = compose(replace(BASE, draft=True))
        assert without_headers(composed.stored, ["Bcc", DRAFT_HEADER]) == composed.wire


class TestCrlfLines:
    @pytest.mark.parametrize(
        ("raw", "lines"),
        [
            (b"a\r\nb", b"a\r\nb"),
            (b"a\nb", b"a\r\nb"),
            (b"a\rb", b"a\r\nb"),
            (b"a\n\nb", b"a\r\n\r\nb"),
            (b"a\r\rb", b"a\r\n\r\nb"),
            (b"a\r\n\rb", b"a\r\n\r\nb"),
            (b"a\n\r\nb", b"a\r\n\r\nb"),
            (b"a\r\r\nb", b"a\r\nb"),
            (b"a\n\rb", b"a\r\nb"),
            (b"a\n\r\r\nb", b"a\r\n\r\nb"),
        ],
    )
    def test_counts_a_break_as_the_wire_will(self, raw: bytes, lines: bytes) -> None:
        assert crlf_lines(raw) == lines
        assert crlf_lines(lines) == lines

    def test_is_linear_on_a_long_run_of_crs(self) -> None:
        started = time.perf_counter()
        lines = crlf_lines(b"\r" * 200_000)
        assert time.perf_counter() - started < 1.0
        assert lines == b"\r\n" * 200_000


def test_reads_every_value_of_a_header_unfolded() -> None:
    block = b"Delivered-To: a@x.org\r\nSubject: s\r\nDELIVERED-TO:\r\n  b@x.org\r\n  c@x.org\r\n"
    assert header_values(block, "delivered-to") == ["a@x.org", "b@x.org c@x.org"]
    assert header_values(block, "x-original-to") == []


class TestEnvelopeMailboxes:
    def test_lowercases_and_skips_empty_addresses(self) -> None:
        def entry(name: bytes | None, mailbox: bytes | None, host: bytes | None) -> object:
            return SimpleNamespace(name=name, mailbox=mailbox, host=host)

        raw = (
            entry(b"Ana", b"Ana", b"Example.ORG"),
            entry(None, b"undisclosed-recipients", None),
            entry(None, None, b"example.org"),
            entry(None, b"bo", b""),
        )
        assert envelope_mailboxes(raw) == (Mailbox("ana@example.org", "Ana"),)
        assert envelope_mailboxes(None) == ()


ORIGINAL = Original(
    ref=REF,
    message_id="<m@x.org>",
    references=tuple(f"<r{i}@x.org>" for i in range(25)),
    subject="Plans",
    date=datetime(2026, 10, 3, 12, 12, tzinfo=UTC),
    sender=(Mailbox("ana@x.org", "Ana"),),
    to=(Mailbox("me@example.test"), Mailbox("bo@x.org")),
    cc=(Mailbox("alias@example.test"), Mailbox("cy@x.org"), Mailbox("ana@x.org")),
    text="Shall we?\n\nAna",
)


class TestRepliesAndForwards:
    def test_prefixes_subjects_once_recognising_other_languages(self) -> None:
        assert reply_subject("Plans") == "Re: Plans"
        assert reply_subject("R: Piani") == "R: Piani"
        assert reply_subject("AW: Pläne") == "AW: Pläne"
        assert reply_subject("Regarding: x") == "Re: Regarding: x"
        assert forward_subject("Plans") == "Fwd: Plans"
        assert forward_subject("I: Piani") == "I: Piani"
        assert forward_subject("Fw: x") == "Fw: x"

    def test_threads_under_the_original_keeping_the_20_most_recent_references(self) -> None:
        threaded = threading(ORIGINAL)
        assert threaded.in_reply_to == "<m@x.org>"
        assert len(threaded.references) == 20
        assert threaded.references[-1] == "<m@x.org>"

    def test_threads_without_a_message_id(self) -> None:
        threaded = threading(replace(ORIGINAL, message_id=None))
        assert threaded.in_reply_to is None
        assert threaded.references == ORIGINAL.references[-20:]

    def test_replies_to_the_sender_or_everyone_but_the_account_itself_once_each(self) -> None:
        one = reply_recipients(ORIGINAL, ACCOUNT)
        assert (one.to, one.cc) == ((Mailbox("ana@x.org", "Ana"),), ())
        everyone = reply_recipients(ORIGINAL, ACCOUNT, reply_all=True)
        assert everyone.to == (Mailbox("ana@x.org", "Ana"), Mailbox("bo@x.org"))
        assert everyone.cc == (Mailbox("cy@x.org"),)
        listed = replace(ORIGINAL, reply_to=(Mailbox("list@x.org"),))
        assert reply_recipients(listed, ACCOUNT).to == (Mailbox("list@x.org"),)

    def test_answering_ones_own_message_goes_to_whom_it_was_sent_to(self) -> None:
        own = replace(
            ORIGINAL,
            sender=(Mailbox("me@example.test"),),
            to=(Mailbox("bo@x.org"),),
            cc=(),
        )
        assert reply_recipients(own, ACCOUNT).to == (Mailbox("bo@x.org"),)
        assert reply_recipients(own, ACCOUNT, reply_all=True).to == (Mailbox("bo@x.org"),)

    def test_quotes_the_original_under_an_attribution_line(self) -> None:
        assert quoted_original(ORIGINAL, "UTC") == (
            "On 2026-10-03 12:12, Ana <ana@x.org> wrote:\n> Shall we?\n>\n> Ana"
        )
        assert quoted_original(replace(ORIGINAL, sender=(), date=None), "UTC").startswith(
            "On unknown date, someone wrote:"
        )

    def test_shows_a_forwarded_message_with_its_headers(self) -> None:
        assert forwarded_original(ORIGINAL, "Europe/Rome").split("\n") == [
            "---------- Forwarded message ---------",
            "From: Ana <ana@x.org>",
            "Date: 2026-10-03 14:12",
            "Subject: Plans",
            "To: me@example.test, bo@x.org",
            "Cc: alias@example.test, cy@x.org, ana@x.org",
            "",
            "Shall we?",
            "",
            "Ana",
        ]
        assert "Cc:" not in forwarded_original(replace(ORIGINAL, cc=()), "UTC")

    def test_reads_less_of_an_original_than_a_read_does(self) -> None:
        assert MAX_ORIGINAL_BYTES == 2 * 1024 * 1024


def test_shows_a_mailbox_as_results_do() -> None:
    assert mailbox_text(Mailbox("a@x.org")) == "a@x.org"
    assert mailbox_text(Mailbox("a@x.org", "Ana")) == "Ana <a@x.org>"


class TestCaps:
    def test_caps_each_field(self) -> None:
        check_fields(
            to=["a"] * MAX_ADDRESSES,
            cc=["a"] * MAX_ADDRESSES,
            bcc=["a"] * MAX_ADDRESSES,
            subject="s" * MAX_SUBJECT_CHARS,
            text="t" * MAX_TEXT_CHARS,
        )
        for over in (
            {"bcc": ["a"] * (MAX_ADDRESSES + 1)},
            {"subject": "s" * (MAX_SUBJECT_CHARS + 1)},
            {"text": "t" * (MAX_TEXT_CHARS + 1)},
            {"text": "😀" * (MAX_TEXT_CHARS // 2 + 1)},
        ):
            with pytest.raises(MailError) as raised:
                check_fields(**over)  # type: ignore[arg-type]
            assert raised.value.code == "invalid_argument"

    def test_caps_uploads_by_count_and_total(self) -> None:
        cap = attachment_cap(Limits(attachment_max_mb=1.5))
        assert cap == 1_572_864
        check_uploads([cap // 2, cap - cap // 2], cap)
        with pytest.raises(MailError, match=r"may total 1\.5 MB") as raised:
            check_uploads([cap, 1], cap)
        assert raised.value.code == "too_large"
        with pytest.raises(MailError, match="At most 10"):
            check_uploads([1] * (MAX_UPLOADS + 1), cap)


class TestAudit:
    def test_records_an_outgoing_message_without_its_content(self) -> None:
        message = replace(BASE, attachments=(OutgoingAttachment("a", "text/plain", b"x"),))
        item = outgoing_item("acc", message, "ok", folder="Drafts", message_id="<m@x.org>")
        record = item.record
        assert record["from"] == "me@example.test"
        assert record["to"] == ("ana@example.org",)
        assert record["cc"] == ()
        assert record["bcc"] == ("hidden@example.org",)
        assert record["attachments"] == 1
        assert record["folder"] == "Drafts"
        assert "uid" not in record
        assert "recipients" not in record
        dumped = json.dumps(record)
        assert "Hello" not in dumped
        assert "Line one" not in dumped
        assert len(str(record["subject_sha256"])) == 64

    def test_hashes_a_subject_with_lone_surrogates(self) -> None:
        assert outgoing_item("acc", replace(BASE, subject="a\ud800"), "ok").subject_sha256

    def test_needs_neither_folder_nor_uid_and_records_a_removal(self) -> None:
        item = AuditItem(account="acc", removed="failed", result="internal")
        assert item.record == {"account": "acc", "removed": "failed", "result": "internal"}

    def test_counts_internal_and_delivery_unknown_as_failures_not_refusals(self) -> None:
        assert audit_outcome(["internal"]) == "failed"
        assert audit_outcome(["delivery_unknown"]) == "failed"
        assert audit_outcome(["ok", "internal"]) == "partial"
