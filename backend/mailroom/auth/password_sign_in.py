"""Whether users sign in with their password, or through the identity provider alone.

An admin turns password sign-in off with the instance setting ``external_sign_in_only``. It then
stays off unless the environment says otherwise (the way back in when the provider breaks) or no
provider is configured, since nobody could sign in at all. Specification:
``docs/requirements/nonfunctional-requirements.md``, *External sign-in only*.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final, Literal

from mailroom.auth.sso import provider_identities
from mailroom.config.instance_settings import stored_settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.auth.sso import Sso
    from mailroom.models import User

logger = logging.getLogger(__name__)

PASSWORD_SIGN_IN_ENV: Final = "MAILROOM_PASSWORD_SIGN_IN"

ExternalSignIn = Literal["available", "not_configured", "not_linked"]
"""Whether an admin may turn external sign-in only on: not without a provider, nor without a
sign-in of their own linked from it."""


def _from_env() -> str:
    return os.environ.get(PASSWORD_SIGN_IN_ENV, "").strip().lower()


def password_sign_in_forced() -> bool:
    """``MAILROOM_PASSWORD_SIGN_IN=on``: passwords work whatever the instance setting says."""
    return _from_env() == "on"


def warn_about_environment() -> None:
    """At startup: say that the environment overrides the setting, or that its value is ignored."""
    if password_sign_in_forced():
        logger.warning(
            "%s=on: passwords work even with external sign-in only.", PASSWORD_SIGN_IN_ENV
        )
    elif _from_env():
        logger.warning("%s is ignored: it is on or unset.", PASSWORD_SIGN_IN_ENV)


def password_sign_in(db: Session, sso: Sso | None) -> bool:
    """Whether passwords work: signing in with one, changing one's own, resetting a user's. Read
    from the database each time, so a change holds at once in every process."""
    if sso is None or password_sign_in_forced():
        return True
    return not stored_settings(db).external_sign_in_only


def external_sign_in(db: Session, sso: Sso | None, admin: User) -> ExternalSignIn:
    if sso is None:
        return "not_configured"
    return "available" if provider_identities(db, sso, admin.id) else "not_linked"
