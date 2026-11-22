"""The vacation reply's checks (RFC 3834) over parsed header fields, each guard on its own and in
the order they are asked; the fields read from an ENVELOPE and a header block; and the reply,
threaded and marked automatic."""

from dataclasses import replace

import pytest
from imapclient.response_types import Address, Envelope

from mailroom_core.mail import (
    Incoming,
    Mailbox,
    MessageInput,
    compose,
    no_reply_address,
    vacation_refusal,
    vacation_reply,
)
from mailroom_core.mail.vacation import LIST_FIELDS, MAX_REFERENCES, incoming_of

ME = "me@example.com"
ALIAS = "alias@example.com"
OTHER_ACCOUNT = "me@home.example"
IDENTITIES = frozenset({ME, ALIAS})
OWN = IDENTITIES | {OTHER_ACCOUNT}
FRIEND = Mailbox("friend@example.org", "Friend")


def plain(**changes: object) -> Incoming:
    """A message a person sent to ME: answered."""
    message = Incoming(
        sender=FRIEND,
        return_path="friend@example.org",
        to=(ME,),
        message_id="<1@example.org>",
    )
    return replace(message, **changes)  # type: ignore[arg-type]


def refusal(message: Incoming) -> str | None:
    return vacation_refusal(message, identities=IDENTITIES, own=OWN)


def test_a_person_writing_to_an_identity_is_answered() -> None:
    assert refusal(plain()) is None
    assert refusal(plain(to=("someone@else.example",), cc=(ALIAS,))) is None
    assert refusal(plain(return_path=None)) is None
    assert refusal(plain(auto_submitted="no", precedence="first-class")) is None
    assert refusal(plain(response_suppress=frozenset({"dr", "rn"}))) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"sender": None},
        {"return_path": ""},
    ],
    ids=["no-single-from", "null-return-path"],
)
def test_mail_without_a_valid_sender_is_refused(changes: dict[str, object]) -> None:
    assert refusal(plain(**changes)) == "no_sender"


@pytest.mark.parametrize(
    "changes",
    [
        {"auto_submitted": "auto-replied"},
        {"auto_submitted": "auto-generated"},
        {"auto_submitted": "auto-notified"},
        {"auto_submitted": "something-new"},
        {"autoreply_marks": True},
        {"response_suppress": frozenset({"oof"})},
        {"response_suppress": frozenset({"all"})},
        {"response_suppress": frozenset({"autoreply"})},
    ],
)
def test_automatic_mail_is_refused(changes: dict[str, object]) -> None:
    assert refusal(plain(**changes)) == "auto_submitted"


@pytest.mark.parametrize("precedence", ["bulk", "junk", "list"])
def test_bulk_mail_is_refused(precedence: str) -> None:
    assert refusal(plain(precedence=precedence)) == "bulk"


@pytest.mark.parametrize("field", LIST_FIELDS)
def test_any_list_field_makes_it_a_lists(field: str) -> None:
    assert refusal(plain(list_fields=frozenset({field}))) == "mailing_list"


@pytest.mark.parametrize(
    "address",
    [
        "noreply@example.org",
        "no-reply@example.org",
        "No_Reply@example.org",
        "noreply+tag@example.org",
        "noreply-alerts@example.org",
        "do-not-reply@example.org",
        "donotreply@example.org",
        "dont.reply@example.org",
        "MAILER-DAEMON@example.org",
        "postmaster@example.org",
        "nobody@example.org",
        "bounce@example.org",
        "bounces+123@example.org",
        "bounce-abc@example.org",
        "listserv@example.org",
        "majordomo@example.org",
        "owner-friends@example.org",
        "friends-request@example.org",
        "friends-owner@example.org",
        "friends-bounces@example.org",
        "notifications@example.org",
    ],
)
def test_no_reply_senders_are_refused(address: str) -> None:
    assert no_reply_address(address)
    assert refusal(plain(sender=Mailbox(address.lower(), None))) == "no_reply_sender"


def test_a_no_reply_return_path_is_refused_too() -> None:
    assert refusal(plain(return_path="bounces+x@mailer.example")) == "no_reply_sender"


@pytest.mark.parametrize(
    "address", ["friend@example.org", "replyall@example.org", "noah@example.org", "bob@noreply.org"]
)
def test_ordinary_addresses_are_no_no_reply(address: str) -> None:
    assert not no_reply_address(address)


@pytest.mark.parametrize("address", [ME, ALIAS, OTHER_ACCOUNT])
def test_the_users_own_addresses_are_refused(address: str) -> None:
    assert refusal(plain(sender=Mailbox(address, None))) == "own_address"


@pytest.mark.parametrize(
    "changes",
    [
        {"to": (), "cc": ()},
        {"to": ("list@lists.example",)},
        {"to": (OTHER_ACCOUNT,)},
    ],
    ids=["bcc", "elsewhere", "another-account"],
)
def test_mail_not_addressed_to_the_accounts_identities_is_refused(
    changes: dict[str, object],
) -> None:
    assert refusal(plain(**changes)) == "not_addressed"


