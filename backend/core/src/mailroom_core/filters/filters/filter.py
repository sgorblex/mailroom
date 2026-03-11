"""Filter type (criterion + action) and If/FilterBuilder API."""

from __future__ import annotations

from imap_thingy.filters.actions import Action
from imap_thingy.filters.criteria import Criterion


class FilterBuilder:
    """Builder for a Filter; call .then(action) to produce a Filter."""

    def __init__(self, criterion: Criterion) -> None:
        """Create a builder that will use this criterion."""
        self._criterion = criterion

    def then(self, action: Action) -> Filter:
        """Return a Filter with this criterion and the given action."""
        return Filter(self._criterion, action)


def if_(criterion: Criterion) -> FilterBuilder:
    """Return a builder for a filter with this criterion; call .then(action) to build the Filter."""
    return FilterBuilder(criterion)


If = if_


class Filter:
    """Filter that applies an action to messages matching a criterion. Run via Folder.run()."""

    def __init__(self, criterion: Criterion, action: Action) -> None:
        """Build a filter from a criterion and an action."""
        self.criterion = criterion
        self.action = action
