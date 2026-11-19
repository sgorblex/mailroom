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


def sent_to(account: AccountConfig, original: Addressed) -> Identity | None:
    """The account's identity a message arrived at: the first of its To, then Cc, then delivery
    addresses that is one; None when none is (a Bcc, a list, a forward from elsewhere)."""
    identities = {identity.address.lower(): identity for identity in account.identities}
    return next(
        (
            identities[address.lower()]
            for address in (*original.to, *original.cc, *original.delivered)
            if address.lower() in identities
        ),
        None,
    )


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
                "This account cannot send as that address. Use one of: "
                f"{', '.join(i.address for i in account.identities)}.",
            )
        return _mailbox(identity)
    if original is not None and (identity := sent_to(account, original)) is not None:
        return _mailbox(identity)
    return _mailbox(next((i for i in account.identities if i.default), account.identities[0]))
