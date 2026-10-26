"""ORM models: import every model module here so ``Base.metadata`` sees it (autogenerate)."""

from mailroom.models.account import AccountFilterSettings
from mailroom.models.base import Base
from mailroom.models.filters import Filter, FilterRun, RuleSet
from mailroom.models.sso import ExternalIdentity, Intent, SsoLoginState
from mailroom.models.user import User, UserSession

__all__ = [
    "AccountFilterSettings",
    "Base",
    "ExternalIdentity",
    "Filter",
    "FilterRun",
    "Intent",
    "RuleSet",
    "SsoLoginState",
    "User",
    "UserSession",
]
