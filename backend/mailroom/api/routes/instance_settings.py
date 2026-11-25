"""What admins choose for the whole instance (``mailroom.config.instance_settings``)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from mailroom.api.deps import CurrentAdmin, DbSession, require_admin
from mailroom.config.instance_settings import (
    InstanceSettings,
    InstanceSettingsChange,
    change_instance_settings,
    stored_settings,
)

logger = logging.getLogger(__name__)

instance_settings_router = APIRouter(
    prefix="/admin/settings", dependencies=[Depends(require_admin)]
)


@instance_settings_router.get("")
def settings(db: DbSession) -> InstanceSettings:
    return stored_settings(db)


@instance_settings_router.put("")
def change_settings(
    body: InstanceSettingsChange, db: DbSession, admin: CurrentAdmin
) -> InstanceSettings:
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
    return stored_settings(db)
