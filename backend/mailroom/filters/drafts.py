"""Filter drafts: a filter spec the filter editor starts from, made from a message or from a
search. A draft is only offered: nothing is saved until the user adds the filter, whose spec is
then checked as any other (``service.checked_spec``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel

from mailroom.errors import Refusal
from mailroom.filters.providers import names_public_provider, public_provider
from mailroom.filters.service import Spec, checked_spec
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from mailroom_core.mail import MessageView, SearchQuery

DRAFT_ACTION: Final[Spec] = {"type": "no_op"}
"""A draft does nothing until the user picks what it does."""
MAX_NAME: Final = 200
NOTHING_TO_FILTER: Final = "Nothing here that a filter can match on."


class MessageDraft(BaseModel):
    spec: Spec
    """Criterion ``criteria[0]``, action ``no_op``."""
    criteria: list[Spec]
    """Every criterion the message offers, narrowest first: its List-Id (``list_is``) when it
    has one, its sender's address (``from_is``), and the sender's domain (``from_domain``)
    unless it is a public mail provider's."""
    name: str
    """The list's name or identifier, else the sender's name or address."""


class SearchDraft(BaseModel):
    spec: Spec
    dropped: list[str]
    """The search fields set that no criterion matches as the search does, by their name in the
    search's body (``from``, ``text``, ``since``…), and a ``from`` naming a public mail
    provider's whole domain, which a filter may not."""


def _criterion(type_: str, **args: str) -> Spec:
    return {"type": type_, "args": args}


def _normalized(criterion: Spec) -> Spec:
    """``criterion`` as the filter engine serialises it; the refusals of ``checked_spec``."""
    normalized: Spec = checked_spec({"criterion": criterion, "action": DRAFT_ACTION})["criterion"]
    return normalized


def _name(*candidates: str | None) -> str:
    for candidate in candidates:
        if candidate:
            cleaned = clean_header(candidate, MAX_NAME)
            if cleaned:
                return cleaned
    return ""


def message_draft(view: MessageView) -> MessageDraft:
    """The draft for the message read as ``view``: ``nothing_to_filter`` when it has neither a
    List-Id nor a From address."""
    found: list[Spec] = []
    listed = view.headers.mailing_list
    list_id = listed.id if listed is not None else None
    if list_id:
        found.append(_criterion("list_is", list_id=list_id))
    sender = next((a for a in view.sender if a.address and "@" in a.address), None)
    address = sender.address if sender is not None else None
    if address:
        found.append(_criterion("from_is", addr=address))
        domain = address.rpartition("@")[2]
        if not public_provider(domain):
            found.append(_criterion("from_domain", domain=domain))
    criteria: list[Spec] = []
    for criterion in found:
        try:
            criteria.append(_normalized(criterion))
        except Refusal:
            continue  # a field the engine will not load (a domain that is no host name)
    if not criteria:
        raise Refusal("invalid", "nothing_to_filter", NOTHING_TO_FILTER)
    name = _name(
        listed.name if list_id and listed is not None else None,
        list_id,
        sender.name if sender is not None else None,
        address,
    )
    return MessageDraft(
        spec={"criterion": criteria[0], "action": DRAFT_ACTION},
        criteria=criteria,
        name=name or criteria[0]["type"],
    )


def _flag(on: bool | None, yes: str, no: str) -> Spec | None:
    return None if on is None else _criterion(yes if on else no)


def search_draft(query: SearchQuery) -> SearchDraft:
    """The draft for a search: what it asks of From, To, Subject and the flags, all of them
    (``and``); ``nothing_to_filter`` when none of its fields has a criterion."""
    sender = query.sender
    if sender is not None and names_public_provider(sender, pattern=False):
        sender = None
    criteria = [
        criterion
        for criterion in (
            None if sender is None else _criterion("from_contains", addr=sender),
            None if query.to is None else _criterion("to_contains", addr=query.to),
            None
            if query.subject is None
            else _criterion("subject_contains", substring=query.subject),
            _flag(query.unread, "is_unread", "is_read"),
            _flag(query.flagged, "is_starred", "is_unstarred"),
            _flag(query.to_sort, "is_to_sort", "is_not_to_sort"),
        )
        if criterion is not None
    ]
    dropped = [
        field
        for field, value in (
            ("from", query.sender if sender is None else None),
            ("text", query.text),
            ("since", query.since),
            ("before", query.before),
            ("has_attachment", query.has_attachment or None),
            ("gmail_query", query.gmail_query),
        )
        if value is not None
    ]
    if not criteria:
        extra = {"dropped": dropped}
        raise Refusal("invalid", "nothing_to_filter", NOTHING_TO_FILTER, extra=extra)
    criterion = criteria[0] if len(criteria) == 1 else {"and": criteria}
    spec = checked_spec({"criterion": criterion, "action": DRAFT_ACTION})
    return SearchDraft(spec=spec, dropped=dropped)
