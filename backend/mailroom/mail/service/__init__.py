"""A user's mail as every front end reads and writes it (the REST routes, the MCP tools): folders,
pages of messages, one message, its HTML and its attachments, an account's quota, over
mailroom-core; moves (to another of the user's accounts too), archiving, trashing and flag
changes of batches of refs, and of every message a search matches; drafts and sends; creating,
renaming and deleting folders, and marking one read. Reading changes nothing, not even
``\\Seen``. Mail is deleted only deliberately: nothing is expunged beyond what a move takes away
and a draft mailroom wrote (*The own-draft exception*), a move to another account removes for
good only when asked, once the copies are confirmed, and a folder's delete takes its mail for
good only with the count the user confirmed (*Folder management*; all in
``docs/requirements/mail-client.md``).

Only the user's own accounts: an account id, or the account a ref names, that is not theirs is
``not_found`` exactly as one that does not exist. A mailbox failure is a ``Refusal`` of kind
``mail`` carrying mailroom-core's code and fixed message (never the server's words); a call still
waiting on the server after ``MailPool.deadline_seconds`` is ``timeout``. Every function blocks:
an async caller runs it in a worker thread. The log names the call, the account and the front end
(``via``), never a subject, an address, a folder or a body; an organize call's and a send's add
only counts and codes.

The package is split by concern: ``read``, ``organize``, ``bulk`` (every message a search
matches), ``folder_writes`` (creating, renaming and deleting folders, marking one read;
``folders`` is the listing, re-exported here), ``compose`` (drafts with it), ``send`` and
``views`` (a view's messages, from the index only), with what more than one of them uses in
``_shared``. This facade re-exports what the front ends call, so they keep calling
``service.X``.
"""

from mailroom.mail.index.reads import ListedSummary
from mailroom.mail.service._shared import MAX_CURSOR, MAX_FOLDER, MAX_REF_LENGTH, MAX_TERM
from mailroom.mail.service.bulk import BULK_BATCH, BULK_MAX, act_on_matching, count_matching
from mailroom.mail.service.compose import (
    DRAFT_GONE,
    DRAFT_NOT_EDITABLE,
    SEND_HOLDS_THE_DRAFT,
    DraftFields,
    DraftView,
    Prefill,
    SendView,
    delete_draft,
    find_draft,
    prefill,
    read_draft,
    save_draft,
    upload_limit,
    upload_place,
)
from mailroom.mail.service.folder_writes import (
    MAX_FOLDER_NAME,
    MAX_MESSAGE_COUNT,
    FolderRenamed,
    create_folder,
    delete_folder,
    mark_folder_read,
    rename_folder,
)
from mailroom.mail.service.organize import (
    MAX_REFS,
    archive,
    flag,
    junk,
    move,
    move_to_account,
    trash,
)
from mailroom.mail.service.read import (
    ALL_ACCOUNTS,
    AllAccounts,
    KeywordFailures,
    Printable,
    Quotas,
    account_by_name,
    account_ids,
    account_keywords,
    attachment,
    folder_segments,
    folders,
    indexable_folders,
    keywords,
    list_messages,
    message_html,
    message_source,
    printable,
    quota,
    read_headers,
    read_message,
    search,
    thread,
    thread_key,
)
from mailroom.mail.service.send import (
    Dispatched,
    read_send,
    send_draft,
    send_forward,
    send_holds,
    send_message,
    send_reply,
)
from mailroom.mail.service.views import TEXT_REFUSED, ViewPage, view_messages

__all__ = [
    "ALL_ACCOUNTS",
    "BULK_BATCH",
    "BULK_MAX",
    "DRAFT_GONE",
    "DRAFT_NOT_EDITABLE",
    "MAX_CURSOR",
    "MAX_FOLDER",
    "MAX_FOLDER_NAME",
    "MAX_MESSAGE_COUNT",
    "MAX_REFS",
    "MAX_REF_LENGTH",
    "MAX_TERM",
    "SEND_HOLDS_THE_DRAFT",
    "TEXT_REFUSED",
    "AllAccounts",
    "Dispatched",
    "DraftFields",
    "DraftView",
    "FolderRenamed",
    "KeywordFailures",
    "ListedSummary",
    "Prefill",
    "Printable",
    "Quotas",
    "SendView",
    "ViewPage",
    "account_by_name",
    "account_ids",
    "account_keywords",
    "act_on_matching",
    "archive",
    "attachment",
    "count_matching",
    "create_folder",
    "delete_draft",
    "delete_folder",
    "find_draft",
    "flag",
    "folder_segments",
    "folders",
    "indexable_folders",
    "junk",
    "keywords",
    "list_messages",
    "mark_folder_read",
    "message_html",
    "message_source",
    "move",
    "move_to_account",
    "prefill",
    "printable",
    "quota",
    "read_draft",
    "read_headers",
    "read_message",
    "read_send",
    "rename_folder",
    "save_draft",
    "search",
    "send_draft",
    "send_forward",
    "send_holds",
    "send_message",
    "send_reply",
    "thread",
    "thread_key",
    "trash",
    "upload_limit",
    "upload_place",
    "view_messages",
]
