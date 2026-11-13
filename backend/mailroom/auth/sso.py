"""Web SSO: signing in, or linking a sign-in, through an OpenID Connect provider; with
``MAILROOM_MCP_AUTH=oidc``, also the sign-in an MCP client's authorization request goes through
before the consent screen (``mcp``).

Authorization code flow with PKCE and a nonce. ``begin`` stores the flow under the SHA-256 of its
``state`` and hands back a browser binding token for a ``SameSite=Lax`` cookie; ``complete`` takes
the row once (single use), checks the cookie against it — so a callback another site sends the
browser to (login CSRF) is refused — exchanges the code and checks the ID token. Only an existing
user signs in: through a linked sign-in, or, with ``MAILROOM_OIDC_LINK_CLAIM``, a
``preferred_username`` equal to their username on first use. Nobody is created.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal, get_args
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from mailroom.auth.oidc import OidcError, OidcProvider, checked_provider_url
from mailroom.auth.throttle import RateLimit
from mailroom.auth.users import user_by_username
from mailroom.models import ExternalIdentity, Intent, SsoLoginState, User

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.orm import Session

    from mailroom.config.public_url import PublicUrl

logger = logging.getLogger(__name__)

ISSUER_ENV = "MAILROOM_OIDC_ISSUER"
CLIENT_ID_ENV = "MAILROOM_OIDC_CLIENT_ID"
CLIENT_SECRET_ENV = "MAILROOM_OIDC_CLIENT_SECRET"
NAME_ENV = "MAILROOM_OIDC_NAME"
LINK_CLAIM_ENV = "MAILROOM_OIDC_LINK_CLAIM"

START_PATH: Final = "/api/v1/auth/oidc/start"
CALLBACK_PATH: Final = "/api/v1/auth/oidc/callback"
MCP_START_PATH: Final = f"{START_PATH}?intent=mcp"
"""Where the built-in authorization server sends the browser in ``oidc`` mode."""
BINDING_COOKIE_NAME: Final = "mailroom_sso_binding"
BINDING_COOKIE_PATH: Final = "/api/v1/auth/oidc"
"""The binding cookie only goes back to the SSO routes."""
SCOPES: Final = "openid profile email"
STATE_TTL: Final = timedelta(minutes=10)
MAX_PENDING: Final = 10_000
"""Flows under way at once; starting one needs no session, so the table is capped."""
MAX_PENDING_PER_CLIENT: Final = 20
"""Flows under way at once from one client (``client_key``), so that one cannot fill the table."""
STARTS_PER_MINUTE: Final = 10
"""Flows one client (``client_key``) may start a minute (``sso_start_limit``)."""
MAX_RETURN_TO_LENGTH: Final = 512
MAX_STATE_LENGTH: Final = 512
MAX_CODE_LENGTH: Final = 2048
"""Longer ``state`` or ``code`` values in a callback are refused unread."""
DEFAULT_NAME: Final = "SSO"

LinkClaim = Literal["preferred_username"]
"""The claims that may name a username. Not ``email``: a username never holds an ``@``."""


@dataclass(frozen=True, slots=True)
class SsoSettings:
    issuer: str
    client_id: str
    client_secret: str
    name: str
    """The sign-in button's label."""
    link_claim: LinkClaim | None
    redirect_uri: str


@dataclass(frozen=True, slots=True)
class Sso:
    """Web SSO as configured: on ``app.state.sso``, or ``None`` there when it is off."""

    settings: SsoSettings
    provider: OidcProvider


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def sso_settings_from_env(public_url: PublicUrl | None) -> SsoSettings | None:
    """The configured SSO; ``None`` (with a warning when half set up) unless the issuer, the
    client and ``MAILROOM_PUBLIC_URL`` (the callback's origin) are all there."""
    issuer, client_id, client_secret = (
        _env(ISSUER_ENV),
        _env(CLIENT_ID_ENV),
        _env(CLIENT_SECRET_ENV),
    )
    if not (issuer or client_id or client_secret):
        return None
    if not (issuer and client_id and client_secret):
        logger.warning(
            "SSO is off: set %s, %s and %s together.", ISSUER_ENV, CLIENT_ID_ENV, CLIENT_SECRET_ENV
        )
        return None
    if public_url is None:
        logger.warning("SSO is off: it needs MAILROOM_PUBLIC_URL for its callback.")
        return None
    try:
        checked_provider_url(issuer, what=ISSUER_ENV)
    except OidcError as exc:
        logger.warning("SSO is off: %s.", exc.message)
        return None
    raw_claim = _env(LINK_CLAIM_ENV)
    link_claim: LinkClaim | None = None
    if raw_claim in get_args(LinkClaim):
        link_claim = raw_claim  # type: ignore[assignment]  # checked against LinkClaim above
    elif raw_claim:
        logger.warning(
            "%s=%r is ignored: it is preferred_username or unset. Sign-ins link from the "
            "profile only.",
            LINK_CLAIM_ENV,
            raw_claim,
        )
    return SsoSettings(
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        name=_env(NAME_ENV) or DEFAULT_NAME,
        link_claim=link_claim,
        redirect_uri=public_url.url(CALLBACK_PATH),
    )


