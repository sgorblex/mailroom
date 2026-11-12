"""Creating, renaming (moving, too) and deleting an account's folders.

Each runs in one `MailConnections.with_connection` session, every check on the session's own
connection (a second one of the account would wait for it): LIST first, then the refusals, then
the one write and the subscriptions, which are best effort. A delete loses the folder's mail for
good, so it is refused unless the caller names the count it confirmed, read again here with
STATUS (docs/core.md, *Safety*). Each call notes exactly one audit item, refused ones included.

When the write itself loses its connection or times out, the server may have done it anyway:
the folders are listed once more, outside the session, and a change that shows is a success.
"""

import imaplib
import logging
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from mailroom_core.mail.audit import AuditItem, audit_result
from mailroom_core.mail.errors import MailError
from mailroom_core.mail.imap import folders_from
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from imapclient import IMAPClient

    from mailroom_core.mail.drafts import Note
    from mailroom_core.mail.imap import Folder, MailConnections

MAX_FOLDER_DEPTH: Final = 32
"""Of a folder's path, in segments, also for every subfolder a rename moves."""
MAX_FOLDER_NAME: Final = 255
"""Characters of one segment, the name a create or a rename gives."""
_MAX_PATH: Final = 1000
_WILDCARDS: Final = ("*", "%")
_UNCERTAIN: Final = ("unreachable", "timeout")
_CANNOT: Final = "The mail server does not accept that folder name."

_log = logging.getLogger("mailroom_core.mail")

type _Stage = Literal["checking", "writing", "written"]


@dataclass(frozen=True, slots=True, kw_only=True)
class FolderChange:
    """What a create, rename or delete did: the folder (`path`, its old path on a rename), its
    new path on a rename, and on a delete how many messages went with it."""

    account: str
    path: str
    delimiter: str | None
    """The account's hierarchy delimiter, None when it has none."""
    destination: str | None = None
    messages: int | None = None


class FolderChangedError(MailError):
    """A delete refused because the folder no longer holds the count the caller confirmed:
    `messages` is what it holds now. Nothing was deleted."""

    messages: int

    def __init__(self, messages: int) -> None:
        """Sets the count the folder holds now, which the message names."""
        self.messages = messages
        noun = "message" if messages == 1 else "messages"
        super().__init__("folder_changed", f"It holds {messages} {noun} now; nothing was deleted.")


@dataclass(slots=True)
class _Progress:
    stage: _Stage = "checking"


def check_folder_name(name: str, delimiter: str | None) -> None:
    """Refuses (`invalid_argument`) a name a create or rename may not give a folder: empty or
    over `MAX_FOLDER_NAME` characters, with a control character, the delimiter, `*` or `%`, or
    a leading or trailing space."""
    if not name:
        raise MailError("invalid_argument", "A folder needs a name.")
    if len(name) > MAX_FOLDER_NAME:
        raise MailError(
            "invalid_argument", f"A folder's name is at most {MAX_FOLDER_NAME} characters."
        )
    if any(unicodedata.category(char) == "Cc" for char in name):
        raise MailError("invalid_argument", "A folder's name cannot hold control characters.")
    if delimiter and delimiter in name:
        raise MailError(
            "invalid_argument",
            f'A folder\'s name cannot hold "{delimiter}", which separates folders on this server.',
        )
    if any(wildcard in name for wildcard in _WILDCARDS):
        raise MailError("invalid_argument", 'A folder\'s name cannot hold "*" or "%".')
    if name != name.strip():
        raise MailError("invalid_argument", "A folder's name cannot start or end with a space.")


def subfolders_of(folders: Iterable[Folder], path: str) -> list[Folder]:
    """The folders of `folders` under `path`, at any depth, in their order."""
    return [f for f in folders if f.delimiter and f.path.startswith(f"{path}{f.delimiter}")]


def create_folder(
    connections: MailConnections, account: str, parent: str | None, name: str, note: Note
) -> FolderChange:
    """Creates `name` under `parent` (None: at the top level) and subscribes it. Refused when
    the parent is not listed (`mailbox_missing`), or the folder exists (`folder_exists`)."""
    target = name
    progress = _Progress()
    try:
        delimiter = connections.delimiter(account)
        target = _target(parent, name, delimiter)

        def run(client: IMAPClient) -> None:
            folders = folders_from(client.list_folders())
            _check_parent(folders, parent, account)
            if _find(folders, target) is not None:
                raise MailError("folder_exists")
            _write(progress, account, lambda: client.create_folder(target))
            _update_subscriptions(client, account, subscribe=[target])

        _run(connections, account, progress, run, lambda listed: _find(listed, target) is not None)
    except Exception as error:
        note(AuditItem(account=account, folder=target, result=audit_result(error)))
        raise
    note(AuditItem(account=account, folder=target, result="ok"))
    return FolderChange(account=account, path=target, delimiter=delimiter)


