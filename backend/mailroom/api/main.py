from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import ArgumentError, NoSuchModuleError, SQLAlchemyError

from mailroom import database as mailroom_database
from mailroom import db_migrations as mailroom_db_migrations
from mailroom.api.build_info import (
    api_deploy_env,
    api_distribution_version,
    api_git_branch,
    api_git_sha,
)
from mailroom.api.csrf import CrossSiteRequestMiddleware
from mailroom.api.deps import require_user
from mailroom.api.routes.auth import auth_router
from mailroom.api.routes.users import own_password_router, users_router
from mailroom.auth.throttle import LoginThrottle
from mailroom.auth.users import user_count
from mailroom.database_connectivity import CONNECTIVITY_ERRORS
from mailroom.observability.http_request_logging import CorrelationLoggingMiddleware
from mailroom.observability.logging_config import configure_logging

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.engine import Engine


def _record_database_startup_failure(
    application: FastAPI,
    reason: str,
    *,
    engine: Engine | None = None,
) -> None:
    if engine is not None:
        engine.dispose()
    if reason == "misconfigured":
        logger.warning(
            "Database is misconfigured: verify DATABASE_URL and optional "
            "MAILROOM_DATABASE_POOL_SIZE / MAILROOM_DATABASE_MAX_OVERFLOW "
            "(see repository .env.example).",
        )
    elif reason == "unreachable":
        logger.warning(
            "Database is unreachable at startup: verify network connectivity, "
            "that the server is running, and DATABASE_URL host/port (credentials not logged).",
        )
    application.state.database_startup_failure = reason
    application.state.database_engine = None
    application.state.session_maker = None


def _initialize_database(application: FastAPI) -> None:
    try:
        database_url = mailroom_database.get_sync_database_url()
    except RuntimeError:
        _record_database_startup_failure(application, "misconfigured")
        return

    try:
        pool_size = int(os.environ.get("MAILROOM_DATABASE_POOL_SIZE", "5"))
        max_overflow = int(os.environ.get("MAILROOM_DATABASE_MAX_OVERFLOW", "10"))
    except ValueError:
        _record_database_startup_failure(application, "misconfigured")
        return

    if pool_size < 1 or max_overflow < 0:
        _record_database_startup_failure(application, "misconfigured")
        return

    try:
        engine = mailroom_database.create_database_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
    except ArgumentError, NoSuchModuleError, SQLAlchemyError, TypeError:
        _record_database_startup_failure(application, "misconfigured")
        return

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except CONNECTIVITY_ERRORS as exc:
        logger.warning(
            "Database driver or connectivity error during API startup connectivity check",
            exc_info=exc,
        )
        _record_database_startup_failure(application, "unreachable", engine=engine)
        return
    except SQLAlchemyError:
        _record_database_startup_failure(application, "misconfigured", engine=engine)
        return

    application.state.database_engine = engine
    application.state.session_maker = mailroom_database.create_session_factory(engine)


def _warn_if_nobody_can_sign_in(application: FastAPI) -> None:
    session_maker = application.state.session_maker
    if session_maker is None:
        return
    try:
        with session_maker() as db:
            if user_count(db):
                return
    except SQLAlchemyError:
        return
    logger.warning(
        "No users yet: create the first with `mise run create-user <name> --admin` "
        "(in a deployment, see deploy/quadlet/README.md)."
    )


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    mailroom_database.load_local_environment()
    application.state.database_startup_failure = None
    mailroom_db_migrations.apply_startup_migrations_if_configured()
    _initialize_database(application)
    _warn_if_nobody_can_sign_in(application)
    yield
    application.state.session_maker = None
    shutdown_engine: Engine | None = application.state.database_engine
    if shutdown_engine is not None:
        shutdown_engine.dispose()
    application.state.database_engine = None


def _not_ready(reason: str) -> JSONResponse:
    return JSONResponse(status_code=503, content={"status": "not_ready", "reason": reason})


def create_application() -> FastAPI:
    application = FastAPI(title="Mailroom API", lifespan=_lifespan)
    application.state.login_throttle = LoginThrottle()
    application.add_middleware(CrossSiteRequestMiddleware)
    application.add_middleware(CorrelationLoggingMiddleware)

    @application.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        """Liveness for the container healthcheck: the process answers, nothing more."""
        return {"status": "ok"}

    public_v1 = APIRouter(prefix="/api/v1")

    @public_v1.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @public_v1.get("/version")
    def api_version() -> dict[str, str]:
        return {
            "version": api_distribution_version(),
            "git_sha": api_git_sha(),
            "git_branch": api_git_branch(),
            "deploy_env": api_deploy_env(),
        }

    @public_v1.get("/ready")
    def ready(request: Request) -> JSONResponse:
        engine: Engine | None = request.app.state.database_engine
        if engine is None:
            failure = getattr(request.app.state, "database_startup_failure", None)
            if failure == "misconfigured":
                return _not_ready("database_misconfigured")
            if failure == "unreachable":
                return _not_ready("database_unreachable")
            return _not_ready("database_not_configured")
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return _not_ready("database_unreachable")
        return JSONResponse(content={"status": "ready"})

    public_v1.include_router(auth_router)
    application.include_router(public_v1)

    # Every other route goes here.
    protected_v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_user)])
    protected_v1.include_router(own_password_router)
    protected_v1.include_router(users_router)
    application.include_router(protected_v1)
    return application


application = create_application()
