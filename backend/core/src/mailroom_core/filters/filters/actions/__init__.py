"""Email actions for performing operations on filtered messages."""

from imap_thingy.filters.actions.base import Action
from imap_thingy.filters.actions.imap import MarkAsRead, MarkAsUnread, MoveTo, Trash

__all__ = [
    "Action",
    "MoveTo",
    "Trash",
    "MarkAsRead",
    "MarkAsUnread",
]
