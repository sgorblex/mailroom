"""The mail index (``docs/requirements/mail-client.md``, *Index*): its settings (which folders of
an account are indexed, the user's age limit under the instance's cap, each folder's state) and
the sync the worker runs."""

from mailroom.mail.index.settings import (
    MAX_AGE_ENV,
    IndexedAs,
    IndexFolderStatus,
    IndexFolderSwitched,
    IndexStatus,
    discover_folders,
    effective_max_age,
    index_status,
    max_age_cap,
    set_folder_enabled,
)
from mailroom.mail.index.sync import due_folders, fail_folder, sync_folder

__all__ = [
    "MAX_AGE_ENV",
    "IndexFolderStatus",
    "IndexFolderSwitched",
    "IndexStatus",
    "IndexedAs",
    "discover_folders",
    "due_folders",
    "effective_max_age",
    "fail_folder",
    "index_status",
    "max_age_cap",
    "set_folder_enabled",
    "sync_folder",
]
