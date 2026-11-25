"""How an account keeps the Handled mark: the star, an IMAP keyword or a Gmail label. The host
says which (`MailConnections(handled=...)`, the star without it), and everything here that reads
or sets the mark resolves it through the session (`MailboxContext.handled`)."""

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Literal

from imapclient.imap_utf7 import encode as encode_utf7

from mailroom_core.mail.errors import MailError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from mailroom_core.mail.imap import MailboxContext

HandledKind = Literal["star", "keyword", "label"]

HANDLED_KEYWORD: Final = "$MailroomHandled"
"""The keyword an account keeps Handled as unless it names another."""
HANDLED_LABEL: Final = "Handled"
"""The Gmail label an account keeps Handled as unless it names another."""
MAX_HANDLED_NAME: Final = 64

_FLAGGED: Final = "\\Flagged"
_KEYWORD: Final = re.compile(r"[!#$&'+,\-./0-9:;<=>?@A-Z\[^_`a-z|}~]+")
"""An atom (RFC 3501), as a keyword must be."""
_TAKEN: Final = frozenset(
    {
        "$forwarded",
        "$mdnsent",
        "$submitpending",
        "$submitted",
        "$junk",
        "$notjunk",
        "junk",
        "nonjunk",
        "notjunk",
        "$phishing",
    }
)
"""Keywords that mean something else to mail clients and servers."""


@dataclass(frozen=True, slots=True)
class HandledMark:
    """The mark an account keeps Handled as; `name` is the keyword's or the label's."""

    kind: HandledKind
    name: str = ""

    def __str__(self) -> str:
        """`star`, `keyword:<name>` or `label:<name>`, as `parse_handled` reads it."""
        return self.kind if self.kind == "star" else f"{self.kind}:{self.name}"


STAR: Final = HandledMark("star")


class HandledKey(Enum):
    """Marked or not, as SEARCH criteria hold it: written in the account's own mark when sent
    (`search_uids`)."""

    HANDLED = True
    NOT_HANDLED = False


def parse_handled(text: str) -> HandledMark:
    """`text` (`star`, `keyword:<name>`, `label:<name>`) as a mark; `invalid_argument` when it is
    none, or its name is not one a keyword or a label of the user's can have."""
    kind, _, name = text.partition(":")
    match kind:
        case "star" if text == "star":
            return STAR
        case "keyword":
            if not _KEYWORD.fullmatch(name) or len(name) > MAX_HANDLED_NAME:
                raise MailError(
                    "invalid_argument",
                    "A keyword is 1 to 64 letters, digits and signs, without spaces, quotes, "
                    "parentheses, backslashes, % or *.",
                )
            if name.lower() in _TAKEN:
                raise MailError("invalid_argument", f"{name} already means something else.")
            return HandledMark("keyword", name)
        case "label":
            _check_label(name)
            return HandledMark("label", name)
    raise MailError("invalid_argument", "Handled is star, keyword:<name> or label:<name>.")


def _check_label(name: str) -> None:
    parts = name.split("/")
    if (
        len(name) > MAX_HANDLED_NAME
        or any(not part or part != part.strip() for part in parts)
        or any(unicodedata.category(char).startswith("C") for char in name)
        or any(char in '*%"\\' for char in name)
    ):
        raise MailError(
            "invalid_argument",
            'A label is 1 to 64 characters, without control characters, *, %, " or a backslash, '
            "or a space or nothing around a /.",
        )
    if parts[0].upper() == "INBOX" or parts[0].lower() in ("[gmail]", "[google mail]"):
        raise MailError("invalid_argument", f"{name} is one of Gmail's own folders.")


def holds_handled(
    mark: HandledMark, flags: Iterable[str], labels: Iterable[str] = (), *, folder: str = ""
) -> bool:
    """Whether a message with `flags` (keywords included, any case) and Gmail `labels` is marked.
    Gmail leaves the selected folder's own label out of a message's labels: `folder` is it."""
    if mark.kind == "label":
        return folder == mark.name or mark.name in labels
    wanted = (_FLAGGED if mark.kind == "star" else mark.name).lower()
    return any(flag.lower() == wanted for flag in flags)


def handled_criteria(key: HandledKey, mark: HandledMark, *, gmail: bool, folder: str) -> list[str]:
    """`key` as SEARCH keys for `mark` in `folder`. A label off Gmail marks nothing."""
    on = key.value
    if mark.kind == "star":
        return ["FLAGGED" if on else "UNFLAGGED"]
    if mark.kind == "keyword":
        return ["KEYWORD" if on else "UNKEYWORD", mark.name]
    if not gmail or folder == mark.name:
        return ["ALL"] if on == (gmail and folder == mark.name) else ["NOT", "ALL"]
    label = encode_utf7(mark.name).decode("ascii")
    return [*([] if on else ["NOT"]), "X-GM-LABELS", label]


def check_handled(context: MailboxContext) -> None:
    """`unsupported` where the session's server cannot keep its account's mark."""
    mark = context.handled
    if mark.kind == "keyword" and not context.keywords:
        raise MailError(
            "unsupported",
            "This mail server keeps no keywords in this folder, so it cannot mark Handled.",
        )
    if mark.kind == "label" and not context.gmail:
        raise MailError("unsupported", "Only Gmail keeps labels, so it cannot mark Handled.")


def store_handled(context: MailboxContext, uids: Sequence[int], *, marked: bool) -> None:
    """Marks `uids` Handled, or clears the mark, in the account's own way; `unsupported`
    (`check_handled`) where it cannot. A label is created when it is not there yet. Silent:
    nothing is read back."""
    check_handled(context)
    if not uids:
        return
    client, mark, wanted = context.client, context.handled, list(uids)
    if mark.kind == "label":
        if not marked:
            client.remove_gmail_labels(wanted, [mark.name], silent=True)
            return
        if not client.list_folders("", mark.name):
            client.create_folder(mark.name)
        client.add_gmail_labels(wanted, [mark.name], silent=True)
        return
    flag = (_FLAGGED if mark.kind == "star" else mark.name).encode("ascii")
    (client.add_flags if marked else client.remove_flags)(wanted, [flag], silent=True)
