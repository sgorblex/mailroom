"""Filter actions: Action, move, flags, delete, noop."""

from imap_thingy.filters.actions.action import Action
from imap_thingy.filters.actions.delete import Delete
from imap_thingy.filters.actions.flags import (
    MarkAsAnswered,
    MarkAsRead,
    MarkAsUnanswered,
    MarkAsUnread,
    Star,
    Unstar,
)
from imap_thingy.filters.actions.move import MoveTo, Trash
from imap_thingy.filters.actions.noop import NoOp

__all__ = [
    "Action",
    "Delete",
    "MarkAsAnswered",
    "MarkAsRead",
    "MarkAsUnanswered",
    "MarkAsUnread",
    "MoveTo",
    "NoOp",
    "Star",
    "Trash",
    "Unstar",
]
