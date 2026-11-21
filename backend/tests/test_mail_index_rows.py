"""The mail index's tables: rows kept with their folder's user, gone with the account and the
user, and the revision that makes them (``202611201200``) undone without touching a ``pg_trgm``
the database had already."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from mailroom.auth.users import create_user
from mailroom.models import MailIndexFolder, MailIndexMessage, MailViewInterest
from mailroom_core.store import CORE_MIGRATIONS
from tests.conftest import PASSWORD
from tests.test_filter_runner import add_account

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User


def indexed(
    db: Session, user_id: int, account_id: int, path: str = "INBOX", uids: tuple[int, ...] = (1,)
) -> int:
    folder = MailIndexFolder(user_id=user_id, account_id=account_id, path=path, state="ready")
    db.add(folder)
    db.flush()
    db.add_all(message(folder.id, user_id, uid) for uid in uids)
    db.commit()
    return folder.id


def message(folder_id: int, user_id: int, uid: int, **changes: Any) -> MailIndexMessage:
    fields: dict[str, Any] = {
        "folder_id": folder_id,
        "user_id": user_id,
        "uid": uid,
        "received": datetime(2026, 11, 20, tzinfo=UTC),
        "sender": [{"name": "Ann", "address": "ann@example.org"}],
        "recipients": {"to": [], "cc": []},
        "sender_text": "Ann ann@example.org",
        "to_text": "",
        "subject": "Quarterly report",
        "flags": [],
        "keywords": [],
        "size": 100,
        "has_attachments": False,
    }
    return MailIndexMessage(**(fields | changes))


def rows(db: Session) -> list[tuple[int, str, int]]:
    query = (
        select(MailIndexFolder.account_id, MailIndexFolder.path, func.count(MailIndexMessage.uid))
        .join(MailIndexMessage, isouter=True)
        .group_by(MailIndexFolder.id)
        .order_by(MailIndexFolder.account_id, MailIndexFolder.path)
    )
    return [tuple(row) for row in db.execute(query)]


def test_index_rows_go_with_their_account(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = add_account(user_client)
    home = add_account(user_client, "home")
    indexed(db_session, user.id, work, uids=(1, 2))
    indexed(db_session, user.id, work, "Archive")
    indexed(db_session, user.id, home, "INBOX")
    expires = datetime(2100, 1, 1, tzinfo=UTC)
    db_session.add_all(
        MailViewInterest(user_id=user.id, account_id=account, folder="INBOX", expires_at=expires)
        for account in (work, home)
    )
    db_session.commit()

    assert user_client.delete(f"/api/v1/accounts/{work}").status_code == 204
    assert rows(db_session) == [(home, "INBOX", 1)]
    assert db_session.scalars(select(MailViewInterest.account_id)).all() == [home]


def test_index_rows_go_with_their_user(
    admin_client: TestClient, db_session: Session, user: User
) -> None:
    bob = create_user(db_session, "bob", PASSWORD)
    indexed(db_session, user.id, 1, uids=(1, 2))
    indexed(db_session, bob.id, 1)

    assert admin_client.delete(f"/api/v1/users/{user.id}").status_code == 204
    assert rows(db_session) == [(1, "INBOX", 1)]
    assert db_session.scalar(select(MailIndexFolder.user_id)) == bob.id


def test_a_message_row_keeps_its_folders_user(db_session: Session, user: User) -> None:
    bob = create_user(db_session, "bob", PASSWORD)
    folder_id = indexed(db_session, user.id, 1)
    db_session.add(message(folder_id, bob.id, 2))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_subject_and_an_address_are_found_by_substring(db_session: Session, user: User) -> None:
    folder_id = indexed(db_session, user.id, 1)
    db_session.add(message(folder_id, user.id, 2, subject="Lunch", sender_text="Bo bo@x.org"))
    db_session.flush()
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    for column, term in (
        (MailIndexMessage.subject, "TERLY rep"),
        (MailIndexMessage.sender_text, "ann@"),
    ):
        query = select(MailIndexMessage.uid).where(column.ilike(f"%{term}%"))
        literal = query.compile(
            dialect=db_session.get_bind().dialect, compile_kwargs={"literal_binds": True}
        )
        assert "_trgm" in "\n".join(db_session.scalars(text(f"EXPLAIN {literal}")))
        assert db_session.scalars(query).all() == [1]


def _schema_of_pg_trgm(engine: Any) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT extnamespace::regnamespace::text FROM pg_extension "
                "WHERE extname = 'pg_trgm'"
            )
        ).scalar()


def _tables(engine: Any) -> list[str]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'app' AND table_name LIKE 'mail_index%' ORDER BY 1"
                )
            ).scalars()
        )


@pytest.mark.parametrize("already", [None, "public"])
def test_the_index_revision_goes_back_leaving_a_pg_trgm_it_found(
    throwaway_databases: Any, monkeypatch: pytest.MonkeyPatch, already: str | None
) -> None:
    from alembic import command
    from alembic.config import Config

    from mailroom.db_migrations import alembic_ini_path
    from mailroom_core.store import upgrade

    with throwaway_databases.migrated(lambda _: None) as engine:
        monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
        config = Config(str(alembic_ini_path()))
        config.attributes["quiet"] = True
        upgrade(engine, CORE_MIGRATIONS)
        command.upgrade(config, "202611191700")
        if already is not None:
            with engine.begin() as connection:
                connection.execute(text(f"CREATE EXTENSION pg_trgm WITH SCHEMA {already}"))

        command.upgrade(config, "202611201200")
        assert _tables(engine) == ["mail_index_folder", "mail_index_message"]
        assert _schema_of_pg_trgm(engine) == (already or "app")

        command.downgrade(config, "202611191700")
        assert _tables(engine) == []
        assert _schema_of_pg_trgm(engine) == already

        command.upgrade(config, "head")
        assert _tables(engine) == ["mail_index_folder", "mail_index_message"]
