"""The Handled mark: what an account may keep it as, and how it is found, read and set."""

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest

from mailroom_core.filters.actions.flags import MarkHandled, UnmarkHandled
from mailroom_core.filters.criteria.flags import IsHandled, IsNotHandled
from mailroom_core.filters.message import Message
from mailroom_core.filters.selected import Selected
from mailroom_core.mail import (
    STAR,
    HandledKey,
    HandledMark,
    MailboxContext,
    MailError,
    SearchQuery,
    fetch_sources,
    holds_handled,
    parse_handled,
    search_arguments,
    search_uids,
    store_handled,
)

if TYPE_CHECKING:
    from imapclient import IMAPClient

KEYWORD = HandledMark("keyword", "$MailroomHandled")
LABEL = HandledMark("label", "Handled")


def context(
    mark: HandledMark, *, gmail: bool = False, keywords: bool = True, path: str = "INBOX"
) -> MailboxContext:
    client = MagicMock()
    client.has_capability.side_effect = lambda name: gmail and name == "X-GM-EXT-1"
    client._raw_command_untagged.return_value = [b""]  # noqa: SLF001
    client.list_folders.return_value = []
    return MailboxContext(
        cast("IMAPClient", client),
        path,
        False,
        uid_validity=1,
        exists=1,
        uid_next=None,
        account=1,
        keywords=keywords and not gmail,
        handled_lookup=lambda: mark,
    )


def searched(on: MailboxContext, criteria: list[object]) -> bytes:
    search_uids(on, criteria)
    client = cast("MagicMock", on.client)
    (_, arguments), _ = client._raw_command_untagged.call_args  # noqa: SLF001
    return b" ".join(arguments)


@pytest.mark.parametrize(
    "text",
    ["star", "keyword:$MailroomHandled", "keyword:$MailroomSort", "label:Handled", "label:A/B è"],
)
def test_a_mark_is_read_as_it_is_written(text: str) -> None:
    assert str(parse_handled(text)) == text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Star",
        "star:",
        "flag",
        "keyword",
        "keyword:",
        "keyword:two words",
        'keyword:a"b',
        "keyword:a)b",
        "keyword:\\Seen",
        "keyword:\\Deleted",
        "keyword:a]b",
        "keyword:é",
        "keyword:a\r\nb",
        "keyword:a\n",
        "keyword:" + "x" * 65,
        "keyword:$forwarded",
        "keyword:$Junk",
        "label:",
        "label:INBOX",
        "label:Inbox/x",
        "label:[Gmail]/Trash",
        "label:[Google Mail]",
        "label:\\Inbox",
        'label:a"b',
        "label:a%",
        "label:*",
        "label:a\r\nb",
        "label: a",
        "label:a//b",
        "label:a/",
        "label:" + "x" * 65,
    ],
)
def test_what_is_no_mark_is_refused(text: str) -> None:
    with pytest.raises(MailError) as refused:
        parse_handled(text)
    assert refused.value.code == "invalid_argument"


@pytest.mark.parametrize(
    ("mark", "gmail", "path", "yes", "no"),
    [
        (STAR, False, "INBOX", b"(FLAGGED)", b"(UNFLAGGED)"),
        (
            KEYWORD,
            False,
            "INBOX",
            b"(KEYWORD $MailroomHandled)",
            b"(UNKEYWORD $MailroomHandled)",
        ),
        (LABEL, True, "INBOX", b"(X-GM-LABELS Handled)", b"(NOT X-GM-LABELS Handled)"),
        (
            HandledMark("label", "Fatto è"),
            True,
            "INBOX",
            b'(X-GM-LABELS "Fatto &AOg-")',
            b'(NOT X-GM-LABELS "Fatto &AOg-")',
        ),
        # Gmail leaves the selected folder's own label out.
        (LABEL, True, "Handled", b"(ALL)", b"(NOT ALL)"),
        # A label marks nothing off Gmail, a folder of its name neither.
        (LABEL, False, "INBOX", b"(NOT ALL)", b"(ALL)"),
        (LABEL, False, "Handled", b"(NOT ALL)", b"(ALL)"),
    ],
)
def test_a_search_is_sent_in_the_accounts_mark(
    mark: HandledMark, gmail: bool, path: str, yes: bytes, no: bytes
) -> None:
    on = context(mark, gmail=gmail, path=path)
    assert searched(on, SearchQuery(handled=True).criteria()) == yes
    assert searched(on, SearchQuery(handled=False).criteria()) == no
    negated = (~IsHandled().imap_query).criteria()
    assert searched(on, negated) == b"NOT " + yes
    either = (IsHandled().imap_query | IsNotHandled().imap_query).criteria()
    assert searched(on, either) == b"OR " + yes + b" " + no


def test_criteria_are_checked_before_the_mark_is_known() -> None:
    assert search_arguments([HandledKey.HANDLED, "UNSEEN"]) == ([b"(ALL)", b"UNSEEN"], False)


