"""Email actions for performing operations on filtered messages."""

from imap_thingy.filters.actions.base import Action
from imap_thingy.filters.actions.imap import MarkAsAnswered, MarkAsRead, MarkAsUnanswered, MarkAsUnread, MoveTo, Star, Trash, Unstar

__all__ = [
    "Action",
    "MoveTo",
    "Trash",
    "MarkAsAnswered",
    "MarkAsRead",
    "MarkAsUnanswered",
    "MarkAsUnread",
    "Star",
    "Unstar",
]
