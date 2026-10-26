"""Clients: dynamic registration (DCR) restricted to the redirect allowlist, and client ID metadata
documents (CIMD) fetched from the allowed hosts. Every client is public: PKCE authenticates it,
as it does claude.ai and Claude Code, so no client secret is ever issued or stored."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import anyio
import httpx
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata
from pydantic import ValidationError
from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert

from mailroom.oauth.config import (
    CIMD_CACHE_TTL,
    CIMD_FAILURE_TTL,
    CIMD_STALE_GRACE,
    IDLE_CLIENT_TTL,
    MAX_CLIENT_NAME,
    MAX_REGISTERED_CLIENTS,
    NEVER_USED_CLIENT_TTL,
    SCOPE,
)
from mailroom.oauth.models import OAuthClient, OAuthGrant

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.auth.provider import RegistrationErrorCode
    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import Session

    from mailroom.oauth.config import OAuthSettings

logger = logging.getLogger(__name__)

CIMD_TIMEOUT_SECONDS = 5.0
CIMD_MAX_BYTES = 64 * 1024

CimdFailure = Literal["unreachable", "invalid"]

#: Failed fetches remembered at most; past it, the oldest is forgotten first.
_MAX_REMEMBERED_FAILURES = 1000


class RegistrationRefused(Exception):  # noqa: N818 -- reads as the case it is
    """A registration the allowlist or the cap refuses. (The SDK's own ``RegistrationError`` is a
    frozen dataclass, which cannot leave a ``with`` block: the provider raises it from this.)"""

    def __init__(self, code: RegistrationErrorCode, description: str) -> None:
        super().__init__(description)
        self.code: RegistrationErrorCode = code
        self.description = description


class CimdRefused(Exception):  # noqa: N818 -- reads as the case it is
    """A client ID metadata document that cannot be used; the message says why (for the log)."""


class CimdUnreachable(CimdRefused):
    """The document could not be fetched (timeout, transport error, a status other than 200), as
    opposed to fetched and found wrong: a cached copy may still serve."""


def public_client(info: OAuthClientMetadata, client_id: str) -> OAuthClientInformationFull:
    """``info`` as a public client of the one scope, whatever it asked for."""
    fields = info.model_dump(
        exclude={"client_id", "client_secret", "client_secret_expires_at", "scope"}
    )
    fields["token_endpoint_auth_method"] = "none"
    return OAuthClientInformationFull(**fields, client_id=client_id, scope=SCOPE)


def client_from_row(row: OAuthClient) -> OAuthClientInformationFull:
    return public_client(OAuthClientInformationFull.model_validate(row.metadata_), row.client_id)


def _metadata_problem(
    settings: OAuthSettings, info: OAuthClientMetadata
) -> tuple[RegistrationErrorCode, str] | None:
    """What is wrong with ``info``, as the registration error code and its description."""
    uris = [str(uri) for uri in info.redirect_uris or ()]
    if not uris:
        return "invalid_client_metadata", "at least one redirect_uri is required"
    refused = [uri for uri in uris if not settings.redirect_uri_allowed(uri)]
    if refused:
        return "invalid_redirect_uri", f"redirect_uri not allowed: {', '.join(refused[:5])}"
    if len(info.client_name or "") > MAX_CLIENT_NAME:
        return (
            "invalid_client_metadata",
            f"client_name is longer than {MAX_CLIENT_NAME} characters",
        )
    return None


def _row_values(info: OAuthClientInformationFull, kind: str) -> dict[str, Any]:
    assert info.client_id is not None
    return {
        "client_id": info.client_id,
        "kind": kind,
        "client_name": info.client_name,
        "redirect_uris": [str(uri) for uri in info.redirect_uris or ()],
        "metadata": info.model_dump(mode="json", exclude_none=True),
    }


def _no_grant() -> ColumnElement[bool]:
    return ~exists().where(OAuthGrant.client_id == OAuthClient.client_id)


def _sweep_idle_clients(db: Session, now: datetime) -> None:
    """Clients nobody holds a grant for: registered ones never used for ``NEVER_USED_CLIENT_TTL``
    or unused for ``IDLE_CLIENT_TTL``, fetched documents past their stale grace."""
    registered = OAuthClient.kind == "dcr"
    db.execute(
        delete(OAuthClient).where(
            _no_grant(),
            (
                registered
                & OAuthClient.last_used_at.is_(None)
                & (OAuthClient.created_at < now - NEVER_USED_CLIENT_TTL)
            )
            | (
                registered
                & (
                    func.coalesce(OAuthClient.last_used_at, OAuthClient.created_at)
                    < now - IDLE_CLIENT_TTL
                )
            )
            | ((OAuthClient.kind == "cimd") & (OAuthClient.fetched_at < now - CIMD_STALE_GRACE)),
        )
    )


def _evict_oldest_never_used(db: Session) -> str | None:
    """Make room in a full register: the oldest registered client that was never used and holds
    no grant goes (with any request of it still pending). Its ``client_id``, or ``None``."""
    oldest = (
        select(OAuthClient.client_id)
        .where(OAuthClient.kind == "dcr", OAuthClient.last_used_at.is_(None), _no_grant())
        .order_by(OAuthClient.created_at, OAuthClient.client_id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    return db.scalar(
        delete(OAuthClient).where(OAuthClient.client_id == oldest).returning(OAuthClient.client_id)
    )


def register_client(
    db: Session, settings: OAuthSettings, registered: OAuthClientInformationFull
) -> OAuthClientInformationFull:
    """Store a dynamically registered client, made public. A full register first evicts its oldest
    never-used client. Raises ``RegistrationRefused`` for a redirect URI off the allowlist, an
    overlong name, or a register full of clients in use."""
    client_id = registered.client_id or ""
    info = public_client(registered, client_id)
    problem = _metadata_problem(settings, info)
    if problem is None:
        _sweep_idle_clients(db, datetime.now(UTC))
        registered_count = db.scalar(
            select(func.count()).select_from(OAuthClient).where(OAuthClient.kind == "dcr")
        )
        if (registered_count or 0) >= MAX_REGISTERED_CLIENTS:
            evicted = _evict_oldest_never_used(db)
            if evicted is None:
                problem = ("invalid_client_metadata", "too many registered clients")
            else:
                logger.warning(
                    "oauth_client_evicted",
                    extra={"event": "oauth_client_evicted", "client_id": evicted},
                )
    if problem is not None:
        code, description = problem
        logger.warning(
            "oauth_client_registration_refused",
            extra={"event": "oauth_client_registration_refused", "reason": description[:300]},
        )
        raise RegistrationRefused(code, description)
    db.execute(insert(OAuthClient).values(**_row_values(info, "dcr")))
    db.commit()
    logger.info(
        "oauth_client_registered",
        extra={"event": "oauth_client_registered", "client_id": client_id, "kind": "dcr"},
    )
    return info


def is_cimd_client_id(client_id: str) -> bool:
    return client_id.startswith("https://")


def cimd_url_problem(settings: OAuthSettings, client_id: str) -> str | None:
    """Why ``client_id`` cannot name a metadata document here: https on the default port, a host
    in ``MAILROOM_OAUTH_CIMD_HOSTS``, a path, and no credentials, query or fragment."""
    parts = urlsplit(client_id)
    try:
        port = parts.port
    except ValueError:
        return "invalid port"
    if parts.scheme != "https" or port not in {None, 443}:
        return "not https on the default port"
    if (parts.hostname or "") not in settings.cimd_hosts:
        return "host not allowed"
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return "credentials, query or fragment"
    segments = parts.path.split("/")
    if parts.path in {"", "/"} or "." in segments or ".." in segments:
        return "no path, or a dot segment"
    return None


async def fetch_client_document(
    client_id: str, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """The JSON object at ``client_id``: no redirects, ``CIMD_TIMEOUT_SECONDS`` in all, at most
    ``CIMD_MAX_BYTES``. Raises ``CimdUnreachable`` when it cannot be fetched, ``CimdRefused`` when
    what came back is no JSON object."""
    body = bytearray()
    try:
        with anyio.fail_after(CIMD_TIMEOUT_SECONDS):
            async with (
                httpx.AsyncClient(
                    transport=transport, follow_redirects=False, timeout=CIMD_TIMEOUT_SECONDS
                ) as client,
                client.stream("GET", client_id, headers={"Accept": "application/json"}) as reply,
            ):
                if reply.status_code != httpx.codes.OK:
                    msg = f"status {reply.status_code}"
                    raise CimdUnreachable(msg)
                async for chunk in reply.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > CIMD_MAX_BYTES:
                        msg = "document too large"
                        raise CimdRefused(msg)
    except TimeoutError as exc:
        msg = "timed out"
        raise CimdUnreachable(msg) from exc
    except httpx.HTTPError as exc:
        msg = f"fetch failed: {type(exc).__name__}"
        raise CimdUnreachable(msg) from exc
    try:
        document = json.loads(body)
    except ValueError as exc:
        msg = "not JSON"
        raise CimdRefused(msg) from exc
    if not isinstance(document, dict):
        msg = "not a JSON object"
        raise CimdRefused(msg)
    return document


def cimd_client(
    settings: OAuthSettings, client_id: str, document: dict[str, Any]
) -> OAuthClientInformationFull:
    """The client a fetched document describes. Raises ``CimdRefused``."""
    if document.get("client_id") != client_id:
        msg = "client_id does not match the document's URL"
        raise CimdRefused(msg)
    method = document.get("token_endpoint_auth_method")
    if method not in {None, "none"}:
        msg = "not a public client"
        raise CimdRefused(msg)
    try:
        info = OAuthClientMetadata.model_validate(
            {key: value for key, value in document.items() if key != "client_id"}
        )
    except ValidationError as exc:
        msg = "invalid client metadata"
        raise CimdRefused(msg) from exc
    problem = _metadata_problem(settings, info)
    if problem is not None:
        raise CimdRefused(problem[1])
    return public_client(info, client_id)


class RecentFailures:
    """Client ID metadata document URLs whose last fetch failed, and how, for ``ttl`` seconds: a
    client that keeps naming a broken document does not make the server fetch it on every
    request. In memory, per process."""

    def __init__(
        self,
        ttl: float = CIMD_FAILURE_TTL.total_seconds(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._failed: dict[str, tuple[float, CimdFailure]] = {}

    def get(self, client_id: str) -> CimdFailure | None:
        now = self._clock()
        with self._lock:
            found = self._failed.get(client_id)
            if found is None:
                return None
            if found[0] <= now:
                del self._failed[client_id]
                return None
            return found[1]

    def remember(self, client_id: str, failure: CimdFailure) -> None:
        now = self._clock()
        with self._lock:
            self._failed.pop(client_id, None)
            if len(self._failed) >= _MAX_REMEMBERED_FAILURES:
                self._failed = {k: v for k, v in self._failed.items() if v[0] > now}
            while len(self._failed) >= _MAX_REMEMBERED_FAILURES:
                del self._failed[next(iter(self._failed))]
            self._failed[client_id] = (now + self._ttl, failure)

    def forget(self, client_id: str) -> None:
        with self._lock:
            self._failed.pop(client_id, None)


def cached_client(db: Session, client_id: str) -> OAuthClient | None:
    return db.get(OAuthClient, client_id)


def cimd_cache_fresh(row: OAuthClient, now: datetime) -> bool:
    return row.fetched_at is not None and row.fetched_at > now - CIMD_CACHE_TTL


def cimd_cache_usable_stale(row: OAuthClient, now: datetime) -> bool:
    """Whether ``row`` may still serve while its document cannot be fetched."""
    return row.fetched_at is not None and row.fetched_at > now - CIMD_STALE_GRACE


def save_cimd_client(db: Session, info: OAuthClientInformationFull) -> None:
    values = _row_values(info, "cimd")
    values["fetched_at"] = datetime.now(UTC)
    upsert = insert(OAuthClient).values(**values)
    db.execute(
        upsert.on_conflict_do_update(
            index_elements=[OAuthClient.client_id],
            set_={
                "client_name": upsert.excluded.client_name,
                "redirect_uris": upsert.excluded.redirect_uris,
                "metadata": upsert.excluded.metadata,
                "fetched_at": upsert.excluded.fetched_at,
            },
            where=OAuthClient.kind == "cimd",
        )
    )
    db.commit()


def touch_client(db: Session, client_id: str) -> None:
    """Record that ``client_id`` was just issued tokens (its idle clock restarts)."""
    row = db.get(OAuthClient, client_id)
    if row is not None:
        row.last_used_at = datetime.now(UTC)
