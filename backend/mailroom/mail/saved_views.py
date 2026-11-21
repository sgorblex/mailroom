"""The user's saved views (``app.saved_view``; ``docs/requirements/mail-client.md``, *Views*):
some folders of any of their accounts, or a search in the search syntax, named and ordered in the
sidebar, and the built-in *All inboxes* (INBOX of every account), which is no row. Each is read
from the mail index only (``service.view_messages``). Removing an account drops its folders from
every view (``mailroom.accounts.app_data``); the views go with their user."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from mailroom.accounts.service import account_not_found
from mailroom.errors import Refusal
from mailroom.mail import service
from mailroom.mail.search_syntax import SearchSyntaxError, parse_search
from mailroom.models import SavedView
from mailroom_core.mail import SearchQuery, Source

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from mailroom.context import AppContext
    from mailroom.mail.search_syntax import ParsedSearch

logger = logging.getLogger(__name__)

ALL_INBOXES: Final = "all_inboxes"
MAX_VIEWS: Final = 100
"""Of one user."""
MAX_SOURCES: Final = 100
"""Folders of one view."""
MAX_NAME: Final = 100
_NAME_CONSTRAINT: Final = "uq_saved_view_user_id"


def _path(folder: str) -> str:
    return "INBOX" if folder.upper() == "INBOX" else folder


def _log(event: str, user_id: int, **extra: object) -> None:
    logger.info(event, extra={"event": event, "user_id": user_id, **extra})


def views_of(db: Session, user_id: int) -> list[SavedView]:
    """The user's views in their order."""
    query = select(SavedView).where(SavedView.user_id == user_id)
    return list(db.scalars(query.order_by(SavedView.position, SavedView.id)))


def owned_view(db: Session, user_id: int, view_id: int) -> SavedView:
    """The user's view, or ``not_found``."""
    view = db.scalar(select(SavedView).where(SavedView.id == view_id, SavedView.user_id == user_id))
    if view is None:
        raise Refusal("not_found", "view_not_found", "No such view.")
    return view


def _name(name: str) -> str:
    kept = name.strip()
    if not kept:
        raise Refusal("invalid", "invalid_argument", "Give the view a name.")
    return kept


def _sources(sources: Sequence[tuple[int, str]], accounts: Sequence[int]) -> list[list[Any]]:
    """``sources`` as stored, each once, INBOX in capitals; ``not_found`` for an account that is
    not the user's."""
    for account_id, _ in sources:
        if account_id not in accounts:
            raise account_not_found(coded=True)
    kept = dict.fromkeys((account_id, _path(folder)) for account_id, folder in sources)
    return [[account_id, folder] for account_id, folder in kept]


def _parsed(query: str) -> ParsedSearch:
    try:
        return parse_search(query)
    except SearchSyntaxError as error:
        raise Refusal("invalid", "invalid_argument", error.message) from None


def _query(query: str) -> str:
    """``query`` once it parses and the index can answer it (no text)."""
    parsed = _parsed(query)
    if parsed.text is not None:
        raise Refusal("invalid", "invalid_argument", service.TEXT_REFUSED)
    return query.strip()


def _name_taken() -> Refusal:
    return Refusal("conflict", "view_name_taken", "Another of your views has that name.")


def _check_name(db: Session, user_id: int, name: str, view_id: int | None = None) -> None:
    query = select(SavedView.id).where(SavedView.user_id == user_id, SavedView.name == name)
    if view_id is not None:
        query = query.where(SavedView.id != view_id)
    if db.scalar(query) is not None:
        raise _name_taken()


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if _NAME_CONSTRAINT not in str(error.orig):
            raise
        raise _name_taken() from None


def create_view(
    db: Session,
    user_id: int,
    accounts: Sequence[int],
    name: str,
    *,
    sources: Sequence[tuple[int, str]] | None = None,
    query: str | None = None,
) -> SavedView:
    """A new view, last in the order: ``sources`` or ``query``, exactly one of them."""
    name = _name(name)
    count = db.scalar(select(func.count()).where(SavedView.user_id == user_id)) or 0
    if count >= MAX_VIEWS:
        msg = f"You have {MAX_VIEWS} views already: delete one first."
        raise Refusal("invalid", "too_many_views", msg)
    _check_name(db, user_id, name)
    view = SavedView(
        user_id=user_id,
        name=name,
        sources=None if sources is None else _sources(sources, accounts),
        query=None if query is None else _query(query),
        position=count,
    )
    db.add(view)
    _commit(db)
    _log("view_created", user_id, view_id=view.id)
    return view


def change_view(
    db: Session,
    user_id: int,
    accounts: Sequence[int],
    view_id: int,
    *,
    name: str | None = None,
    sources: Sequence[tuple[int, str]] | None = None,
    query: str | None = None,
) -> SavedView:
    """Rename the view, or give it other folders or another search (which it then is)."""
    view = owned_view(db, user_id, view_id)
    if name is not None:
        kept = _name(name)
        _check_name(db, user_id, kept, view.id)
        view.name = kept
    if sources is not None:
        view.sources, view.query = _sources(sources, accounts), None
    if query is not None:
        view.sources, view.query = None, _query(query)
    _commit(db)
    _log("view_changed", user_id, view_id=view.id)
    return view


def remove_view(db: Session, user_id: int, view_id: int) -> None:
    """Delete the view; the rest close up."""
    db.delete(owned_view(db, user_id, view_id))
    db.flush()
    for position, rest in enumerate(views_of(db, user_id)):
        rest.position = position
    db.commit()
    _log("view_removed", user_id, view_id=view_id)


def reorder_views(db: Session, user_id: int, view_ids: Sequence[int]) -> list[SavedView]:
    """Put the user's views in the order of ``view_ids``: every one of them, each once."""
    rows = {view.id: view for view in views_of(db, user_id)}
    if len(view_ids) != len(rows) or set(view_ids) != rows.keys():
        raise Refusal("invalid", "invalid_order", "List every one of your views, each once.")
    for position, view_id in enumerate(view_ids):
        rows[view_id].position = position
    db.commit()
    _log("views_reordered", user_id)
    return views_of(db, user_id)


def _search_query(parsed: ParsedSearch) -> SearchQuery:
    return SearchQuery(
        sender=parsed.sender,
        to=parsed.to,
        subject=parsed.subject,
        since=parsed.since,
        before=parsed.before,
        unread=parsed.unread,
        flagged=parsed.flagged,
        to_sort=parsed.to_sort,
        has_attachment=parsed.has_attachment,
    )


def reading(
    ctx: AppContext, user_id: int, view: SavedView | None
) -> tuple[list[Source], SearchQuery | None]:
    """What ``service.view_messages`` reads for ``view`` (None: *All inboxes*). A search reads
    ``in:``'s folder (``*``: every folder), else INBOX, of ``account:``'s account, else of
    every account, as ``POST /search`` does."""
    if view is None:
        return [Source(account, "INBOX") for account in service.account_ids(ctx, user_id)], None
    if view.sources is not None:
        return [Source(int(account), str(path)) for account, path in view.sources], None
    # Checked when saved; refused, not a failure, should the syntax have changed since.
    parsed = _parsed(view.query or "")
    folder = _path(parsed.folder or "INBOX")
    named = (
        service.ALL_ACCOUNTS
        if parsed.account is None
        else service.account_by_name(ctx, user_id, parsed.account)
    )
    accounts = [named] if isinstance(named, int) else service.account_ids(ctx, user_id)
    return [Source(account, folder) for account in accounts], _search_query(parsed)
