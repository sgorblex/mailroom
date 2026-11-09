from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager, nullcontext
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import ArgumentError, NoSuchModuleError, SQLAlchemyError
from starlette.routing import Route

from mailroom import database as mailroom_database
from mailroom import db_migrations as mailroom_db_migrations
from mailroom.accounts.mail import mail_ssl_context
from mailroom.accounts.secrets_key import secrets_subkey_from_env
from mailroom.api.build_info import (
    api_deploy_env,
    api_distribution_version,
    api_git_branch,
    api_git_sha,
)
from mailroom.api.csrf import CrossSiteRequestMiddleware
from mailroom.api.deps import require_user
from mailroom.api.refusals import http_refusal
from mailroom.api.routes.accounts import accounts_router
from mailroom.api.routes.auth import auth_router
from mailroom.api.routes.filters import filters_router
from mailroom.api.routes.limits import limits_router
from mailroom.api.routes.mail import NotStored, mail_router
from mailroom.api.routes.oauth_consent import consent_router, grants_router
from mailroom.api.routes.sso import identities_router, sso_router
from mailroom.api.routes.users import own_password_router, users_router
from mailroom.auth.oidc import OidcProvider
from mailroom.auth.sso import Sso, sso_from_env, sso_start_limit
from mailroom.auth.throttle import LoginThrottle, client_key
from mailroom.auth.users import user_count
from mailroom.config.features import FEATURES_ENV, features_from_env
from mailroom.config.public_url import public_url_from_env
from mailroom.context import app_context
from mailroom.database_connectivity import CONNECTIVITY_ERRORS
from mailroom.errors import Refusal
from mailroom.mail.pool import MailPool
from mailroom.mail.sends import SendRunner
from mailroom.mcp.app import METADATA_PATH, McpEndpoint
from mailroom.mcp.settings import mcp_settings_from_env
from mailroom.mcp.verifier import ExternalJwtVerifier, token_verifier
from mailroom.oauth.config import oauth_settings_from_env
from mailroom.oauth.server import OAuthServer
from mailroom.observability.http_request_logging import CorrelationLoggingMiddleware
from mailroom.observability.logging_config import configure_logging

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx
    from mcp.server.auth.provider import TokenVerifier
    from sqlalchemy.engine import Engine

    from mailroom.config.features import Features
    from mailroom.mcp.settings import McpSettings


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


def _external_verifier(
    application: FastAPI, mcp_settings: McpSettings
) -> tuple[ExternalJwtVerifier | None, OidcProvider | None]:
    """External mode's verifier, when ``MAILROOM_MCP_AUTH`` is ``external``, and the provider made
    for it, which the lifespan closes. An issuer that is SSO's shares SSO's provider (one discovery
    document and JWKS cache), and then none is made; ``mcp_oidc_transport`` only reaches one made
    for MCP."""
    external = mcp_settings.external
    if external is None:
        return None, None
    sso: Sso | None = application.state.sso
    owned = None
    if sso is not None and sso.provider.issuer == external.issuer:
        provider = sso.provider
    else:
        provider = owned = OidcProvider(
            external.issuer, transport=application.state.mcp_oidc_transport
        )
    verifier = ExternalJwtVerifier(
        provider,
        audiences=external.audiences,
        required_scope=external.required_scope,
        resource=mcp_settings.resource,
        session_maker=lambda: application.state.session_maker,
    )
    return verifier, owned


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    features: Features = application.state.features
    logger.info("%s: %s", FEATURES_ENV, features.description)
    application.state.database_startup_failure = None
    mailroom_db_migrations.apply_startup_migrations_if_configured()
    _initialize_database(application)
    _warn_if_nobody_can_sign_in(application)
    application.state.secrets_subkey = secrets_subkey_from_env()
    application.state.mail_ssl_context = mail_ssl_context()
    application.state.mail_pool = MailPool() if features.mail_layer else None
    application.state.send_runner = SendRunner() if features.mail_layer else None
    application.state.sso = sso_from_env(application.state.public_url)
    mcp_settings: McpSettings | None = application.state.mcp_settings
    verifier: TokenVerifier | None = application.state.mcp_verifier
    mcp_provider = None
    if verifier is None and mcp_settings is not None:
        external, mcp_provider = _external_verifier(application, mcp_settings)
        verifier = token_verifier(
            mcp_settings.auth, builtin=application.state.oauth_server, external=external
        )
    mcp: McpEndpoint | None = application.state.mcp
    try:
        async with nullcontext() if mcp is None else mcp.running(mcp_settings, verifier):
            yield
    finally:
        runner: SendRunner | None = application.state.send_runner
        if runner is not None:
            runner.close()
        pool: MailPool | None = application.state.mail_pool
        if pool is not None:
            pool.close()
        if mcp_provider is not None:
            mcp_provider.close()
        sso: Sso | None = application.state.sso
        if sso is not None:
            sso.provider.close()
        application.state.session_maker = None
        shutdown_engine: Engine | None = application.state.database_engine
        if shutdown_engine is not None:
            shutdown_engine.dispose()
        application.state.database_engine = None


