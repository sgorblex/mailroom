"""FastAPI dependencies shared by the routers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from mailroom.auth.sessions import SESSION_COOKIE_NAME, session_user
from mailroom.context import AppContext, app_context
from mailroom.models import User

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import sessionmaker


def get_db_session(request: Request) -> Iterator[Session]:
    session_maker: sessionmaker[Session] | None = request.app.state.session_maker
    if session_maker is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    with session_maker() as session:
        yield session


DbSession = Annotated[Session, Depends(get_db_session)]


def get_app_context(request: Request) -> AppContext:
    context = app_context(request.app.state)
    if context is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return context


Context = Annotated[AppContext, Depends(get_app_context)]
SessionCookie = Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)]


def require_user(db: DbSession, session_cookie: SessionCookie = None) -> User:
    """Every route but health, readiness, version and sign-in/out sits behind this."""
    user = session_user(db, session_cookie)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


CurrentUser = Annotated[User, Depends(require_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admins only")
    return user


CurrentAdmin = Annotated[User, Depends(require_admin)]