def rename_folder(
    connections: MailConnections,
    account: str,
    folder: str,
    parent: str | None,
    name: str,
    note: Note,
) -> FolderChange:
    """Renames `folder` to `name` under `parent` (None: the top level), moving its subfolders
    and their subscriptions with it. Refused for INBOX, a folder with a role or holding one, a
    target inside the folder or the same as it, a parent not listed (`mailbox_missing`), and a
    target that exists (`folder_exists`)."""
    target: str | None = None
    progress = _Progress()
    source = folder
    try:
        delimiter = connections.delimiter(account)
        target = _target(parent, name, delimiter)
        destination = target

        def run(client: IMAPClient) -> None:
            nonlocal source
            folders = folders_from(client.list_folders())
            found = _changeable(folders, folder, account, "renamed")
            source = found.path
            moved = subfolders_of(folders, source)
            if any(sub.role is not None for sub in moved):
                raise MailError(
                    "invalid_argument",
                    "It holds a folder with a role (Sent, Trash…), which cannot be moved.",
                )
            if destination == source:
                raise MailError("invalid_argument", "That is already the folder's name and place.")
            if delimiter and destination.startswith(f"{source}{delimiter}"):
                raise MailError("invalid_argument", "A folder cannot move into itself.")
            _check_parent(folders, parent, account)
            if _find(folders, destination) is not None:
                raise MailError("folder_exists")
            for sub in moved:
                _check_path(destination + sub.path[len(source) :], delimiter)
            subscribed = _subscribed(client, account, source)
            _write(progress, account, lambda: client.rename_folder(source, destination))
            _update_subscriptions(
                client,
                account,
                subscribe=[destination + path[len(source) :] for path in subscribed],
                unsubscribe=subscribed,
            )

        _run(
            connections,
            account,
            progress,
            run,
            lambda listed: _find(listed, destination) is not None and _find(listed, source) is None,
        )
    except Exception as error:
        item = AuditItem(
            account=account, folder=source, destination=target, result=audit_result(error)
        )
        note(item)
        raise
    note(AuditItem(account=account, folder=source, destination=destination, result="ok"))
    return FolderChange(account=account, path=source, delimiter=delimiter, destination=destination)


def delete_folder(
    connections: MailConnections, account: str, folder: str, *, messages: int, note: Note
) -> FolderChange:
    """Deletes `folder` and its mail for good, and unsubscribes it. `messages` is how many
    messages the caller saw in it and confirmed: STATUS reads the count again, and when it
    differs the delete is refused with `FolderChangedError`, carrying the new count. Refused
    also for INBOX, a folder with a role and one with subfolders (`\\Noselect` ones too)."""
    progress = _Progress()
    path = folder
    try:
        if isinstance(messages, bool) or not isinstance(messages, int) or messages < 0:
            raise MailError("invalid_argument", "The message count is a whole number, 0 or more.")
        delimiter = connections.delimiter(account)

        def run(client: IMAPClient) -> None:
            nonlocal path
            folders = folders_from(client.list_folders())
            found = _changeable(folders, folder, account, "deleted")
            path = found.path
            if subfolders_of(folders, path):
                raise MailError("invalid_argument", "It has subfolders: delete or move them first.")
            count = _count(client, found)
            if count != messages:
                raise FolderChangedError(count)
            _write(progress, account, lambda: client.delete_folder(path))
            _update_subscriptions(client, account, unsubscribe=[path])

        _run(connections, account, progress, run, lambda listed: _find(listed, path) is None)
    except Exception as error:
        note(AuditItem(account=account, folder=path, messages=messages, result=audit_result(error)))
        raise
    note(AuditItem(account=account, folder=path, messages=messages, result="ok"))
    return FolderChange(account=account, path=path, delimiter=delimiter, messages=messages)


def _run(
    connections: MailConnections,
    account: str,
    progress: _Progress,
    run: Callable[[IMAPClient], None],
    done: Callable[[Sequence[Folder]], bool],
) -> None:
    """`run` in a session; a write whose answer was lost is looked for in a fresh listing, and
    one that was answered stands, whatever failed after it (a subscription)."""
    try:
        connections.with_connection(account, run)
    except MailError as error:
        if progress.stage == "written":
            _log.warning(
                "imap_folder_subscriptions_failed",
                extra={"event": "imap_folder_subscriptions_failed", "account": account},
            )
            return
        if progress.stage != "writing" or error.code not in _UNCERTAIN:
            raise
        try:
            listed = connections.folders(account)
        except MailError:
            raise error from None
        if not done(listed):
            raise
        _log.info(
            "imap_folder_write_confirmed",
            extra={"event": "imap_folder_write_confirmed", "account": account, "code": error.code},
        )