def _not_ready(reason: str) -> JSONResponse:
    return JSONResponse(status_code=503, content={"status": "not_ready", "reason": reason})


async def _refusal_answer(request: Request, exc: Exception) -> Response:
    """A service's ``Refusal``, raised through a route, answered as ``http_refusal`` words it."""
    if not isinstance(exc, Refusal):  # registered for Refusal only
        raise exc
    return await http_exception_handler(request, http_refusal(exc))


def _builtin_oauth_server(
    application: FastAPI, mcp_settings: McpSettings | None
) -> OAuthServer | None:
    """The built-in authorization server, when the MCP server is on (``MAILROOM_PUBLIC_URL`` set)
    with ``MAILROOM_MCP_AUTH`` ``builtin``, the default."""
    if mcp_settings is None or mcp_settings.auth != "builtin":
        return None
    return OAuthServer(
        oauth_settings_from_env(mcp_settings.public_url),
        lambda: application.state.session_maker,
        client_key=client_key,
    )


def create_application(
    *,
    features: Features | None = None,
    mcp_settings: McpSettings | None = None,
    mcp_verifier: TokenVerifier | None = None,
    mcp_oidc_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """The API, with the routes of ``features`` (``MAILROOM_FEATURES`` when None, which raises
    ``ValueError`` for an unknown name). ``mcp_settings`` and ``mcp_verifier`` replace what the
    environment would choose for the MCP server, and ``mcp_oidc_transport`` carries external
    mode's requests to its provider (tests)."""
    mailroom_database.load_local_environment()
    if features is None:
        features = features_from_env()
    public_url = public_url_from_env()
    if not features.mcp:
        mcp_settings = None
    elif mcp_settings is None:
        mcp_settings = mcp_settings_from_env(public_url, features)
    application = FastAPI(title="Mailroom API", lifespan=_lifespan)
    application.state.features = features
    application.state.public_url = public_url
    application.state.mcp_settings = mcp_settings
    application.state.mcp_verifier = mcp_verifier
    application.state.mcp_oidc_transport = mcp_oidc_transport
    mcp = McpEndpoint(lambda: app_context(application.state)) if features.mcp else None
    application.state.mcp = mcp
    application.add_exception_handler(Refusal, _refusal_answer)
    application.state.login_throttle = LoginThrottle()
    application.state.sso_start_limit = sso_start_limit()
    application.add_middleware(CrossSiteRequestMiddleware)
    if features.mail:
        application.add_middleware(NotStored, prefix=f"/api/v1{mail_router.prefix}/")
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
    public_v1.include_router(sso_router)
    application.include_router(public_v1)

    # Every other route goes here.
    protected_v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_user)])
    protected_v1.include_router(own_password_router)
    protected_v1.include_router(users_router)
    protected_v1.include_router(accounts_router)
    protected_v1.include_router(identities_router)
    if features.filters:
        protected_v1.include_router(filters_router)
    if features.mail_layer:
        protected_v1.include_router(limits_router)
    if features.mail:
        protected_v1.include_router(mail_router)
    if features.mcp:
        protected_v1.include_router(consent_router)
        protected_v1.include_router(grants_router)
    application.include_router(protected_v1)

    oauth_server = None
    if mcp is not None:
        # Exact routes: a mount would redirect /mcp to /mcp/.
        application.router.routes.append(Route("/mcp", mcp.endpoint))
        application.router.routes.append(Route(METADATA_PATH, mcp.metadata))
        oauth_server = _builtin_oauth_server(application, mcp_settings)
        if oauth_server is not None:
            application.router.routes.extend(oauth_server.routes())
    application.state.oauth_server = oauth_server
    return application


application = create_application()
