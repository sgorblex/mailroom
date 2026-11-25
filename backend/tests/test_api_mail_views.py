"""Saved views and *All inboxes* (``docs/requirements/mail-client.md``, *Views*): creating,
renaming, changing, ordering and deleting them, only the user's own; their messages read from the
index only, merged newest first across folders with a cursor, the folders off the index named;
Gmail's INBOX and labels read from All Mail's rows; an account's folders dropped with it."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.mail.saved_views import MAX_VIEWS
from mailroom.mail.service.views import view_sources
from mailroom.models import MailIndexFolder, SavedView
from mailroom_core.mail import MailError, MessageRef, Source, format_ref
from tests.test_api_accounts import added, new_account
from tests.test_api_mail import MAIL
from tests.test_mail_index_reads import ALL_MAIL, GMAIL, ready
from tests.test_mail_index_rows import message

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.testclient import TestClient

    from mailroom.models import User

VIEWS = f"{MAIL}/views"
DAY = datetime(2026, 11, 20, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _index_in_the_test(
    application: FastAPI, sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(application.state, "session_maker", sessions)


def _create(client: TestClient, **body: Any) -> tuple[int, Any]:
    response = client.post(VIEWS, json=body)
    return response.status_code, response.json()


def _code(response: Any) -> str:
    return str(response.json()["detail"]["code"])


def _subjects(page: dict[str, Any]) -> list[str]:
    return [m["subject"] for m in page["messages"]]


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.get(VIEWS).status_code == 401
    assert _create(api_client, name="x", query="is:unread")[0] == 401
    assert api_client.get(f"{VIEWS}/all_inboxes/messages").status_code == 401


def test_a_user_keeps_names_orders_and_deletes_views(
    user_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    work = added(user_client, new_account("work"))
    status, lists = _create(
        user_client,
        name=" Lists ",
        sources=[
            {"account_id": work, "folder": "inbox"},
            {"account_id": work, "folder": "Lists/Secret"},
            {"account_id": work, "folder": "INBOX"},
        ],
    )
    assert status == 201
    assert lists == {
        "id": lists["id"],
        "name": "Lists",
        "sources": [
            {"account_id": work, "folder": "INBOX"},
            {"account_id": work, "folder": "Lists/Secret"},
        ],
        "query": None,
        "position": 0,
    }
    status, unread = _create(user_client, name="Unread", query="is:unread from:ada")
    assert (status, unread["query"], unread["sources"], unread["position"]) == (
        201,
        "is:unread from:ada",
        None,
        1,
    )
    assert "Lists/Secret" not in caplog.text
    assert "ada" not in caplog.text

    names = [v["name"] for v in user_client.get(VIEWS).json()["views"]]
    assert names == ["Lists", "Unread"]

    renamed = user_client.patch(f"{VIEWS}/{unread['id']}", json={"name": "New"})
    assert renamed.json()["name"] == "New"
    taken = user_client.patch(f"{VIEWS}/{unread['id']}", json={"name": "Lists"})
    assert (taken.status_code, _code(taken)) == (409, "view_name_taken")
    assert _create(user_client, name="Lists", query="is:flagged")[0] == 409
    switched = user_client.patch(
        f"{VIEWS}/{unread['id']}", json={"sources": [{"account_id": work, "folder": "Archive"}]}
    )
    assert (switched.json()["query"], switched.json()["sources"]) == (
        None,
        [{"account_id": work, "folder": "Archive"}],
    )

    ordered = user_client.put(f"{VIEWS}/order", json={"view_ids": [unread["id"], lists["id"]]})
    assert [(v["name"], v["position"]) for v in ordered.json()["views"]] == [
        ("New", 0),
        ("Lists", 1),
    ]
    partial = user_client.put(f"{VIEWS}/order", json={"view_ids": [lists["id"]]})
    assert (partial.status_code, _code(partial)) == (400, "invalid_order")

    assert user_client.delete(f"{VIEWS}/{unread['id']}").status_code == 204
    assert [(v["name"], v["position"]) for v in user_client.get(VIEWS).json()["views"]] == [
        ("Lists", 0)
    ]
    assert user_client.delete(f"{VIEWS}/{unread['id']}").status_code == 404


def test_a_view_is_folders_or_a_search_the_index_answers(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = added(user_client, new_account("work"))
    source = [{"account_id": work, "folder": "INBOX"}]
    assert _create(user_client, name="x")[0] == 422
    assert _create(user_client, name="x", sources=source, query="is:unread")[0] == 422
    assert _create(user_client, name="x", sources=[])[0] == 422
    assert _create(user_client, name=" ", query="is:unread")[0] == 400
    assert (
        user_client.post(VIEWS, json={"name": "x", "query": "is:x", "extra": 1}).status_code == 422
    )
    for query in ('from:"ada', "is:sometimes", "board meeting"):
        status, body = _create(user_client, name="x", query=query)
        assert (status, body["detail"]["code"]) == (400, "invalid_argument"), query
    status, body = _create(
        user_client, name="x", sources=[{"account_id": work + 100, "folder": "INBOX"}]
    )
    assert (status, body["detail"]["code"]) == (404, "not_found")

    _, unread = _create(user_client, name="Unread", query="is:unread")
    _, flagged = _create(user_client, name="Flagged", query="is:flagged")
    for change in ({"sources": source, "query": "is:unread"}, {}):
        assert user_client.patch(f"{VIEWS}/{unread['id']}", json=change).status_code == 422
    texted = user_client.patch(f"{VIEWS}/{unread['id']}", json={"query": "board meeting"})
    assert (texted.status_code, _code(texted)) == (400, "invalid_argument")
    twice = user_client.put(f"{VIEWS}/order", json={"view_ids": [unread["id"], unread["id"]]})
    assert (twice.status_code, _code(twice)) == (400, "invalid_order")

    # A line saved before the syntax changed is refused, not a failure of the server.
    stored = db_session.get(SavedView, flagged["id"])
    assert stored is not None
    stored.query = 'from:"ada'
    db_session.commit()
    broken = user_client.get(f"{VIEWS}/{flagged['id']}/messages")
    assert (broken.status_code, _code(broken)) == (400, "invalid_argument")

    db_session.add_all(
        SavedView(user_id=user.id, name=f"v{n}", query="is:unread", position=n)
        for n in range(2, MAX_VIEWS)
    )
    db_session.commit()
    status, body = _create(user_client, name="One more", query="is:unread")
    assert (status, body["detail"]["code"]) == (400, "too_many_views")


def test_views_are_the_users_own(
    api_client: TestClient, db_session: Session, user: User, admin: User
) -> None:
    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, admin))
    theirs = added(api_client, new_account("admin"))
    _, view = _create(api_client, name="Admin", sources=[{"account_id": theirs, "folder": "INBOX"}])
    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, user))
    assert api_client.get(VIEWS).json() == {"views": []}
    assert api_client.get(f"{VIEWS}/{view['id']}/messages").status_code == 404
    assert api_client.patch(f"{VIEWS}/{view['id']}", json={"name": "Mine"}).status_code == 404
    assert api_client.delete(f"{VIEWS}/{view['id']}").status_code == 404
    status, body = _create(
        api_client, name="Theirs", sources=[{"account_id": theirs, "folder": "INBOX"}]
    )
    assert (status, body["detail"]["code"]) == (404, "not_found")
    reordered = api_client.put(f"{VIEWS}/order", json={"view_ids": [view["id"]]})
    assert reordered.status_code == 400


def test_all_inboxes_merge_the_indexed_inboxes_newest_first(
    user_client: TestClient, db_session: Session, user: User, admin: User
) -> None:
    work = added(user_client, new_account("work"))
    home = added(user_client, new_account("home"))
    unindexed = added(user_client, new_account("old"))
    work_inbox = ready(db_session, user, work, "INBOX")
    home_inbox = ready(db_session, user, home, "INBOX", uid_validity=9)
    ready(db_session, user, unindexed, "INBOX", state="syncing")
    archive = ready(db_session, user, work, "Archive")
    theirs = ready(db_session, admin, work, "INBOX")
    db_session.add_all(
        [
            message(work_inbox, user.id, 1, received=DAY, subject="w1"),
            message(home_inbox, user.id, 1, received=DAY + timedelta(hours=1), subject="h1"),
            message(work_inbox, user.id, 2, received=DAY + timedelta(hours=2), subject="w2"),
            message(archive, user.id, 3, received=DAY + timedelta(hours=3), subject="archived"),
            message(theirs, admin.id, 4, received=DAY + timedelta(hours=4), subject="theirs"),
        ]
    )
    db_session.commit()

    url = f"{VIEWS}/all_inboxes/messages"
    first = user_client.get(url, params={"limit": 2}).json()
    assert _subjects(first) == ["w2", "h1"]
    assert first["not_indexed"] == [{"account_id": unindexed, "folder": "INBOX"}]
    assert first["failures"] == []
    assert first["messages"][1]["ref"] == format_ref(MessageRef(home, "INBOX", 9, 1))
    second = user_client.get(url, params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert (_subjects(second), second["next_cursor"], second["not_indexed"]) == (["w1"], None, [])

    rebuilt = db_session.get(MailIndexFolder, home_inbox)
    assert rebuilt is not None
    rebuilt.uid_validity = 10
    db_session.commit()
    stale = user_client.get(url, params={"limit": 2, "cursor": first["next_cursor"]})
    assert (stale.status_code, _code(stale)) == (409, "stale_reference")
    assert _code(user_client.get(url, params={"cursor": "not a cursor"})) == "invalid_argument"


def test_all_inboxes_take_in_subfolders_when_asked(
    user_client: TestClient, db_session: Session, user: User, admin: User
) -> None:
    work = added(user_client, new_account("work"))
    home = added(user_client, new_account("home"))
    folders = {
        (work, "INBOX"): ready(db_session, user, work, "INBOX"),
        (work, "INBOX/Lists"): ready(db_session, user, work, "INBOX/Lists", inbox_subfolder=True),
        (work, "INBOX/Trash"): ready(db_session, user, work, "INBOX/Trash"),
        (home, "INBOX"): ready(db_session, user, home, "INBOX"),
        (home, "INBOX.A.B"): ready(db_session, user, home, "INBOX.A.B", inbox_subfolder=True),
    }
    theirs = ready(db_session, admin, work, "INBOX/Theirs", inbox_subfolder=True)
    for n, ((_, path), folder_id) in enumerate(folders.items()):
        received = DAY + timedelta(hours=n)
        db_session.add(message(folder_id, user.id, 1, received=received, subject=path))
    db_session.add(message(theirs, admin.id, 1, received=DAY, subject="theirs"))
    db_session.commit()

    url = f"{VIEWS}/all_inboxes/messages"
    assert _subjects(user_client.get(url).json()) == ["INBOX", "INBOX"]
    first = user_client.get(url, params={"subfolders": True, "limit": 3}).json()
    assert _subjects(first) == ["INBOX.A.B", "INBOX", "INBOX/Lists"]
    assert (first["not_indexed"], first["failures"]) == ([], [])
    params = {"subfolders": True, "limit": 3, "cursor": first["next_cursor"]}
    assert _subjects(user_client.get(url, params=params).json()) == ["INBOX"]

    # A search in All inboxes reads what its list reads.
    everything = _search(user_client, view="all_inboxes", subfolders=True, unread=True).json()
    assert (len(_subjects(everything)), everything["failures"]) == (4, [])
    lists = _search(user_client, view="all_inboxes", subfolders=True, subject="Lists").json()
    assert _subjects(lists) == ["INBOX/Lists"]
    assert _subjects(_search(user_client, view="all_inboxes", subject="Lists").json()) == []

    # An account removed takes its subfolders along.
    assert user_client.delete(f"/api/v1/accounts/{work}").status_code == 204
    left = user_client.get(url, params={"subfolders": True}).json()
    assert _subjects(left) == ["INBOX.A.B", "INBOX"]


def test_subfolders_are_all_inboxes_only(user_client: TestClient) -> None:
    work = added(user_client, new_account("work"))
    _, own = _create(user_client, name="Own", sources=[{"account_id": work, "folder": "INBOX"}])
    page = user_client.get(f"{VIEWS}/{own['id']}/messages", params={"subfolders": True})
    assert (page.status_code, _code(page)) == (400, "invalid_argument")
    assert user_client.get(f"{VIEWS}/{own['id'] + 1}/messages?subfolders=1").status_code == 404
    assert _search(user_client, view=own["id"], subfolders=True).status_code == 422
    for scope in ({"account": "*"}, {"folder": "INBOX"}):
        assert _search(user_client, view="all_inboxes", **scope).status_code == 422
    assert _search(user_client, view="inboxes").status_code == 422


def test_a_saved_view_reads_its_folders_or_its_search(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = added(user_client, new_account("work"))
    home = added(user_client, new_account("home"))
    inbox = ready(db_session, user, work, "INBOX")
    archive = ready(db_session, user, home, "Archive")
    db_session.add_all(
        [
            message(inbox, user.id, 1, received=DAY, subject="read", flags=["\\Seen"]),
            message(archive, user.id, 2, received=DAY + timedelta(hours=1), subject="kept"),
            message(inbox, user.id, 3, received=DAY + timedelta(hours=2), subject="new"),
        ]
    )
    db_session.commit()

    _, folders = _create(
        user_client,
        name="Both",
        sources=[
            {"account_id": work, "folder": "INBOX"},
            {"account_id": home, "folder": "Archive"},
            {"account_id": home, "folder": "Projects"},
        ],
    )
    page = user_client.get(f"{VIEWS}/{folders['id']}/messages").json()
    assert _subjects(page) == ["new", "kept", "read"]
    # A folder without a row of an indexed account may be a Gmail label: the server is asked.
    assert page["not_indexed"] == []
    assert page["failures"] == [{"account_id": home, "folder": "Projects", "code": "unreachable"}]

    _, unread = _create(user_client, name="Unread", query="is:unread")
    assert _subjects(user_client.get(f"{VIEWS}/{unread['id']}/messages").json()) == ["new"]
    _, everywhere = _create(user_client, name="Home", query="account:home@example.org in:Archive")
    assert _subjects(user_client.get(f"{VIEWS}/{everywhere['id']}/messages").json()) == ["kept"]
    _, nobody = _create(user_client, name="Nobody", query="account:nobody")
    missing = user_client.get(f"{VIEWS}/{nobody['id']}/messages")
    assert (missing.status_code, _code(missing)) == (404, "not_found")


def _search(client: TestClient, **body: Any) -> Any:
    return client.post(f"{MAIL}/search", json=body)


def test_a_search_in_a_view_reads_its_folders_only(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = added(user_client, new_account("work"))
    home = added(user_client, new_account("home"))
    inbox = ready(db_session, user, work, "INBOX")
    archive = ready(db_session, user, home, "Archive")
    other = ready(db_session, user, home, "INBOX")
    db_session.add_all(
        [
            message(inbox, user.id, 1, received=DAY, subject="read", flags=["\\Seen"]),
            message(archive, user.id, 2, received=DAY + timedelta(hours=1), subject="kept"),
            message(inbox, user.id, 3, received=DAY + timedelta(hours=2), subject="new"),
            message(other, user.id, 4, received=DAY + timedelta(hours=3), subject="elsewhere"),
        ]
    )
    db_session.commit()
    sources = [{"account_id": work, "folder": "INBOX"}, {"account_id": home, "folder": "Archive"}]
    _, both = _create(user_client, name="Both", sources=sources)

    first = _search(user_client, view=both["id"], unread=True, limit=1).json()
    assert (_subjects(first), first["failures"]) == (["new"], [])
    cursor = first["next_cursor"]
    second = _search(user_client, view=both["id"], unread=True, limit=1, cursor=cursor).json()
    assert (_subjects(second), second["next_cursor"]) == (["kept"], None)
    grouped = _search(user_client, view=both["id"], subject="kept", group="thread").json()
    assert (_subjects(grouped), len(grouped["conversations"])) == (["kept"], 1)

    # An account removed leaves the view with none of its folders: nothing is found, or refused.
    _, only = _create(user_client, name="Only", sources=sources[:1])
    assert user_client.delete(f"/api/v1/accounts/{work}").status_code == 204
    page = _search(user_client, view=only["id"], unread=True).json()
    assert (page["messages"], page["next_cursor"], page["failures"]) == ([], None, [])
    assert _subjects(_search(user_client, view=both["id"], unread=True).json()) == ["kept"]


def test_a_search_in_a_view_is_refused_unless_it_is_the_users_view_of_folders(
    api_client: TestClient, db_session: Session, user: User, admin: User
) -> None:
    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, admin))
    theirs = added(api_client, new_account("admin"))
    _, view = _create(api_client, name="Admin", sources=[{"account_id": theirs, "folder": "INBOX"}])
    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, user))
    assert _search(api_client, view=view["id"]).status_code == 404
    assert _search(api_client, view=0).status_code == 422
    mine = added(api_client, new_account("work"))
    _, own = _create(api_client, name="Own", sources=[{"account_id": mine, "folder": "INBOX"}])
    for scope in ({"account": mine}, {"folder": "INBOX"}, {"subfolders": True}):
        assert _search(api_client, view=own["id"], **scope).status_code == 422
    _, query = _create(api_client, name="Unread", query="is:unread")
    refused = _search(api_client, view=query["id"])
    assert (refused.status_code, _code(refused)) == (400, "invalid_argument")
    broken = _search(api_client, view=own["id"], text="a\r\nb")
    assert (broken.status_code, _code(broken)) == (400, "invalid_argument")
    # A count or a bulk action acts on a folder or a search's own scope only.
    counted = api_client.post(f"{MAIL}/messages/count", json={"view": own["id"]})
    assert counted.status_code == 422


def test_a_removed_accounts_folders_leave_the_views(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = added(user_client, new_account("work"))
    home = added(user_client, new_account("home"))
    _, view = _create(
        user_client,
        name="Both",
        sources=[{"account_id": work, "folder": "INBOX"}, {"account_id": home, "folder": "INBOX"}],
    )
    _create(user_client, name="Search", query="is:unread")
    assert user_client.delete(f"/api/v1/accounts/{work}").status_code == 204
    kept = {v["name"]: v["sources"] for v in user_client.get(VIEWS).json()["views"]}
    assert kept == {"Both": [{"account_id": home, "folder": "INBOX"}], "Search": None}
    assert user_client.delete(f"/api/v1/accounts/{home}").status_code == 204
    emptied = user_client.get(f"{VIEWS}/{view['id']}/messages").json()
    assert (emptied["messages"], emptied["not_indexed"]) == ([], [])
    rows = db_session.scalars(select(SavedView).where(SavedView.user_id == user.id))
    assert len(rows.all()) == 2


def test_gmail_folders_are_read_from_all_mail_by_their_labels(
    db_session: Session, user: User
) -> None:
    every = ready(db_session, user, 1, ALL_MAIL)
    ready(db_session, user, 2, "INBOX")

    def folders(account: int) -> list[Any]:
        if account == 2:
            raise MailError("unreachable")
        return GMAIL

    connections: Any = SimpleNamespace(
        folders=folders, capabilities=lambda account: SimpleNamespace(gmail=account == 1)
    )
    wanted = [Source(1, "INBOX"), Source(1, "Work"), Source(1, "[Gmail]/Spam"), Source(2, "*")]
    picked, failures, not_indexed = view_sources(db_session, connections, user.id, wanted)
    assert [(s.folder_id, s.labels, s.live) for s in picked] == [
        (every, ("Work", "\\Inbox"), ("INBOX", "Work"))
    ]
    assert not_indexed == [Source(1, "[Gmail]/Spam")]
    assert [(f.account, f.folder, f.code) for f in failures] == [(2, "*", "unreachable")]

    whole, _, _ = view_sources(db_session, connections, user.id, [Source(1, "*")])
    assert [(s.folder, s.labels) for s in whole] == [(ALL_MAIL, None)]


def test_folders_and_views_read_by_conversation(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = added(user_client, new_account("work"))
    inbox = ready(db_session, user, work, "INBOX")
    archive = ready(db_session, user, work, "Archive")
    db_session.add_all(
        [
            message(inbox, user.id, 1, received=DAY, subject="plan", thread_key="<plan>"),
            message(archive, user.id, 2, received=DAY + timedelta(hours=1), thread_key="<plan>"),
            message(inbox, user.id, 3, received=DAY + timedelta(hours=2), subject="lunch"),
            message(
                inbox,
                user.id,
                4,
                received=DAY + timedelta(hours=3),
                subject="Re: plan",
                thread_key="<plan>",
                flags=["\\Seen"],
            ),
        ]
    )
    db_session.commit()
    ref = {uid: format_ref(MessageRef(work, "INBOX", 7, uid)) for uid in (1, 3, 4)}

    url = f"{MAIL}/accounts/{work}/messages"
    flat = user_client.get(url, params={"folder": "INBOX"}).json()
    assert (_subjects(flat), flat["conversations"]) == (["Re: plan", "lunch", "plan"], None)
    first = user_client.get(url, params={"folder": "INBOX", "group": "thread", "limit": 1}).json()
    assert _subjects(first) == ["Re: plan"]
    assert first["conversations"] == [
        {
            "count": 2,
            "unread": 1,
            "participants": ["Ann"],
            "has_attachments": False,
            "flagged": False,
            "refs": [ref[4], ref[1]],
            "listed_folders": {},
            # Ann wrote both.
            "latest_from_new_sender": False,
        }
    ]
    params = {"folder": "INBOX", "group": "thread", "cursor": first["next_cursor"]}
    rest = user_client.get(url, params=params).json()
    assert (_subjects(rest), rest["next_cursor"]) == (["lunch"], None)
    assert [c["refs"] for c in rest["conversations"]] == [[ref[3]]]
    assert user_client.get(url, params={"group": "flat"}).status_code == 422

    searched = user_client.post(
        f"{MAIL}/search",
        json={"account": work, "folder": "INBOX", "unread": True, "group": "thread"},
    ).json()
    assert [(c["count"], c["refs"]) for c in searched["conversations"]] == [
        (1, [ref[3]]),
        (1, [ref[1]]),
    ]

    _, both = _create(
        user_client,
        name="Both",
        sources=[
            {"account_id": work, "folder": "INBOX"},
            {"account_id": work, "folder": "Archive"},
        ],
    )
    viewed = user_client.get(f"{VIEWS}/{both['id']}/messages", params={"group": "thread"}).json()
    assert _subjects(viewed) == ["Re: plan", "lunch"]
    assert viewed["conversations"][0]["refs"] == [
        ref[4],
        format_ref(MessageRef(work, "Archive", 7, 2)),
        ref[1],
    ]
    assert viewed["not_indexed"] == []
