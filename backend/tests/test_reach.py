from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from mailroom.accounts.store import AccountNotFound, UserAccounts
from mailroom.reach import SEND_CEILINGS, WHOLE, Reach, reach_of, stricter
from mailroom_core.config import CoreSettings
from tests.test_api_accounts import new_account

if TYPE_CHECKING:
    from mailroom_core.config import SendMode


def loaded() -> UserAccounts:
    raw = [
        {
            "id": account_id,
            **{k: v for k, v in new_account(label).items() if k != "password"},
            "auth": {"type": "password"},
            "send_mode": mode,
        }
        for account_id, label, mode in ((1, "work", "direct"), (2, "home", "drafts"))
    ]
    doc: dict[str, Any] = {"account": raw}
    return UserAccounts(1, doc, CoreSettings.model_validate(doc))


def test_the_send_modes_go_from_the_strictest() -> None:
    assert SEND_CEILINGS == ("off", "drafts", "approval", "direct")
    for i, one in enumerate(SEND_CEILINGS):
        for j, other in enumerate(SEND_CEILINGS):
            assert stricter(one, other) == SEND_CEILINGS[min(i, j)]


def test_the_whole_reach_changes_nothing() -> None:
    accounts = loaded()
    assert WHOLE.within(accounts) is accounts
    assert WHOLE.includes(None)


@pytest.mark.parametrize(
    ("ceiling", "modes"),
    [
        (None, ["direct", "drafts"]),
        ("direct", ["direct", "drafts"]),
        ("approval", ["approval", "drafts"]),
        ("off", ["off", "off"]),
    ],
)
def test_a_ceiling_only_ever_tightens(ceiling: SendMode | None, modes: list[str]) -> None:
    within = Reach(None, ceiling).within(loaded())
    assert [account.send_mode for account in within.settings.account] == modes


def test_an_account_beyond_is_not_there() -> None:
    reach = Reach(frozenset({2}))
    within = reach.within(loaded())
    assert [account.id for account in within.settings.account] == [2]
    with pytest.raises(AccountNotFound):
        within.account(1)
    assert not reach.includes(1)
    assert not reach.includes(None)


@pytest.mark.parametrize(
    ("account_ids", "ceiling", "reach"),
    [
        (None, None, WHOLE),
        ([2, 1], "drafts", Reach(frozenset({1, 2}), "drafts")),
        # No accounts is none, and what cannot be read is the least, never the whole.
        ([], None, Reach(frozenset())),
        ("1", None, Reach(frozenset())),
        (["1", True], None, Reach(frozenset())),
        (None, "anything", Reach(None, "off")),
        (None, 3, Reach(None, "off")),
    ],
)
def test_the_limits_kept_are_read_closed(account_ids: Any, ceiling: Any, reach: Reach) -> None:
    assert reach_of(account_ids, ceiling) == reach
