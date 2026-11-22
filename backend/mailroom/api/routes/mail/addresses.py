"""The addresses a recipient field suggests as the user types, from the mail index
(``mailroom.mail.service.addresses``). What is typed travels in the body, never in a URL."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from mailroom.api.deps import Context, CurrentUser, DbSession
from mailroom.api.routes.mail._shared import MailAddress, mail_router
from mailroom.mail import service
from mailroom.mail.index.addresses import MAX_SUGGESTIONS


class AddressQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: Annotated[str, Field(min_length=1, max_length=service.MAX_PREFIX)]
    limit: Annotated[int, Field(ge=1, le=MAX_SUGGESTIONS)] = MAX_SUGGESTIONS


class SuggestedAddress(MailAddress):
    sent: bool
    """The user wrote to it; else it wrote to them."""


class SuggestedAddresses(BaseModel):
    addresses: list[SuggestedAddress]


@mail_router.post("/addresses")
def suggest_addresses(
    request: AddressQuery, ctx: Context, db: DbSession, user: CurrentUser
) -> SuggestedAddresses:
    found = service.suggest_addresses(ctx, db, user.id, request.q, limit=request.limit)
    return SuggestedAddresses(
        addresses=[
            SuggestedAddress(name=one.name, address=one.address, sent=one.sent) for one in found
        ]
    )
