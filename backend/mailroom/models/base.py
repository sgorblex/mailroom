from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

#: Every table of the app lives here. Core's schema (``core``) and any other history sharing the
#: database (the retired mailroom-mcp's ``mcp``) keep their own objects, which the app never
#: touches.
APP_SCHEMA = "app"

#: Alembic's bookkeeping table, inside ``APP_SCHEMA`` so another Alembic history in the same
#: database (core's) never sees this one.
ALEMBIC_VERSION_TABLE = "alembic_version"

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=APP_SCHEMA, naming_convention=NAMING_CONVENTION)
