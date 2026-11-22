"""Each identity's signature (``app.identity_signature``), written by the user in the web client's
rich editor and kept as sanitised HTML with its text. A message Mailroom starts for the user (a
reply's or a forward's prefill, an agent's draft or send) carries the signature of the identity it
goes from in a marked block: in text below a ``-- `` line, in HTML in a ``div`` with
``render.SIGNATURE_MARK``, which the web client finds again to swap it when the From changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from mailroom.models import IdentitySignature
from mailroom_core import mail as core
from mailroom_core import render

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

MAX_SIGNATURE_CHARS: Final = 20_000
"""Of a signature's HTML as written."""
TEXT_MARK: Final = "-- \n"
"""What a signature follows in text: the usual delimiter line."""


@dataclass(frozen=True, slots=True)
class Signature:
    html: str
    text: str


def account_signatures(db: Session, user_id: int, account_id: int) -> dict[str, Signature]:
    """The signatures of the account's identities, by lowercase address; the caller checked the
    account is the user's."""
    rows = db.scalars(
        select(IdentitySignature).where(
            IdentitySignature.user_id == user_id, IdentitySignature.account_id == account_id
        )
    )
    return {row.address: Signature(row.html, row.text) for row in rows}


def set_signature(
    db: Session, user_id: int, account_id: int, address: str, html: str
) -> Signature | None:
    """Keeps ``html``, sanitised (images and marks dropped), as the identity's signature, with its
    text; one with no text is removed: None. The caller checked the identity is the account's
    and the account the user's."""
    cleaned = render.outgoing_html(html, data_images=False, signature_mark=False).html.strip()
    text = core.html_text(cleaned).strip()
    key = address.lower()
    if not text:
        db.execute(
            delete(IdentitySignature).where(
                IdentitySignature.user_id == user_id,
                IdentitySignature.account_id == account_id,
                IdentitySignature.address == key,
            )
        )
        db.commit()
        return None
    statement = insert(IdentitySignature).values(
        user_id=user_id, account_id=account_id, address=key, html=cleaned, text=text
    )
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[
                IdentitySignature.user_id,
                IdentitySignature.account_id,
                IdentitySignature.address,
            ],
            set_={
                "html": statement.excluded.html,
                "text": statement.excluded.text,
                "updated_at": statement.excluded.updated_at,
            },
        )
    )
    db.commit()
    return Signature(cleaned, text)


def text_block(signature: Signature) -> str:
    return f"{TEXT_MARK}{signature.text}"


def html_block(signature: Signature | None) -> str:
    """Empty without a signature: where the web client puts one if the From changes."""
    return f"<div {render.SIGNATURE_MARK}>{'' if signature is None else signature.html}</div>"


def signed_text(text: str, signature: Signature | None) -> str:
    """``text`` with the signature below it."""
    if signature is None:
        return text
    return f"{text}\n\n{text_block(signature)}" if text else text_block(signature)


def signed_html(html: str | None, signature: Signature | None) -> str | None:
    """``html`` with the signature below it; None without HTML."""
    if html is None or signature is None:
        return html
    return f"{html}{html_block(signature)}"
