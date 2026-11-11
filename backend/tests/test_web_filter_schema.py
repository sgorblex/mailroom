"""The web tests' copy of the filter spec schema (``web/src/test/filter-schema.json``), which the
filter editor's tests read, is the filter engine's."""

from __future__ import annotations

import json
from pathlib import Path

from mailroom_core.filters import json_schema

FIXTURE = Path(__file__).resolve().parents[2] / "web" / "src" / "test" / "filter-schema.json"


def test_the_web_tests_read_the_engines_schema() -> None:
    copy = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert copy == json_schema(), (
        f"{FIXTURE} is not the filter engine's json_schema(): write it out again "
        "(json.dumps(json_schema(), indent=2, ensure_ascii=False))"
    )
