"""The web tests' copy of the filter spec schema (``web/src/test/filter-schema.json``), which the
filter editor's tests read, is the one the app serves (``offered_schema``)."""

from __future__ import annotations

import json
from pathlib import Path

from mailroom.filters.spec import offered_schema

FIXTURE = Path(__file__).resolve().parents[2] / "web" / "src" / "test" / "filter-schema.json"


def test_the_web_tests_read_the_offered_schema() -> None:
    copy = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert copy == offered_schema(), (
        f"{FIXTURE} is not the app's offered_schema(): write it out again "
        "(json.dumps(offered_schema(), indent=2, ensure_ascii=False))"
    )