def sso_from_env(public_url: PublicUrl | None) -> Sso | None:
    settings = sso_settings_from_env(public_url)
    if settings is None:
        return None
    return Sso(settings=settings, provider=OidcProvider(settings.issuer))


def sso_start_limit() -> RateLimit:
    """``app.state.sso_start_limit``: ``STARTS_PER_MINUTE`` per client."""
    return RateLimit(STARTS_PER_MINUTE, 60.0)


class SsoError(Exception):
    """Why an SSO flow ended without signing in or linking. ``code`` goes back to the web app
    (``sso_error``), which words it; ``return_to`` is where to send the browser."""

    def __init__(self, code: str, return_to: str = "/") -> None:
        super().__init__(code)
        self.code = code
        self.return_to = return_to


def safe_return_to(raw: str | None, default: str = "/") -> str:
    """``raw`` when it is a path on this site (``/…``, not ``//host`` or ``/\\host``), else
    ``default``: the callback redirects there, so it must never lead off-site."""
    if not raw or len(raw) > MAX_RETURN_TO_LENGTH or not raw.startswith("/"):
        return default
    if raw.startswith(("//", "/\\")) or any(c == "\\" or ord(c) < 0x20 for c in raw):
        return default
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return default
    return raw


def with_query(path: str, **params: str) -> str:
    """``path`` (a safe return path) with ``params`` set in its query string."""
    parts = urlsplit(path)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in params]
    query.extend(params.items())
    return urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class Started:
    authorize_url: str
    binding: str
    """The raw browser binding token, for the ``SameSite=Lax`` cookie."""


