"""What admins choose for the whole instance (``mailroom.config.instance_settings``)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from fastapi import APIRouter, Depends

from mailroom.api.deps import ConfiguredSso, CurrentAdmin, DbSession, require_admin
from mailroom.auth.password_sign_in import (
    ExternalSignIn,
    external_sign_in,
    password_sign_in_forced,
)
from mailroom.config.instance_settings import (
    InstanceSettings,
    InstanceSettingsChange,
    change_instance_settings,
    stored_settings,
)
from mailroom.errors import Refusal

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.auth.sso import Sso
    from mailroom.models import User

logger = logging.getLogger(__name__)

instance_settings_router = APIRouter(
    prefix="/admin/settings", dependencies=[Depends(require_admin)]
)


class InstanceSettingsOut(InstanceSettings):
    external_sign_in: ExternalSignIn
    """Whether the admin who asks may turn ``external_sign_in_only`` on."""
    password_sign_in_forced: bool
    """The environment keeps passwords working whatever ``external_sign_in_only`` says."""


def _answer(db: Session, sso: Sso | None, admin: User) -> InstanceSettingsOut:
    return InstanceSettingsOut(
        **stored_settings(db).model_dump(),
        external_sign_in=external_sign_in(db, sso, admin),
        password_sign_in_forced=password_sign_in_forced(),
    )


@instance_settings_router.get("")
def settings(db: DbSession, admin: CurrentAdmin, sso: ConfiguredSso) -> InstanceSettingsOut:
    return _answer(db, sso, admin)


_NOT_OFFERED: Final[dict[ExternalSignIn, tuple[str, str]]] = {
    "not_configured": (
        "external_sign_in_not_configured",
        "No identity provider is configured: nobody could sign in.",
    ),
    "not_linked": (
        "external_sign_in_not_linked",
        "Link your own sign-in on your profile first, or you could not sign in again.",
    ),
}


def _check_external_sign_in(db: Session, sso: Sso | None, admin: User) -> None:
    """Refuse an admin turning external sign-in only on who could not sign in with it."""
    refusal = _NOT_OFFERED.get(external_sign_in(db, sso, admin))
    if refusal is not None:
        code, message = refusal
        logger.info(
            "external_sign_in_only_refused",
            extra={
                "event": "external_sign_in_only_refused",
                "user_id": admin.id,
                "outcome": code,
            },
        )
        raise Refusal("conflict", code, message)


@instance_settings_router.put("")
def change_settings(
    body: InstanceSettingsChange, db: DbSession, admin: CurrentAdmin, sso: ConfiguredSso
) -> InstanceSettingsOut:
    if body.external_sign_in_only and not stored_settings(db).external_sign_in_only:
        _check_external_sign_in(db, sso, admin)
    changed = change_instance_settings(db, admin, body)
    if changed:
        logger.info(
            "instance_settings_changed",
            extra={
                "event": "instance_settings_changed",
                "user_id": admin.id,
                "settings": changed,
            },
        )
    if "external_sign_in_only" in changed:
        logger.warning(
            "external_sign_in_only_changed",
            extra={
                "event": "external_sign_in_only_changed",
                "user_id": admin.id,
                "outcome": "on" if body.external_sign_in_only else "off",
            },
        )
    return _answer(db, sso, admin)
