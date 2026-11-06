"""Attaching what is already in a mailbox (`src/mail/attachments.ts`), so no file ever round-trips
through a model."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mailroom_core.mail.compose import OutgoingAttachment
from mailroom_core.mail.content_type import content_type
from mailroom_core.mail.download import part_content
from mailroom_core.mail.errors import MailError
from mailroom_core.mail.outgoing import attachments_too_large
from mailroom_core.mail.read import clearly_over, fetch_structure
from mailroom_core.mail.ref import check_fresh

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mailroom_core.mail.imap import MailboxContext, MailConnections
    from mailroom_core.mail.ref import MessageRef


@dataclass(frozen=True, slots=True)
class AttachmentSource:
    """An attachment of an existing message, named by its ref and part number as a read lists
    them."""

    ref: MessageRef
    part: str


def attachments_from(
    connections: MailConnections, sources: Iterable[AttachmentSource], cap_bytes: int
) -> tuple[OutgoingAttachment, ...]:
    """The parts to attach, read from their mailboxes. Each must be an attachment or the calendar
    part of its message (not its body, nor what its HTML shows in place), and together they stay
    under `cap_bytes`: `too_large` beyond. A host checks that each ref's account is the caller's,
    as for any ref."""
    attachments: list[OutgoingAttachment] = []
    for source in sources:
        room = cap_bytes - sum(len(attachment.content) for attachment in attachments)
        if room < 0:
            raise attachments_too_large(cap_bytes)
        attachments.append(_read(connections, source, room, cap_bytes))
    return tuple(attachments)


def _read(
    connections: MailConnections, source: AttachmentSource, room: int, cap_bytes: int
) -> OutgoingAttachment:
    ref, part = source.ref, source.part

    def run(context: MailboxContext) -> OutgoingAttachment:
        check_fresh(ref, context.uid_validity)
        _data, structure = fetch_structure(context.client, ref.uid, [])
        calendar = () if structure.calendar is None else (structure.calendar,)
        info = next((p for p in (*structure.attachments, *calendar) if p.part == part), None)
        leaf = structure.parts.get(part)
        if info is None or leaf is None:
            raise MailError("not_found", f"Part {part} is not an attachment of that message.")
        if clearly_over(leaf, room):
            raise attachments_too_large(cap_bytes)
        content = part_content(context.client, ref.uid, leaf, room + 1)
        if len(content) > room:
            raise attachments_too_large(cap_bytes)
        return OutgoingAttachment(
            filename=info.name or f"part-{part}",
            content_type=content_type(content, info.type, info.name),
            content=content,
        )

    return connections.with_mailbox(ref.account, ref.folder, True, run)
