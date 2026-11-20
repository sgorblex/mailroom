"""What a folder holds and how its flags changed, for a host keeping its own copy of them: the
folder's UIDs, every message's flags, and, where the server offers CONDSTORE (RFC 7162), only the
flags changed since a mod-sequence. Each reads in the session `with_mailbox` opened (EXAMINE
will do) and changes nothing.

Each answer carries the folder's UIDVALIDITY and HIGHESTMODSEQ as the SELECT gave them, before
anything was read: a change in between shows again next time, never goes unseen. CHANGEDSINCE
reports no expunge, so a host finds those by comparing `uids_of` with the UIDs it holds.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from imapclient.imap_utf7 import decode as decode_utf7
from imapclient.response_parser import parse_fetch_response

from mailroom_core.mail._mime import text_value, well_formed
from mailroom_core.mail.errors import MailError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from mailroom_core.mail.imap import MailboxContext

UID_CHUNK: Final = 50_000
"""Messages per UID SEARCH at most: imaplib refuses an answer line over 1 MB, which a UID SEARCH
ALL of a few hundred thousand messages would be."""
_RECENT: Final = "\\recent"


@dataclass(frozen=True, slots=True)
class FolderUids:
    """The UIDs in a folder, ascending."""

    uid_validity: int
    highest_modseq: int | None
    """As `MailboxContext.highest_modseq`."""
    uids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FolderFlags:
    """Flags by UID, of every message in a folder or of those changed since a mod-sequence (new
    ones included). `\\Recent` is left out: it belongs to the session, not the message."""

    uid_validity: int
    highest_modseq: int | None
    """As `MailboxContext.highest_modseq`: the mod-sequence to ask changes since next time."""
    flags: Mapping[int, frozenset[str]]
    labels: Mapping[int, tuple[str, ...]] | None = None
    """On Gmail, the same messages' X-GM-LABELS (`stored_labels`), fetched with their flags:
    Gmail changes a message's mod-sequence when its labels change. None elsewhere."""


def uids_of(context: MailboxContext) -> FolderUids:
    """Every UID in the selected folder (`uids_matching` with no criteria)."""
    return FolderUids(context.uid_validity, context.highest_modseq, uids_matching(context))


def uids_matching(context: MailboxContext, criteria: Sequence[object] = ()) -> tuple[int, ...]:
    """The UIDs in the selected folder that match `criteria` (imapclient's search criteria,
    ANDed in), ascending, by UID SEARCH over ranges of at most `UID_CHUNK` messages each.

    The ranges' bounds are the UIDs at every `UID_CHUNK`th message (a FETCH by sequence number,
    during which the server may not report expunges) and together cover every UID, so a message
    expunged meanwhile only makes a range smaller.
    """
    client = context.client
    marks = list(range(UID_CHUNK, context.exists, UID_CHUNK))
    bounds: list[int] = []
    if marks:
        client.use_uid = False
        try:
            fetched = client.fetch(marks, ["UID"])
        finally:
            client.use_uid = True
        bounds = sorted({int(data[b"UID"]) for data in fetched.values() if b"UID" in data})
    found: set[int] = set()
    if context.exists:
        low = 1
        for high in [*bounds, None]:
            # `n:*` matches the highest UID even when it is below n, so the last range filters.
            found.update(
                uid
                for uid in client.search(
                    ["UID", f"{low}:{'*' if high is None else high}", *criteria]
                )
                if uid >= low
            )
            if high is not None:
                low = high + 1
    return tuple(sorted(found))


def flags_of(context: MailboxContext) -> FolderFlags:
    """Every message's flags in the selected folder (UID FETCH 1:* (FLAGS)), and on Gmail its
    labels: what a server without CONDSTORE leaves to compare."""
    return _fetch_flags(context, None)


def changed_flags_of(context: MailboxContext, since: int) -> FolderFlags:
    """The flags of the messages whose mod-sequence is above `since`, a HIGHESTMODSEQ an earlier
    answer gave (UID FETCH 1:* (FLAGS) (CHANGEDSINCE since)), and on Gmail their labels;
    `unsupported` in a folder without mod-sequences (`MailboxContext.highest_modseq` None)."""
    if context.highest_modseq is None:
        raise MailError("unsupported", "The folder keeps no mod-sequences.")
    if since < 0:
        raise MailError("invalid_argument", "A mod-sequence is not negative.")
    if since >= context.highest_modseq:
        return FolderFlags(
            context.uid_validity, context.highest_modseq, {}, {} if context.gmail else None
        )
    return _fetch_flags(context, since)


def _fetch_flags(context: MailboxContext, since: int | None) -> FolderFlags:
    gmail = context.gmail
    if not context.exists:
        return FolderFlags(context.uid_validity, context.highest_modseq, {}, {} if gmail else None)
    client = context.client
    arguments = [b"1:*", b"(FLAGS X-GM-LABELS)" if gmail else b"(FLAGS)"]
    if since is not None:
        arguments.append(f"(CHANGEDSINCE {since})".encode())
    # Sent raw: imapclient's `fetch` keeps only the answers to message ids it was given, and
    # `1:*` names none. Keeping only the answers that carry a UID takes the place of its filter:
    # an unsolicited FETCH (another client's flag change, with CONDSTORE) names a sequence number.
    data = client._raw_command_untagged(b"FETCH", arguments)  # noqa: SLF001
    fetched = parse_fetch_response(data, client.normalise_times, False)
    answers = {int(items[b"UID"]): items for items in fetched.values() if b"UID" in items}
    return FolderFlags(
        context.uid_validity,
        context.highest_modseq,
        {uid: stored_flags(items) for uid, items in answers.items()},
        {uid: stored_labels(items) for uid, items in answers.items()} if gmail else None,
    )


def stored_flags(data: Mapping[bytes, object]) -> frozenset[str]:
    """The FLAGS of a FETCH answer as text, without `\\Recent`."""
    flags = data.get(b"FLAGS")
    texts = (text_value(flag) for flag in (flags if isinstance(flags, tuple | list) else ()))
    return frozenset(text for text in texts if text.lower() != _RECENT)


def stored_labels(data: Mapping[bytes, object]) -> tuple[str, ...]:
    """The X-GM-LABELS of a FETCH answer, decoded as LIST decodes a folder's path (modified UTF-7
    only), so a label matches its folder; each once, without NULs, which Postgres holds none of."""
    raw = data.get(b"X-GM-LABELS")
    if not isinstance(raw, tuple | list):
        return ()
    out: list[str] = []
    for label in raw:
        try:
            text = decode_utf7(label) if isinstance(label, bytes) else text_value(label)
        except ValueError:
            text = text_value(label)
        out.append(well_formed(text).replace("\x00", ""))
    return tuple(dict.fromkeys(label for label in out if label))