def test_a_message_holds_the_mark() -> None:
    assert holds_handled(STAR, ["\\FLAGGED"])
    assert not holds_handled(STAR, ["$MailroomHandled"])
    assert holds_handled(KEYWORD, ["\\Seen", "$mailroomhandled"])
    assert not holds_handled(KEYWORD, ["\\Flagged"])
    assert holds_handled(LABEL, ["\\Flagged"], ["\\Inbox", "Handled"])
    assert not holds_handled(LABEL, ["\\Flagged"], ["\\Inbox"])
    assert holds_handled(LABEL, [], folder="Handled")


@pytest.mark.parametrize(("marked", "method"), [(True, "add_flags"), (False, "remove_flags")])
@pytest.mark.parametrize(("mark", "flag"), [(STAR, b"\\Flagged"), (KEYWORD, b"$MailroomHandled")])
def test_the_mark_is_stored_as_a_flag(
    mark: HandledMark, flag: bytes, marked: bool, method: str
) -> None:
    on = context(mark)
    store_handled(on, [5], marked=marked)
    getattr(on.client, method).assert_called_once_with([5], [flag], silent=True)
    store_handled(on, [], marked=marked)
    getattr(on.client, method).assert_called_once()


def test_a_label_is_created_once_and_stored() -> None:
    on = context(LABEL, gmail=True)
    client = cast("MagicMock", on.client)
    store_handled(on, [5], marked=True)
    client.create_folder.assert_called_once_with("Handled")
    client.add_gmail_labels.assert_called_once_with([5], ["Handled"], silent=True)

    client.list_folders.return_value = [((), b"/", "Handled")]
    store_handled(on, [6], marked=True)
    client.create_folder.assert_called_once()
    store_handled(on, [6], marked=False)
    client.remove_gmail_labels.assert_called_once_with([6], ["Handled"], silent=True)
    client.add_flags.assert_not_called()


@pytest.mark.parametrize(
    "on",
    [context(KEYWORD, keywords=False), context(KEYWORD, gmail=True), context(LABEL)],
    ids=["no keywords", "keyword on gmail", "label off gmail"],
)
@pytest.mark.parametrize("marked", [True, False])
def test_a_mark_the_server_cannot_keep_is_refused(on: MailboxContext, marked: bool) -> None:
    with pytest.raises(MailError) as refused:
        store_handled(on, [5], marked=marked)
    assert refused.value.code == "unsupported"
    client = cast("MagicMock", on.client)
    client.add_flags.assert_not_called()
    client.remove_flags.assert_not_called()


def test_filters_set_clear_and_match_the_accounts_mark() -> None:
    on = Selected(context(KEYWORD), "/")
    MarkHandled().execute(on, [1])
    UnmarkHandled().execute(on, [2])
    client = cast("MagicMock", on.context.client)
    client.add_flags.assert_called_once_with([1], [b"$MailroomHandled"], silent=True)
    client.remove_flags.assert_called_once_with([2], [b"$MailroomHandled"], silent=True)
    assert on.changed == {(1, "INBOX")}

    message = Message(1, MagicMock(), [], handled=True)
    plain = Message(2, MagicMock(), [b"\\Flagged"])
    candidates = {1: message, 2: plain}
    assert list(IsHandled().select(candidates)) == [1]
    assert list(IsNotHandled().select(candidates)) == [2]


def test_a_filter_does_not_clear_the_mark_in_its_labels_own_folder() -> None:
    on = Selected(context(LABEL, gmail=True, path="Handled"), "/")
    with pytest.raises(MailError) as refused:
        UnmarkHandled().execute(on, [1])
    assert refused.value.code == "invalid_argument"
    cast("MagicMock", on.context.client).remove_gmail_labels.assert_not_called()


def test_a_fetched_message_says_whether_it_is_handled() -> None:
    on = context(LABEL, gmail=True)
    client = cast("MagicMock", on.client)
    client.fetch.return_value = {
        1: {b"BODY[]": b"x", b"FLAGS": (b"\\Flagged",), b"X-GM-LABELS": (b"Handled",)},
        2: {b"BODY[]": b"x", b"FLAGS": (b"\\Flagged",), b"X-GM-LABELS": ()},
    }
    found = fetch_sources(on, [1, 2])
    assert "X-GM-LABELS" in client.fetch.call_args[0][1]
    assert (found[1].handled, found[2].handled) == (True, False)

    keyword = context(KEYWORD)
    cast("MagicMock", keyword.client).fetch.return_value = {
        1: {b"BODY[]": b"x", b"FLAGS": (b"$MAILROOMHANDLED",)}
    }
    assert fetch_sources(keyword, [1])[1].handled
    assert "X-GM-LABELS" not in cast("MagicMock", keyword.client).fetch.call_args[0][1]
