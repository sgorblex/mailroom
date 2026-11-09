"""Which identity a message goes out as (`src/mail/identity.ts`)."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mailroom_core.mail.errors import MailError
from mailroom_core.mail.recipients import Mailbox, recipient_list

if TYPE_CHECKING:
    from mailroom_core.config import AccountConfig
    from mailroom_core.config.schema import Identity


@dataclass(frozen=True, slots=True)
class Addressed:
    """Where a message being answered was addressed, for picking the identity that answers it."""

    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    delivered: tuple[str, ...] = ()
    """X-Original-To and Delivered-To values: where an alias actually delivered it."""


def _mailbox(identity: Identity) -> Mailbox:
    return Mailbox(identity.address.lower(), identity.name)


def from_identity(
    account: AccountConfig, explicit: str | None = None, original: Addressed | None = None
) -> Mailbox:
    """The From identity (docs/core.md, *Compose*): an explicit one must be among the
    account's identities; otherwise, for a reply or forward, the identity the original was sent
    to (To, then Cc, then the delivery headers); otherwise the default one."""
    identities = {identity.address.lower(): identity for identity in account.identities}
    if explicit is not None:
        [wanted] = recipient_list([explicit])
        identity = identities.get(wanted.address)
        if identity is None:
            raise MailError(
                "invalid_argument",
                f"{account.id} cannot send as that address. Use one of: "
                f"{', '.join(i.address for i in account.identities)}.",
            )
        return _mailbox(identity)
    if original is not None:
        for address in (*original.to, *original.cc, *original.delivered):
            identity = identities.get(address.lower())
            if identity is not None:
                return _mailbox(identity)
    return _mailbox(next((i for i in account.identities if i.default), account.identities[0]))
