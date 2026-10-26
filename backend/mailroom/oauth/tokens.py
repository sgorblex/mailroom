"""Grants, authorization codes and tokens. Codes and tokens are opaque random strings; only
their SHA-256 is stored, so revoking (deleting the grant) takes effect at once and no signing key
exists to manage."""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update

from mailroom.oauth.clients import touch_client
from mailroom.oauth.config import ACCESS_TOKEN_TTL, CODE_TTL, REFRESH_TOKEN_TTL
from mailroom.oauth.models import OAuthAuthorizationCode, OAuthGrant, OAuthToken

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: A grant's ``last_used_at`` is written at most this often.
_TOUCH_EVERY = timedelta(minutes=1)


def new_secret() -> str:
    """256 bits, URL-safe."""
    return secrets.token_urlsafe(32)


def secret_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    scopes: list[str]


@dataclass(frozen=True, slots=True)
class VerifiedAccess:
    """Who a live access token speaks for."""

    user_id: int
    client_id: str
    grant_id: int
    resource: str
    scopes: list[str]
    expires_at: int


@dataclass(frozen=True, slots=True)
class RefreshGrant:
    """A refresh token that has not been spent, and the grant it belongs to."""

    client_id: str
    grant_id: int
    user_id: int
    resource: str
    scopes: list[str]
    expires_at: int


def sweep_expired(db: Session, now: datetime) -> None:
    """Expired codes and tokens, and grants left with neither (a grant whose code was never
    exchanged, or whose refresh token lapsed)."""
    db.execute(delete(OAuthAuthorizationCode).where(OAuthAuthorizationCode.expires_at <= now))
    db.execute(delete(OAuthToken).where(OAuthToken.expires_at <= now))
    db.execute(
        delete(OAuthGrant).where(
            OAuthGrant.created_at < now - CODE_TTL,
            ~select(OAuthToken.token_hash).where(OAuthToken.grant_id == OAuthGrant.id).exists(),
            ~select(OAuthAuthorizationCode.code_hash)
            .where(OAuthAuthorizationCode.grant_id == OAuthGrant.id)
            .exists(),
        )
    )


def create_grant_with_code(
    db: Session,
    *,
    user_id: int,
    client_id: str,
    redirect_uri: str,
    redirect_uri_explicit: bool,
    code_challenge: str,
    resource: str,
    scopes: list[str],
) -> str:
    """A new grant and its authorization code (raw, returned). Leaves the commit to the caller."""
    now = datetime.now(UTC)
    sweep_expired(db, now)
    grant = OAuthGrant(user_id=user_id, client_id=client_id, resource=resource, scopes=scopes)
    db.add(grant)
    db.flush()
    code = new_secret()
    db.add(
        OAuthAuthorizationCode(
            code_hash=secret_hash(code),
            client_id=client_id,
            user_id=user_id,
            grant_id=grant.id,
            redirect_uri=redirect_uri,
            redirect_uri_explicit=redirect_uri_explicit,
            code_challenge=code_challenge,
            resource=resource,
            scopes=scopes,
            expires_at=now + CODE_TTL,
        )
    )
    return code


def take_code(db: Session, client_id: str, raw_code: str) -> OAuthAuthorizationCode | None:
    """The code, marked used as it is read: it works once, and a failed check after this (a wrong
    PKCE verifier, a different redirect URI) burns it rather than leaving it open to guesses. A
    used code stays until it expires; presented again, it ends the grant it was issued in and the
    tokens issued from it (RFC 6749, 4.1.2)."""
    code_hash = secret_hash(raw_code)
    row: OAuthAuthorizationCode | None = db.scalar(
        update(OAuthAuthorizationCode)
        .where(
            OAuthAuthorizationCode.code_hash == code_hash,
            OAuthAuthorizationCode.client_id == client_id,
            OAuthAuthorizationCode.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
        .returning(OAuthAuthorizationCode)
    )
    if row is not None:
        db.commit()
        return row
    replayed = db.scalar(
        select(OAuthAuthorizationCode.grant_id).where(
            OAuthAuthorizationCode.code_hash == code_hash,
            OAuthAuthorizationCode.used_at.is_not(None),
        )
    )
    if replayed is None:
        db.rollback()
        return None
    db.execute(delete(OAuthGrant).where(OAuthGrant.id == replayed))
    db.commit()
    logger.warning(
        "oauth_authorization_code_reused",
        extra={
            "event": "oauth_authorization_code_reused",
            "grant_id": replayed,
            "client_id": client_id,
        },
    )
    return None


def _issue(db: Session, grant: OAuthGrant, now: datetime) -> IssuedTokens:
    access, refresh = new_secret(), new_secret()
    db.add_all(
        [
            OAuthToken(
                token_hash=secret_hash(access),
                grant_id=grant.id,
                kind="access",
                expires_at=now + ACCESS_TOKEN_TTL,
            ),
            OAuthToken(
                token_hash=secret_hash(refresh),
                grant_id=grant.id,
                kind="refresh",
                expires_at=now + REFRESH_TOKEN_TTL,
            ),
        ]
    )
    grant.last_used_at = now
    touch_client(db, grant.client_id)
    db.flush()
    sweep_expired(db, now)
    db.commit()
    return IssuedTokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
        scopes=list(grant.scopes),
    )


