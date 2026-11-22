"""The mail client (``docs/requirements/mail-client.md``, *REST contract*): folders, message
pages, search, one message, its HTML as a standalone document and part downloads, which change
nothing; the organize routes (move, archive, junk, trash, flag), which change folders and flags, and
the bulk ones, which count and act on every message a search matches; creating, renaming and
deleting folders; drafts and sends; the addresses a recipient field suggests; the senders whose
remote content is shown without asking; one-click unsubscribing; muted threads; snoozed messages;
the mail index's settings; the live updates' event stream; the folders desktop notifications come
from; and the saved views. Only the signed-in user's accounts.

The package is split by concern: ``folders`` (the listing, and creating, renaming and deleting
folders), ``read``, ``organize``, ``bulk``, ``drafts`` (sends with them), ``addresses``,
``remote_senders``, ``unsubscribe``, ``mute``, ``snooze``, ``index``, ``events``,
``notifications`` and ``views``, each registering its routes on ``mail_router``, which
``_shared`` holds with what more than one of them uses. The order they are imported in is the
order the routes are listed in, the OpenAPI document's."""

# isort: off
from mailroom.api.routes.mail import (  # noqa: F401 -- each registers its routes on mail_router
    folders,
    read,
    organize,
    bulk,
    drafts,
    addresses,
    remote_senders,
    unsubscribe,
    mute,
    snooze,
    index,
    events,
    notifications,
    views,
)

# isort: on
from mailroom.api.routes.mail._shared import NotStored, mail_router

__all__ = ["NotStored", "mail_router"]
