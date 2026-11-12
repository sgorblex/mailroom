"""Tests for filter classes."""

from mailroom_core.filters.actions.flags import MarkAsRead
from mailroom_core.filters.actions.move import MoveTo
from mailroom_core.filters.criteria.address import FromIs, ToIs
from mailroom_core.filters.criteria.criterion import Anything
from mailroom_core.filters.criteria.subject import SubjectMatches
from mailroom_core.filters.filter import Filter, If
from mailroom_core.filters.path import Path


def test_filter_construction() -> None:
    """Both ways of building a filter give one with a criterion and an action."""
    f = Filter(ToIs("members@club.example.org"), MarkAsRead() + MoveTo(Path("Club")))
    assert f.criterion is not None
    assert f.action is not None
    f2 = If(FromIs("x@y.edu") & SubjectMatches(r"List Digest")).then(
        MarkAsRead() + MoveTo(Path("List"))
    )
    assert f2.criterion is not None
    assert f2.action is not None


class TestFilter:
    """Test Filter (criterion + action, run on folder)."""

    def test_filter_creation(self) -> None:
        criterion = Anything()
        action = MoveTo(Path("TestFolder"))
        filter_obj = Filter(criterion, action)
        assert filter_obj.criterion == criterion
        assert filter_obj.action == action

    def test_filter_with_chained_action(self) -> None:
        """Test Filter(criterion, action1 + action2) builds filter with chained action."""

        criterion = Anything()
        action1 = MoveTo(Path("D1"))
        action2 = MarkAsRead()
        f = Filter(criterion, action1 + action2)
        assert isinstance(f, Filter)
        assert f.criterion is criterion
        assert "mark as read" in f.action.name
