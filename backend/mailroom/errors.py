"""Why the app will not do what was asked, independent of how it was asked: a front end (the REST
routes, ``mailroom.api.refusals``) maps a ``Refusal``'s ``kind`` to its own wording of the case."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

RefusalKind = Literal[
    "not_found", "invalid", "conflict", "busy", "mail", "unavailable", "timeout", "failed"
]
"""``not_found``: no such thing of this user's. ``invalid``: the request or the stored thing does
not hold. ``conflict``: the current state is in the way. ``busy``: one of the user's is still going.
``mail``: the mailbox would not answer or let us in. ``unavailable``: the app cannot now (a missing
key, a lock held elsewhere). ``timeout``: it took too long. ``failed``: something unexpected, its
text kept out."""


class Refusal(Exception):  # noqa: N818 -- the outcome of a request, not a bug
    """``code`` names the case for a client that words it itself; ``message`` is safe to show.
    ``issues``: the fields at fault, for an account a client sends (``invalid_account``).
    ``coded``: a ``not_found`` that keeps its ``code`` for the client, unlike a missing id, which
    is answered by its message alone (a dry run's ``account_not_found``: the account went after
    the rule set was found).
    ``extra``: more of the answer's ``detail``, for a client that acts on the refusal."""

    def __init__(
        self,
        kind: RefusalKind,
        code: str,
        message: str,
        *,
        issues: Sequence[Mapping[str, object]] | None = None,
        coded: bool = False,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: RefusalKind = kind
        self.code = code
        self.message = message
        self.issues = None if issues is None else [dict(issue) for issue in issues]
        self.coded = coded
        self.extra = {} if extra is None else dict(extra)

    def __repr__(self) -> str:
        return f"Refusal({self.kind!r}, {self.code!r}, {self.message!r})"


SECRETS_KEY_MISSING = "secrets_key_missing"


def mail_refusal(code: str, message: str) -> Refusal:
    """A mailbox failure (``MailFailure``'s or a run's code) as a refusal: ``unavailable`` without
    the secrets key, ``mail`` otherwise."""
    return Refusal("unavailable" if code == SECRETS_KEY_MISSING else "mail", code, message)
