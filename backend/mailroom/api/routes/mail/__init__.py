"""The mail client (``docs/requirements/mail-client.md``, *REST contract*): folders, message
pages, search, one message, its HTML as a standalone document and part downloads, which change
nothing; the organize routes (move, archive, trash, flag), which change folders and flags;
creating, renaming and deleting folders; and drafts and sends. Only the signed-in user's
accounts.

The package is split by concern: ``folders`` (the listing, and creating, renaming and deleting
folders), ``read``, ``organize`` and ``drafts`` (sends with them), each registering its routes on
``mail_router``, which ``_shared`` holds with what more than one of them uses. The order they are
imported in is the order the routes are listed in, the OpenAPI document's."""

# isort: off
from mailroom.api.routes.mail import folders, read, organize, drafts  # noqa: F401 -- each registers its routes on mail_router

# isort: on
from mailroom.api.routes.mail._shared import NotStored, mail_router

__all__ = ["NotStored", "mail_router"]
