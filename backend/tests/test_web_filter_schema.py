"""The web tests' copy of imap-thingy's filter spec schema (``web/src/test/filter-schema.json``),
which the filter editor's tests read, is the installed imap-thingy's."""

from __future__ import annotations

import json
from pathlib import Path

from imap_thingy.spec import json_schema

FIXTURE = Path(__file__).resolve().parents[2] / "web" / "src" / "test" / "filter-schema.json"


def test_the_web_tests_read_imap_thingys_schema() -> None:
    copy = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert copy == json_schema(), (
        f"{FIXTURE} is not imap-thingy's json_schema(): write it out again "
        "(json.dumps(json_schema(), indent=2, ensure_ascii=False))"
    )
