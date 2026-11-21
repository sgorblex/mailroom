"""The mail index's settings (``docs/requirements/mail-client.md``, *Index*): which folders of an
account are indexed, the user's age limit under the instance's cap, and each folder's state."""

from mailroom.mail.index.settings import (
    MAX_AGE_ENV,
    IndexedAs,
    IndexFolderStatus,
    IndexFolderSwitched,
    IndexStatus,
    effective_max_age,
    index_status,
    max_age_cap,
    set_folder_enabled,
)

__all__ = [
    "MAX_AGE_ENV",
    "IndexFolderStatus",
    "IndexFolderSwitched",
    "IndexStatus",
    "IndexedAs",
    "effective_max_age",
    "index_status",
    "max_age_cap",
    "set_folder_enabled",
]
