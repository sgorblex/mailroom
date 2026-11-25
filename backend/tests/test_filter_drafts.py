"""Filter drafts and the public mail providers (``mailroom.filters.drafts``, ``providers``)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from mailroom.errors import Refusal
from mailroom.filters.drafts import message_draft, search_draft
from mailroom.filters.providers import public_provider
from mailroom.filters.service import checked_spec
from mailroom_core.mail import SearchQuery
from mailroom_core.render import Address

if TYPE_CHECKING:
    from mailroom_core.mail import MessageView

NO_OP = {"type": "no_op"}


@pytest.mark.parametrize(
    "domain",
    [
        "gmail.com",
        "GMail.COM",
        "gmail.com.",
        "googlemail.com",
        "hotmail.it",
        "hotmail.co.uk",
        "live.com",
        "outlook.com",
        "yahoo.com.br",
        "gmx.de",
        "gmx.net",
        "web.de",
        "libero.it",
        "proton.me",
        "yandex.ru",
        "mail.ru",
        "fastmail.com",
        "eu.zoho.com",
        "\uff47\uff4d\uff41\uff49\uff4c.com",  # fullwidth: its IDNA form is gmail.com
    ],
)
def test_public_providers_are_known(domain: str) -> None:
    assert public_provider(domain)


@pytest.mark.parametrize(
    "domain",
    [
        "example.org",
        "shop.example",
        "notgmail.com",
        "gmail.com.example.org",
        "live.example.org",
        "hotmail.example.org",
        "mail.example.ru",
        "",
        "not a host",
    ],
)
def test_other_domains_are_not_public_providers(domain: str) -> None:
    assert not public_provider(domain)


def _filter(criterion: dict[str, Any]) -> dict[str, Any]:
    return {"criterion": criterion, "action": NO_OP}


@pytest.mark.parametrize(
    "criterion",
    [
        {"type": "from_domain", "args": {"domain": "gmail.com"}},
        {"type": "from_domain", "args": {"domain": "Hotmail.IT", "incl_subdomains": True}},
        {"not": {"type": "from_domain", "args": {"domain": "yahoo.co.uk"}}},
        {"type": "from_domain", "args": {"domain": "co.uk", "incl_subdomains": True}},
        {"type": "from_domain", "args": {"domain": "com.br", "incl_subdomains": True}},
        {
            "and": [
                {"type": "is_unread"},
                {"or": [{"type": "from_domain", "args": {"domain": "outlook.com"}}]},
            ]
        },
        {"type": "from_contains", "args": {"addr": "gmail.com"}},
        {"type": "from_contains", "args": {"addr": "@Mail.Libero.it"}},
        {"type": "from_matches", "args": {"pattern": "@gmail\\.com$"}},
        {"type": "from_matches", "args": {"pattern": "^.*@hotmail\\.it"}},
    ],
    ids=[
        "plain",
        "with subdomains",
        "negated",
        "co.uk",
        "com.br",
        "nested",
        "contains",
        "contains with @",
        "matches",
        "matches anchored",
    ],
)
def test_a_spec_on_a_public_provider_is_refused(criterion: dict[str, Any]) -> None:
    with pytest.raises(Refusal) as refused:
        checked_spec(_filter(criterion))
    assert refused.value.code == "public_provider_domain"


def test_a_spec_on_another_domain_is_kept() -> None:
    spec = checked_spec(_filter({"type": "from_domain", "args": {"domain": "shop.example"}}))
    assert spec["criterion"]["args"]["domain"] == "shop.example"
    short = {"type": "from_domain", "args": {"domain": "gnu.org", "incl_subdomains": True}}
    assert checked_spec(_filter(short))["criterion"]["args"]["domain"] == "gnu.org"
    for criterion in (
        {"type": "from_contains", "args": {"addr": "maria.rossi@gmail.com"}},
        {"type": "from_contains", "args": {"addr": "gmail"}},
        {"type": "from_matches", "args": {"pattern": "^maria\\.rossi@gmail\\.com$"}},
        {"type": "from_matches", "args": {"pattern": "(gmail|yahoo)\\.com"}},
    ):
        assert checked_spec(_filter(criterion))["criterion"] == criterion


def test_a_filters_own_spec_on_a_public_provider_is_kept_on_an_edit() -> None:
    stored = {
        "criterion": {"type": "from_domain", "args": {"domain": "gmail.com"}},
        "action": NO_OP,
    }
    kept = checked_spec({**stored, "criterion": {"and": [stored["criterion"]]}}, stored=stored)
    assert kept["criterion"]["args"]["domain"] == "gmail.com"
    changed = _filter({"type": "from_contains", "args": {"addr": "@gmail.com"}})
    with pytest.raises(Refusal) as refused:
        checked_spec(changed, stored=stored)
    assert refused.value.code == "public_provider_domain"


def _view(
    *senders: Address, list_id: str | None = None, list_name: str | None = None
) -> MessageView:
    listed = (
        None
        if list_id is None and list_name is None
        else SimpleNamespace(id=list_id, name=list_name)
    )
    return cast(
        "MessageView", SimpleNamespace(sender=senders, headers=SimpleNamespace(mailing_list=listed))
    )


def test_a_list_message_drafts_its_list_then_its_sender() -> None:
    draft = message_draft(
        _view(
            Address("Shop", "news@shop.example"),
            list_id="news.shop.example",
            list_name="Shop news",
        )
    )
    assert [c["type"] for c in draft.criteria] == ["list_is", "from_is", "from_domain"]
    assert draft.criteria[0]["args"] == {"list_id": "news.shop.example"}
    assert draft.criteria[1]["args"] == {"addr": "news@shop.example"}
    assert draft.criteria[2]["args"]["domain"] == "shop.example"
    assert draft.spec == {"criterion": draft.criteria[0], "action": NO_OP}
    assert draft.name == "Shop news"


def test_a_sender_at_a_public_provider_is_drafted_by_address_only() -> None:
    draft = message_draft(_view(Address("Maria Rossi", "Maria.Rossi@GMail.com")))
    assert [c["type"] for c in draft.criteria] == ["from_is"]
    assert draft.spec["criterion"]["args"] == {"addr": "Maria.Rossi@GMail.com"}
    assert draft.name == "Maria Rossi"


def test_a_drafts_name_is_one_short_line() -> None:
    hostile = "Evil\r\nBcc: x@y.example " + "a" * 500
    draft = message_draft(_view(Address(hostile, "x@shop.example")))
    assert "\n" not in draft.name
    assert "\r" not in draft.name
    assert len(draft.name) <= 200
    assert message_draft(_view(Address(None, "x@shop.example"))).name == "x@shop.example"


def test_a_sender_whose_domain_is_no_host_is_drafted_by_address() -> None:
    draft = message_draft(_view(Address(None, "odd@[127.0.0.1]")))
    assert [c["type"] for c in draft.criteria] == ["from_is"]


def test_a_message_without_list_or_sender_has_no_draft() -> None:
    for view in (_view(), _view(Address("Nobody", None)), _view(Address(None, "undisclosed"))):
        with pytest.raises(Refusal) as refused:
            message_draft(view)
        assert refused.value.code == "nothing_to_filter"


def test_a_search_drafts_what_a_filter_can_match() -> None:
    draft = search_draft(
        SearchQuery(
            sender="shop.example",
            to="me@example.org",
            subject="Invoice",
            text="anything",
            since=date(2026, 1, 1),
            before=date(2026, 2, 1),
            unread=True,
            flagged=False,
            has_attachment=True,
            gmail_query="label:x",
        )
    )
    assert draft.spec["action"] == NO_OP
    assert draft.spec["criterion"] == {
        "and": [
            {"type": "from_contains", "args": {"addr": "shop.example"}},
            {
                "type": "to_contains",
                "args": {"addr": "me@example.org", "incl_cc": False, "incl_bcc": False},
            },
            {"type": "subject_contains", "args": {"substring": "Invoice"}},
            {"type": "is_unread"},
            {"type": "is_unstarred"},
        ]
    }
    assert draft.dropped == ["text", "since", "before", "has_attachment", "gmail_query"]


def test_a_search_from_a_public_provider_drops_the_sender() -> None:
    draft = search_draft(SearchQuery(sender="@gmail.com", subject="Invoice"))
    assert draft.spec["criterion"] == {
        "type": "subject_contains",
        "args": {"substring": "Invoice"},
    }
    assert draft.dropped == ["from"]
    with pytest.raises(Refusal) as refused:
        search_draft(SearchQuery(sender="hotmail.it"))
    assert refused.value.extra == {"dropped": ["from"]}
    address = search_draft(SearchQuery(sender="maria@gmail.com"))
    assert address.spec["criterion"]["type"] == "from_contains"


def test_one_search_field_is_the_criterion_itself() -> None:
    draft = search_draft(SearchQuery(subject="Invoice", has_attachment=False))
    assert draft.spec["criterion"]["type"] == "subject_contains"
    assert draft.dropped == []
    assert search_draft(SearchQuery(handled=True)).spec["criterion"]["type"] == "is_handled"


def test_a_search_with_nothing_a_filter_matches_has_no_draft() -> None:
    with pytest.raises(Refusal) as refused:
        search_draft(SearchQuery(text="hello", since=date(2026, 1, 1)))
    assert refused.value.code == "nothing_to_filter"
    assert refused.value.extra == {"dropped": ["text", "since"]}
    with pytest.raises(Refusal):
        search_draft(SearchQuery())