def _write(progress: _Progress, account: str, command: Callable[[], object]) -> None:
    """The one CREATE, RENAME or DELETE, with the server's refusals that name their reason as
    our codes; others go to `with_connection` to map."""
    progress.stage = "writing"
    try:
        command()
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error as error:
        mapped = _answer(str(error))
        if mapped is None:
            raise
        _log.warning(
            "imap_error",
            extra={
                "event": "imap_error",
                "account": account,
                "mapped": mapped.code,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        raise mapped from None
    progress.stage = "written"


def _answer(text: str) -> MailError | None:
    """A CREATE, RENAME or DELETE refusal by its response code (RFC 5530), None for others."""
    if "[ALREADYEXISTS]" in text:
        return MailError("folder_exists")
    if "[CANNOT]" in text:
        return MailError("invalid_argument", _CANNOT)
    if "[INUSE]" in text:
        return MailError("busy")
    return None


def _subscribed(client: IMAPClient, account: str, path: str) -> list[str]:
    """`path` and the folders under it that are subscribed; best effort, as the subscriptions
    are: none when the server refuses LSUB (deprecated in IMAP4rev2)."""
    try:
        lsub = folders_from(client.list_sub_folders())
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error as error:
        _log.info(
            "imap_subscriptions_unlisted",
            extra={
                "event": "imap_subscriptions_unlisted",
                "account": account,
                "error_type": type(error).__name__,
            },
        )
        return []
    return [f.path for f in lsub if f.path == path] + [f.path for f in subfolders_of(lsub, path)]


def _update_subscriptions(
    client: IMAPClient,
    account: str,
    *,
    subscribe: Sequence[str] = (),
    unsubscribe: Sequence[str] = (),
) -> None:
    """Best effort: a server's NO is logged and passed over (Dovecot may already have moved a
    renamed folder's subscriptions); a lost connection is raised, for the session to drop."""
    for subscribing, paths in ((True, subscribe), (False, unsubscribe)):
        for path in paths:
            try:
                if subscribing:
                    client.subscribe_folder(path)
                else:
                    client.unsubscribe_folder(path)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error as error:
                _log.info(
                    "imap_subscription_refused",
                    extra={
                        "event": "imap_subscription_refused",
                        "account": account,
                        "subscribe": subscribing,
                        "error": str(error),
                    },
                )


def _target(parent: str | None, name: str, delimiter: str | None) -> str:
    """The path `name` gets under `parent`, its name and length checked."""
    check_folder_name(name, delimiter)
    if parent is None:
        if name.upper() == "INBOX":
            raise MailError("invalid_argument", "INBOX is a name the server keeps for itself.")
        path = name
    elif delimiter is None:
        raise MailError(
            "invalid_argument", "This server has no folder hierarchy: a folder has no parent."
        )
    else:
        # INBOX is any case, and the server lists it as INBOX: the path follows that spelling.
        listed = "INBOX" if parent.upper() == "INBOX" else parent
        path = f"{listed}{delimiter}{name}"
    _check_path(path, delimiter)
    return path


def _check_path(path: str, delimiter: str | None) -> None:
    depth = len(path.split(delimiter)) if delimiter else 1
    if depth > MAX_FOLDER_DEPTH:
        raise MailError("invalid_argument", f"Folders nest at most {MAX_FOLDER_DEPTH} levels deep.")
    if len(path) > _MAX_PATH:
        raise MailError("invalid_argument", f"A folder's path is at most {_MAX_PATH} characters.")


def _find(folders: Iterable[Folder], path: str) -> Folder | None:
    inbox = path.upper() == "INBOX"
    return next(
        (f for f in folders if f.path == path or (inbox and f.path.upper() == "INBOX")), None
    )


def _missing(path: str, account: str) -> MailError:
    return MailError(
        "mailbox_missing", f'"{clean_header(path, 200)}" is not a folder of {account}; see folders.'
    )


def _check_parent(folders: Sequence[Folder], parent: str | None, account: str) -> None:
    if parent is not None and _find(folders, parent) is None:
        raise _missing(parent, account)


def _changeable(folders: Sequence[Folder], path: str, account: str, verb: str) -> Folder:
    """The listed folder at `path`, unless it is INBOX or has a role."""
    found = _find(folders, path)
    if found is None:
        raise _missing(path, account)
    if found.path.upper() == "INBOX" or found.role is not None:
        raise MailError(
            "invalid_argument",
            f"INBOX and the folders with a role (Sent, Trash…) cannot be {verb}.",
        )
    return found


def _count(client: IMAPClient, folder: Folder) -> int:
    """How many messages the folder holds now; 0 for one that cannot hold any."""
    if not folder.selectable:
        return 0
    status = client.folder_status(folder.path, ["MESSAGES"])
    return int(status[b"MESSAGES"])
