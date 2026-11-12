"""The rule sets and filters that name a folder, and their rewrite after a rename
(``mailroom.filters.folders``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mailroom.auth.users import create_user
from mailroom.filters.folders import (
    Rewritten,
    RuleSetRef,
    naming_rule_sets,
    rewrite_folder,
    rewritten_spec,
    under,
)
from mailroom.models import Filter, RuleSet
from tests.conftest import PASSWORD

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.models import User

FROM_SHOP = {"type": "from_contains", "args": {"addr": "shop"}}


def move_to(*folder: str) -> dict[str, Any]:
    return {"type": "move_to", "args": {"folder": list(folder)}}


def spec(action: dict[str, Any]) -> dict[str, Any]:
    return {"criterion": FROM_SHOP, "action": action}


def test_under_matches_the_folder_and_below_and_inbox_in_any_case() -> None:
    assert under(["Projects"], ["Projects"])
    assert under(["Projects", "2026"], ["Projects"])
    assert not under(["Projects2"], ["Projects"])
    assert not under(["Projects"], ["Projects", "2026"])
    assert not under(["projects"], ["Projects"])
    assert under(["inbox", "Lists"], ["INBOX", "Lists"])
    assert not under(["Lists", "inbox"], ["Lists", "INBOX"])
    assert not under(["Projects"], [])


def test_the_rewritten_spec_moves_every_path_under_the_folder_at_any_depth() -> None:
    nested = {
        "criterion": {"and": [FROM_SHOP, {"type": "folder_like", "args": {"folder": ["Old"]}}]},
        "action": {
            "seq": [
                move_to("Old"),
                {"seq": [move_to("Old", "Deep"), {"type": "trash", "args": {"folder": ["Old"]}}]},
                move_to("Older"),
                {"type": "trash", "args": {"folder": None}},
                {"type": "trash", "args": {}},
                {"type": "star"},
            ]
        },
    }
    original = repr(nested)
    rewritten = rewritten_spec(nested, ["Old"], ["New", "Place"])
    assert rewritten == {
        # Not an action: only move_to and trash hold paths.
        "criterion": {"and": [FROM_SHOP, {"type": "folder_like", "args": {"folder": ["Old"]}}]},
        "action": {
            "seq": [
                move_to("New", "Place"),
                {
                    "seq": [
                        move_to("New", "Place", "Deep"),
                        {"type": "trash", "args": {"folder": ["New", "Place"]}},
                    ]
                },
                move_to("Older"),
                {"type": "trash", "args": {"folder": None}},
                {"type": "trash", "args": {}},
                {"type": "star"},
            ]
        },
    }
    assert repr(nested) == original
    assert rewritten_spec(spec(move_to("Elsewhere")), ["Old"], ["New"]) == spec(
        move_to("Elsewhere")
    )


def add_rule_set(
    db: Session,
    user: User,
    folder: list[str],
    *filters: dict[str, Any],
    account_id: str = "work",
    enabled: bool = False,
    name: str = "rules",
) -> RuleSet:
    rule_set = RuleSet(
        user_id=user.id,
        account_id=account_id,
        folder=folder,
        name=name,
        trigger="manual",
        enabled=enabled,
    )
    db.add(rule_set)
    db.flush()
    for position, filter_spec in enumerate(filters):
        db.add(Filter(rule_set_id=rule_set.id, position=position, name="f", spec=filter_spec))
    db.commit()
    return rule_set


def test_naming_rule_sets_finds_a_rule_set_by_its_folder_or_a_filter_s(
    db_session: Session, user: User
) -> None:
    on_it = add_rule_set(db_session, user, ["Projects"], name="on it")
    below = add_rule_set(db_session, user, ["Projects", "2026"], enabled=True, name="below")
    moving = add_rule_set(
        db_session, user, ["INBOX"], spec({"seq": [move_to("Projects", "x")]}), name="moving"
    )
    add_rule_set(db_session, user, ["INBOX"], spec(move_to("Projects2")), name="other folder")
    add_rule_set(db_session, user, ["Projects"], account_id="home", name="other account")
    bob = create_user(db_session, "bob", PASSWORD)
    add_rule_set(db_session, bob, ["Projects"], name="bob's")
    assert naming_rule_sets(db_session, user.id, "work", ["Projects"]) == [
        RuleSetRef(on_it.id, "on it"),
        RuleSetRef(below.id, "below"),
        RuleSetRef(moving.id, "moving"),
    ]
    assert naming_rule_sets(db_session, user.id, "work", ["Projects", "2026"]) == [
        RuleSetRef(below.id, "below")
    ]
    assert naming_rule_sets(db_session, user.id, "work", ["Nowhere"]) == []


def test_rewrite_folder_rewrites_this_account_s_rule_sets_and_filters_only(
    db_session: Session, user: User
) -> None:
    enabled = add_rule_set(db_session, user, ["Old"], enabled=True)
    below = add_rule_set(db_session, user, ["Old", "Sub"], spec(move_to("Elsewhere")))
    mover = add_rule_set(
        db_session, user, ["INBOX"], spec(move_to("Keep")), spec({"seq": [move_to("Old", "Sub")]})
    )
    untouched = add_rule_set(db_session, user, ["Older"], spec(move_to("Older")))
    home = add_rule_set(db_session, user, ["Old"], spec(move_to("Old")), account_id="home")
    bob = create_user(db_session, "bob", PASSWORD)
    bobs = add_rule_set(db_session, bob, ["Old"], spec(move_to("Old")))
    [keep, moving] = db_session.query(Filter).filter_by(rule_set_id=mover.id).order_by(Filter.id)

    assert rewrite_folder(db_session, user.id, "work", ["Old"], ["Archive", "New"]) == Rewritten(
        rule_sets=[enabled.id, below.id, mover.id], filters=[moving.id]
    )
    db_session.expire_all()
    assert enabled.folder == ["Archive", "New"]
    assert enabled.enabled is True
    assert below.folder == ["Archive", "New", "Sub"]
    assert moving.spec == spec({"seq": [move_to("Archive", "New", "Sub")]})
    assert keep.spec == spec(move_to("Keep"))
    assert untouched.folder == ["Older"]
    assert (home.folder, bobs.folder) == (["Old"], ["Old"])
    home_filter = db_session.query(Filter).filter_by(rule_set_id=home.id).one()
    assert home_filter.spec == spec(move_to("Old"))
    assert rewrite_folder(db_session, user.id, "work", ["Gone"], ["New"]) == Rewritten([], [])