def issue_for_code(db: Session, grant_id: int) -> IssuedTokens | None:
    """Tokens for the grant a code was issued in; ``None`` when the grant is gone (revoked)."""
    now = datetime.now(UTC)
    grant = db.get(OAuthGrant, grant_id, with_for_update=True)
    if grant is None:
        db.commit()
        return None
    tokens = _issue(db, grant, now)
    _log_issued(grant, "authorization_code")
    return tokens


def _log_issued(grant: OAuthGrant, trigger: str) -> None:
    logger.info(
        "oauth_token_issued",
        extra={
            "event": "oauth_token_issued",
            "user_id": grant.user_id,
            "client_id": grant.client_id,
            "grant_id": grant.id,
            "trigger": trigger,
        },
    )


def _end_grant_for_reuse(db: Session, grant_id: int, presented_by: str) -> None:
    """A spent refresh token came back: someone holds a copy. The whole grant ends, the
    legitimate chain included, so a stolen token stops working too (OAuth 2.0 Security BCP,
    4.14.2)."""
    db.execute(delete(OAuthGrant).where(OAuthGrant.id == grant_id))
    db.commit()
    logger.warning(
        "oauth_refresh_token_reused",
        extra={
            "event": "oauth_refresh_token_reused",
            "grant_id": grant_id,
            "client_id": presented_by,
        },
    )


def verify_refresh_token(db: Session, client_id: str, raw_token: str) -> RefreshGrant | None:
    """The live refresh token ``raw_token`` of ``client_id``. A spent one ends its grant."""
    now = datetime.now(UTC)
    found = db.execute(
        select(OAuthToken, OAuthGrant)
        .join(OAuthGrant, OAuthGrant.id == OAuthToken.grant_id)
        .where(OAuthToken.token_hash == secret_hash(raw_token), OAuthToken.kind == "refresh")
    ).one_or_none()
    if found is None:
        return None
    token, grant = found
    if token.used_at is not None:
        _end_grant_for_reuse(db, grant.id, client_id)
        return None
    if grant.client_id != client_id or token.expires_at <= now:
        return None
    return RefreshGrant(
        client_id=grant.client_id,
        grant_id=grant.id,
        user_id=grant.user_id,
        resource=grant.resource,
        scopes=list(grant.scopes),
        expires_at=int(token.expires_at.timestamp()),
    )


def rotate_refresh_token(db: Session, client_id: str, raw_token: str) -> IssuedTokens | None:
    """New tokens for a refresh token, which is spent in the same statement that checks it: of two
    concurrent uses, the second is reuse."""
    now = datetime.now(UTC)
    spent = db.scalar(
        update(OAuthToken)
        .where(
            OAuthToken.token_hash == secret_hash(raw_token),
            OAuthToken.kind == "refresh",
            OAuthToken.used_at.is_(None),
            OAuthToken.expires_at > now,
        )
        .values(used_at=now)
        .returning(OAuthToken.grant_id)
    )
    if spent is None:
        db.rollback()
        reused = db.scalar(
            select(OAuthToken.grant_id).where(
                OAuthToken.token_hash == secret_hash(raw_token), OAuthToken.used_at.is_not(None)
            )
        )
        if reused is not None:
            _end_grant_for_reuse(db, reused, client_id)
        return None
    grant = db.get(OAuthGrant, spent, with_for_update=True)
    if grant is None or grant.client_id != client_id:
        db.rollback()
        return None
    tokens = _issue(db, grant, now)
    _log_issued(grant, "refresh_token")
    return tokens


def verify_access_token(db: Session, raw_token: str) -> VerifiedAccess | None:
    """Who the access token ``raw_token`` speaks for, or ``None`` when it is unknown, expired or
    its grant was revoked. The grant's ``last_used_at`` follows, at most once a minute."""
    now = datetime.now(UTC)
    found = db.execute(
        select(OAuthToken.expires_at, OAuthGrant)
        .join(OAuthGrant, OAuthGrant.id == OAuthToken.grant_id)
        .where(
            OAuthToken.token_hash == secret_hash(raw_token),
            OAuthToken.kind == "access",
            OAuthToken.expires_at > now,
        )
    ).one_or_none()
    if found is None:
        return None
    expires_at, grant = found
    if grant.last_used_at is None or grant.last_used_at < now - _TOUCH_EVERY:
        db.execute(update(OAuthGrant).where(OAuthGrant.id == grant.id).values(last_used_at=now))
        db.commit()
    return VerifiedAccess(
        user_id=grant.user_id,
        client_id=grant.client_id,
        grant_id=grant.id,
        resource=grant.resource,
        scopes=list(grant.scopes),
        expires_at=int(expires_at.timestamp()),
    )


def revoke_grant(db: Session, grant_id: int, *, user_id: int | None = None) -> bool:
    """End a grant and every token of it. Whether one ended. With ``user_id``, only that user's
    grant: another user's id ends nothing."""
    statement = delete(OAuthGrant).where(OAuthGrant.id == grant_id)
    if user_id is not None:
        statement = statement.where(OAuthGrant.user_id == user_id)
    gone = db.execute(statement.returning(OAuthGrant.user_id, OAuthGrant.client_id)).first()
    db.commit()
    if gone is None:
        return False
    logger.info(
        "oauth_grant_revoked",
        extra={
            "event": "oauth_grant_revoked",
            "user_id": gone.user_id,
            "client_id": gone.client_id,
            "grant_id": grant_id,
        },
    )
    return True
