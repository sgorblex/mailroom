from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_local_environment() -> None:
    env_file = _repository_root() / ".env"
    if env_file.is_file():
        load_dotenv(env_file)


def normalize_sync_database_url(raw: str) -> str:
    """Rewrite a bare ``postgresql://`` URL to explicitly request the psycopg (v3) driver."""
    if raw.startswith("postgresql://"):
        return f"postgresql+psycopg://{raw.removeprefix('postgresql://')}"
    return raw


def get_sync_database_url() -> str:
    load_local_environment()
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        message = (
            "DATABASE_URL is not set. Copy .env.example to .env at the repository root "
            "and adjust values, or export DATABASE_URL for this shell."
        )
        raise RuntimeError(message)
    return normalize_sync_database_url(raw)


def create_database_engine(url: str, *, pool_size: int = 5, max_overflow: int = 10) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