def begin(
    db: Session,
    sso: Sso,
    *,
    intent: Intent,
    return_to: str,
    client: str,
    link_user_id: int | None = None,
) -> Started:
    """Store a new flow and return where to send the browser. ``link`` needs ``link_user_id``,
    the signed-in user; ``client`` is the starting client's ``client_key``. Raises ``OidcError``
    when the provider cannot be discovered and ``SsoError("busy")`` when too many flows are under
    way, in all or from ``client``."""
    if (intent == "link") != (link_user_id is not None):
        msg = "a link names its user, a sign-in none"
        raise ValueError(msg)
    metadata = sso.provider.metadata()
    now = datetime.now(UTC)
    db.execute(delete(SsoLoginState).where(SsoLoginState.expires_at <= now))
    pending = db.scalar(select(func.count()).select_from(SsoLoginState)) or 0
    theirs = (
        db.scalar(
            select(func.count())
            .select_from(SsoLoginState)
            .where(SsoLoginState.client_key == client)
        )
        or 0
    )
    if pending >= MAX_PENDING or theirs >= MAX_PENDING_PER_CLIENT:
        db.commit()
        raise SsoError("busy", return_to)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    binding = secrets.token_urlsafe(32)
    db.add(
        SsoLoginState(
            state_hash=_sha256(state),
            nonce=nonce,
            code_verifier=verifier,
            intent=intent,
            link_user_id=link_user_id,
            return_to=return_to,
            browser_binding_hash=_sha256(binding),
            client_key=client,
            expires_at=now + STATE_TTL,
        )
    )
    db.commit()
    params = {
        "response_type": "code",
        "client_id": sso.settings.client_id,
        "redirect_uri": sso.settings.redirect_uri,
        "scope": SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": _code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    endpoint = urlsplit(metadata.authorization_endpoint)
    query = parse_qsl(endpoint.query, keep_blank_values=True) + list(params.items())
    return Started(
        authorize_url=urlunsplit(endpoint._replace(query=urlencode(query))), binding=binding
    )


@dataclass(frozen=True, slots=True)
class Completed:
    user: User
    intent: Intent
    return_to: str
    linked: bool
    """A link was made by this flow (from the profile, or by the link claim)."""


def _take_state(db: Session, state: str | None) -> SsoLoginState:
    """The flow ``state`` names, deleted on the spot so it cannot be replayed whatever follows."""
    if not state or len(state) > MAX_STATE_LENGTH:
        raise SsoError("state_invalid")
    row = db.scalar(
        select(SsoLoginState).where(SsoLoginState.state_hash == _sha256(state)).with_for_update()
    )
    if row is None:
        raise SsoError("state_invalid")
    db.delete(row)
    db.commit()
    if row.expires_at <= datetime.now(UTC):
        raise SsoError("state_invalid", row.return_to)
    return row


def claimed_user(
    db: Session, claims: Mapping[str, Any], link_claim: LinkClaim | None
) -> User | None:
    """The existing user whose username ``link_claim``'s value is, exactly: no case folding nor
    trimming, so ``Alice`` never names ``alice``. ``None`` as well when that user already has a
    sign-in linked from this issuer: the claim links a first one only, never a second."""
    if link_claim is None:
        return None
    value = claims.get(link_claim)
    if not isinstance(value, str) or not value:
        return None
    user = user_by_username(db, value)
    if user is None or user.username != value:
        return None
    already = db.scalar(
        select(ExternalIdentity.id).where(
            ExternalIdentity.user_id == user.id, ExternalIdentity.issuer == claims["iss"]
        )
    )
    return None if already is not None else user


def _intent(stored: str) -> Intent:
    """A stored flow's intent (the table's check constraint allows no other)."""
    return "link" if stored == "link" else "mcp" if stored == "mcp" else "login"


def complete(
    db: Session,
    sso: Sso,
    *,
    state: str | None,
    code: str | None,
    error: str | None,
    binding: str | None,
) -> Completed:
    """Finish the flow the provider's callback names. Raises ``SsoError``."""
    row = _take_state(db, state)
    return_to = row.return_to
    if not binding or not hmac.compare_digest(_sha256(binding), row.browser_binding_hash):
        raise SsoError("browser_mismatch", return_to)
    if error:
        raise SsoError("denied", return_to)
    if not code or len(code) > MAX_CODE_LENGTH:
        raise SsoError("failed", return_to)
    settings = sso.settings
    try:
        id_token = sso.provider.exchange_code(
            code=code,
            redirect_uri=settings.redirect_uri,
            code_verifier=row.code_verifier,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        )
        claims = sso.provider.verify_id_token(
            id_token, client_id=settings.client_id, nonce=row.nonce
        )
    except OidcError as exc:
        logger.warning(
            "sso_failed: %s",
            exc.message,
            extra={"event": "sso_failed", "outcome": exc.code},
        )
        raise SsoError("unavailable" if exc.code == "unavailable" else "failed", return_to) from exc
    issuer, subject = claims["iss"], claims["sub"]
    identity = db.scalar(
        select(ExternalIdentity)
        .where(ExternalIdentity.issuer == issuer, ExternalIdentity.subject == subject)
        .with_for_update()
    )
    intent = _intent(row.intent)
    linked = False
    if intent == "link":
        user = db.get(User, row.link_user_id) if row.link_user_id is not None else None
        if user is None:
            raise SsoError("state_invalid", return_to)
        if identity is not None and identity.user_id != user.id:
            raise SsoError("identity_taken", return_to)
    else:
        found = (
            db.get(User, identity.user_id)
            if identity is not None
            else claimed_user(db, claims, settings.link_claim)
        )
        if found is None:
            logger.info("sso_not_linked", extra={"event": "sso_not_linked"})
            raise SsoError("not_linked", return_to)
        user = found
    now = datetime.now(UTC)
    if identity is None:
        identity = ExternalIdentity(user_id=user.id, issuer=issuer, subject=subject)
        db.add(identity)
        linked = True
    identity.last_used_at = now
    try:
        db.commit()
    except IntegrityError as exc:  # the same identity linked meanwhile
        db.rollback()
        raise SsoError("identity_taken", return_to) from exc
    if linked:
        logger.info(
            "sso_identity_linked",
            extra={
                "event": "sso_identity_linked",
                "user_id": user.id,
                "via": "profile" if intent == "link" else settings.link_claim,
            },
        )
    return Completed(user=user, intent=intent, return_to=return_to, linked=linked)


def linked_user_id(db: Session, issuer: str, subject: str) -> int | None:
    """The user whose linked sign-in is ``subject`` at ``issuer``, or None when nobody's is."""
    return db.scalar(
        select(ExternalIdentity.user_id).where(
            ExternalIdentity.issuer == issuer, ExternalIdentity.subject == subject
        )
    )


def user_identities(db: Session, user_id: int) -> list[ExternalIdentity]:
    return list(
        db.scalars(
            select(ExternalIdentity)
            .where(ExternalIdentity.user_id == user_id)
            .order_by(ExternalIdentity.created_at, ExternalIdentity.id)
        )
    )


def unlink(db: Session, user_id: int, identity_id: int) -> bool:
    """Remove one of ``user_id``'s linked sign-ins; ``False`` when they have no such one."""
    removed = db.execute(
        delete(ExternalIdentity)
        .where(ExternalIdentity.id == identity_id, ExternalIdentity.user_id == user_id)
        .returning(ExternalIdentity.id)
    ).first()
    db.commit()
    if removed is not None:
        logger.info(
            "sso_identity_unlinked", extra={"event": "sso_identity_unlinked", "user_id": user_id}
        )
    return removed is not None
