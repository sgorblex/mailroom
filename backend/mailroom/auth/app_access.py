"""Ending everything that acts for a user without signing in: their personal API tokens and their
connected apps' grants, all at once."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailroom.auth.api_tokens import delete_user_tokens
from mailroom.oauth.tokens import delete_user_grants

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session


def revoke_app_access(db: Session, user_id: int) -> Callable[[], None]:
    """Delete every API token and grant of the user, the grants' codes and tokens with them, so
    the next request of any is refused. Leaves the commit to the caller, who then calls what this
    returns: it logs each one as revoking it alone does."""
    log_tokens = delete_user_tokens(db, user_id)
    log_grants = delete_user_grants(db, user_id)

    def log() -> None:
        log_tokens()
        log_grants()

    return log
