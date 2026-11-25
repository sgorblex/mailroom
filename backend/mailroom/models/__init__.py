"""ORM models: import every model module here so ``Base.metadata`` sees it (autogenerate)."""

# The authorization server keeps its models in its own package. A plain import: its module may be
# the one importing this package, still half-initialised.
import mailroom.oauth.models  # noqa: F401
from mailroom.models.account import AccountFilterSettings, AccountHandled
from mailroom.models.api_token import ApiToken
from mailroom.models.audit import AuditRecord
from mailroom.models.base import Base
from mailroom.models.filters import (
    Filter,
    FilterMatchDay,
    FilterRun,
    MutedThread,
    RuleSet,
    TransferLedger,
)
from mailroom.models.instance import InstanceSetting
from mailroom.models.mail_index import MailIndexFolder, MailIndexMessage, MailViewInterest
from mailroom.models.notifications import MailNotificationFolders
from mailroom.models.remote_content import RemoteContentSender
from mailroom.models.sender_view import SenderViewPreference
from mailroom.models.sends import ScheduledSend
from mailroom.models.signatures import IdentitySignature
from mailroom.models.snoozes import Snooze
from mailroom.models.sso import ExternalIdentity, Intent, SsoLoginState
from mailroom.models.user import User, UserSession
from mailroom.models.vacation import Vacation, VacationReplied
from mailroom.models.views import SavedView

__all__ = [
    "AccountFilterSettings",
    "AccountHandled",
    "ApiToken",
    "AuditRecord",
    "Base",
    "ExternalIdentity",
    "Filter",
    "FilterMatchDay",
    "FilterRun",
    "IdentitySignature",
    "InstanceSetting",
    "Intent",
    "MailIndexFolder",
    "MailIndexMessage",
    "MailNotificationFolders",
    "MailViewInterest",
    "MutedThread",
    "RemoteContentSender",
    "RuleSet",
    "SavedView",
    "ScheduledSend",
    "SenderViewPreference",
    "Snooze",
    "SsoLoginState",
    "TransferLedger",
    "User",
    "UserSession",
    "Vacation",
    "VacationReplied",
]
