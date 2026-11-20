"""The mail index (``docs/requirements/mail-client.md``, *Index*): its settings (which folders of
an account are indexed, the user's age limit under the instance's cap, each folder's state), the
sync the worker runs, the rows the app's own writes change (``writes``), and the folders browsers
are viewing (``interest``)."""

from mailroom.mail.index.interest import INTEREST_CHANNEL, prune_interest
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
from mailroom.mail.index.writes import NOTIFY_CHANNEL, notify

__all__ = [
    "INTEREST_CHANNEL",
    "MAX_AGE_ENV",
    "NOTIFY_CHANNEL",
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
    "notify",
    "prune_interest",
    "set_folder_enabled",
    "sync_folder",
]
