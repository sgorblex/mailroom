"""What more than one of the mail routes' modules uses: ``mail_router``, which each concern's
module registers on; the ``NotStored`` middleware and its ``Cache-Control`` value; the pattern of a
part number; the types of an account id and of a ref (``Ref`` in a path, ``BatchRef`` in a body);
and the address and part models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final

from fastapi import APIRouter, Path
from pydantic import BaseModel, StringConstraints
from starlette.datastructures import MutableHeaders

from mailroom.mail import service

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from mailroom_core.render import Address, PartInfo

_NO_STORE: Final = "private, no-store"
_PART: Final = r"^\d+(\.\d+)*$"


class NotStored:
    """What answers under ``prefix`` is the user's mail: no cache keeps it, whatever the status. As
    middleware outside the cross-site check, so that a refusal, the 401 of a missing session, a
    422 and that check's 403 carry ``Cache-Control: private, no-store`` too."""

    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return

        async def not_stored(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = _NO_STORE
            await send(message)

        await self.app(scope, receive, not_stored)


mail_router = APIRouter(prefix="/mail")

AccountId = Annotated[str, Path(min_length=1, max_length=64)]
Ref = Annotated[str, Path(min_length=1, max_length=service.MAX_REF_LENGTH)]


class MailAddress(BaseModel):
    name: str
    address: str

    @classmethod
    def of(cls, address: Address) -> MailAddress:
        return cls(name=address.name or "", address=address.address or "")


class MessagePart(BaseModel):
    part: str
    name: str
    type: str
    size: int

    @classmethod
    def of(cls, info: PartInfo) -> MessagePart:
        return cls(part=info.part, name=info.name or "", type=info.type, size=info.size)


BatchRef = Annotated[str, StringConstraints(min_length=1, max_length=service.MAX_REF_LENGTH)]
