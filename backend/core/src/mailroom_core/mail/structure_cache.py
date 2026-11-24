"""Analysed BODYSTRUCTUREs kept between the calls that read one message.

A message's structure never changes while its UID and its folder's UIDVALIDITY stand, so its
view, its HTML and an attachment of it need the structure fetched once. `MailConnections` keeps
one cache for its accounts and hands it to every `MailboxContext`.
"""

import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from mailroom_core.mail.structure import Structure

STRUCTURE_CACHE_SIZE: Final = 256
"""Structures kept per cache, the least recently used dropped first: a page of messages and the
ones opened from it, with room to spare, at a few kilobytes each."""

type StructureKey = tuple[int, str, int, int]
"""Account id, folder path, UIDVALIDITY and UID: what a message ref encodes."""


class StructureCache:
    """The last `size` structures used, by `StructureKey`; safe to share between threads."""

    def __init__(self, size: int = STRUCTURE_CACHE_SIZE) -> None:
        """`size`: how many structures to keep, at least one."""
        if size < 1:
            raise ValueError("size must be at least 1")
        self._size = size
        self._lock = threading.Lock()
        self._kept: OrderedDict[StructureKey, Structure] = OrderedDict()

    def get(self, key: StructureKey) -> Structure | None:
        """The structure kept for `key`, None when there is none."""
        with self._lock:
            found = self._kept.get(key)
            if found is not None:
                self._kept.move_to_end(key)
            return found

    def put(self, key: StructureKey, structure: Structure) -> None:
        """Keeps `structure` for `key`, dropping the least recently used beyond the size."""
        with self._lock:
            self._kept[key] = structure
            self._kept.move_to_end(key)
            while len(self._kept) > self._size:
                self._kept.popitem(last=False)

    def forget(self, account: int) -> None:
        """Drops every structure of the account: its server may have changed."""
        with self._lock:
            for key in [key for key in self._kept if key[0] == account]:
                del self._kept[key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._kept)
