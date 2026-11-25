"""FastAPI dependencies shared by the routers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import Cookie, Depends, HTTPException, Path, Request
from sqlalchemy.orm import Session

from mailroom.api.refusals import refused
from mailroom.api.token_access import route_groups
from mailroom.audit import WEB, Origin
from mailroom.auth.api_tokens import token_access
from mailroom.auth.password_sign_in import password_sign_in
from mailroom.auth.sessions import SESSION_COOKIE_NAME, session_user
from mailroom.auth.sso import Sso
from mailroom.context import AppContext, app_context
from mailroom.models import User
from mailroom.reach import WHOLE, Reach
from mailroom_core.config import MAX_SAFE_INTEGER

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from sqlalchemy.orm import sessionmaker

    from mailroom.mcp.groups import GroupName

logger = logging.getLogger(__name__)


def get_db_session(request: Request) -> Iterator[Session]:
    session_maker: sessionmaker[Session] | None = request.app.state.session_maker
    if session_maker is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    with session_maker() as session:
        yield session


DbSession = Annotated[Session, Depends(get_db_session)]


def configured_sso(request: Request) -> Sso | None:
    """Web SSO as configured; None when it is off."""
    sso: Sso | None = getattr(request.app.state, "sso", None)
    return sso


ConfiguredSso = Annotated[Sso | None, Depends(configured_sso)]


def require_password_sign_in(db: DbSession, sso: ConfiguredSso) -> None:
    """Refuse what passwords are for while users sign in through the identity provider alone."""
    if not password_sign_in(db, sso):
        logger.info(
            "password_refused",
            extra={"event": "password_refused", "outcome": "password_sign_in_off"},
        )
        msg = "Passwords are off here: sign in through the identity provider."
        raise refused(403, "password_sign_in_off", msg)


SessionCookie = Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)]


@dataclass(frozen=True, slots=True)
class Caller:
    """Who a request speaks for: the signed-in user, or a personal API token of theirs, an agent
    within its reach and tool groups."""

    user: User
    origin: Origin = WEB
    reach: Reach = WHOLE
    groups: frozenset[str] | None = None
    """A token's tool groups that the instance offers; None for the user, who needs none."""

    def require(self, group: GroupName) -> None:
        """Refuse a token without ``group``: for a route whose body decides which one it needs."""
        if self.groups is not None and group not in self.groups:
            raise _not_approved([group])


def _not_approved(groups: Iterable[str]) -> HTTPException:
    named = " or ".join(sorted(groups))
    return refused(403, "token_not_approved", f"This API token does not allow {named}.")


def _bearer(request: Request) -> str | None:
    """The bearer token the request carries; another scheme's credentials are not ours to read."""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else None


def _token_caller(request: Request, db: Session, raw: str) -> Caller:
    access = token_access(db, raw)
    if access is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    opened_by = route_groups(request)
    offered = {group.name for group in request.app.state.tool_groups}
    held = frozenset(access.tool_groups & offered)
    outcome = "ok"
    try:
        if not opened_by:
            outcome = "token_not_accepted"
            raise refused(403, outcome, "API tokens cannot do this: sign in to Mailroom instead.")
        if not opened_by & held:
            outcome = "token_not_approved"
            raise _not_approved(opened_by)
    finally:
        logger.info(
            "api_token_request",
            extra={
                "event": "api_token_request",
                "user_id": access.user.id,
                "token_id": access.token_id,
                "outcome": outcome,
            },
        )
    return Caller(access.user, Origin("api", token_id=access.token_id), access.reach, held)


def require_caller(request: Request, db: DbSession, session_cookie: SessionCookie = None) -> Caller:
    """Every route but health, readiness, version and sign-in/out sits behind this: the session
    cookie, or a personal API token as ``Authorization: Bearer`` on the routes open to it
    (``mailroom.api.token_access``). Both at once is refused, rather than one quietly winning."""
    raw = _bearer(request)
    if raw is not None:
        if session_cookie is not None:
            msg = "Send the session cookie or an API token, not both."
            raise refused(400, "two_credentials", msg)
        return _token_caller(request, db, raw)
    user = session_user(db, session_cookie)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return Caller(user)


CurrentCaller = Annotated[Caller, Depends(require_caller)]


def require_user(caller: CurrentCaller) -> User:
    return caller.user


def get_app_context(request: Request, caller: CurrentCaller) -> AppContext:
    """The app's context within what the caller reaches, as who they are."""
    context = app_context(request.app.state)
    if context is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return context.within(caller.reach, caller.origin)


Context = Annotated[AppContext, Depends(get_app_context)]
CurrentUser = Annotated[User, Depends(require_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admins only")
    return user


CurrentAdmin = Annotated[User, Depends(require_admin)]

AccountId = Annotated[int, Path(ge=1, le=MAX_SAFE_INTEGER)]
"""An account's id in a route's path."""