def test_the_checks_are_asked_in_order() -> None:
    everything = plain(
        sender=Mailbox("noreply@example.org", None),
        auto_submitted="auto-generated",
        precedence="bulk",
        list_fields=frozenset({"list-id"}),
        to=(),
    )
    assert refusal(replace(everything, return_path="")) == "no_sender"
    assert refusal(everything) == "auto_submitted"
    assert refusal(replace(everything, auto_submitted=None)) == "bulk"
    assert refusal(replace(everything, auto_submitted=None, precedence=None)) == "mailing_list"
    assert (
        refusal(replace(everything, auto_submitted=None, precedence=None, list_fields=frozenset()))
        == "no_reply_sender"
    )


def address(text: str, name: bytes | None = None) -> Address:
    mailbox, _, host = text.partition("@")
    return Address(name, None, mailbox.encode(), host.encode())


def envelope(sender: tuple[Address, ...] | None, to: tuple[Address, ...] = ()) -> Envelope:
    return Envelope(
        None, b"=?utf-8?q?Pranzo=3F?=", sender, sender, None, to, None, None, None, b"<1@x.org>"
    )


HEADERS = (
    b"From: Friend <friend@example.org>\r\n"
    b"Return-Path: <friend@example.org>\r\n"
    b"References: <0@x.org>\r\n <00@x.org>\r\n"
    b"Precedence: First-Class\r\n"
    b"Auto-Submitted: No (a person)\r\n"
    b"X-Auto-Response-Suppress: DR, RN\r\n"
)


def test_fields_are_read_from_the_envelope_and_the_header_block() -> None:
    read = incoming_of(
        envelope((address("Friend@Example.org", b"Friend"),), (address(ME),)), HEADERS
    )
    assert read == Incoming(
        sender=Mailbox("friend@example.org", "Friend"),
        return_path="friend@example.org",
        to=(ME,),
        message_id="<1@x.org>",
        references=("<0@x.org>", "<00@x.org>"),
        precedence="first-class",
        auto_submitted="no",
        response_suppress=frozenset({"dr", "rn"}),
    )
    assert refusal(read) is None


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (HEADERS + b"From: Other <other@example.org>\r\n", "no_sender"),
        (b"Return-Path: <>\r\n" + HEADERS, "no_sender"),
        (
            HEADERS.replace(b"No (a person)", b"auto-replied; owner-email=x@y.org"),
            "auto_submitted",
        ),
        (HEADERS.replace(b"No (a person)", b"Auto-Generated"), "auto_submitted"),
        (HEADERS + b"X-Autoreply: yes\r\n", "auto_submitted"),
        (HEADERS.replace(b"DR, RN", b"DR, OOF"), "auto_submitted"),
        (HEADERS.replace(b"First-Class", b"BULK"), "bulk"),
        (HEADERS + b"List-Unsubscribe: <mailto:u@x.org>\r\n", "mailing_list"),
        (HEADERS + b"Mailing-List: contact x@y.org\r\n", "mailing_list"),
    ],
)
def test_header_blocks_that_are_refused(headers: bytes, expected: str) -> None:
    read = incoming_of(envelope((address("friend@example.org"),), (address(ME),)), headers)
    assert refusal(read) == expected


def test_an_envelope_without_one_deliverable_from_has_no_sender() -> None:
    two = (address("a@example.org"), address("b@example.org"))
    for sender in (None, two, (address("not an address@example.org"),)):
        assert incoming_of(envelope(sender, (address(ME),)), HEADERS).sender is None


def test_the_reply_is_threaded_marked_and_goes_to_the_sender_alone() -> None:
    message = plain(references=tuple(f"<{n}@x.org>" for n in range(30)), cc=(ALIAS,))
    reply = vacation_reply(message, Mailbox(ME, "Me"), subject="Away", html="<p>Back soon</p>")
    assert isinstance(reply, MessageInput)
    assert (reply.to, reply.cc, reply.bcc) == ((FRIEND,), (), ())
    assert reply.in_reply_to == "<1@example.org>"
    assert len(reply.references) == MAX_REFERENCES
    assert reply.references[-2:] == ("<29@x.org>", "<1@example.org>")
    composed = compose(reply)
    assert b"\r\nAuto-Submitted: auto-replied\r\n" in composed.wire
    assert b"\r\nIn-Reply-To: <1@example.org>\r\n" in composed.wire
    assert b"Subject: Away\r\n" in composed.wire


def test_only_an_automatic_reply_is_marked() -> None:
    composed = compose(MessageInput(sender=Mailbox(ME, None), to=(FRIEND,), subject="s", text="t"))
    assert b"Auto-Submitted" not in composed.wire
