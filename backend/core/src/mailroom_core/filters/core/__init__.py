"""Core representations: folder path, IMAP search query, message, and flags."""

from imap_thingy.core.flags import Flag, FlagTriplet
from imap_thingy.core.imap_query import All, IMAPQuery, Q, build_base_query
from imap_thingy.core.message import Message
from imap_thingy.core.path import Path

__all__ = [
    "All",
    "Flag",
    "FlagTriplet",
    "IMAPQuery",
    "Message",
    "Path",
    "Q",
    "build_base_query",
]
