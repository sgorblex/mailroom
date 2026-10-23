from __future__ import annotations

from mailroom.database import libpq_database_url


def test_libpq_database_url_drops_the_sqlalchemy_driver() -> None:
    assert libpq_database_url("postgresql+psycopg://u:p@h:1/d") == "postgresql://u:p@h:1/d"
    assert libpq_database_url("postgresql://u:p@h:1/d") == "postgresql://u:p@h:1/d"
