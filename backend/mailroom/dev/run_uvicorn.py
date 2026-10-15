from __future__ import annotations

import os

import uvicorn

from mailroom.database import load_local_environment


def ensure_development_default_startup_migrations() -> None:
    """Match container behaviour: run Alembic at API boot when the flag is still unset.

    ``backend/Containerfile`` sets ``MAILROOM_RUN_STARTUP_MIGRATIONS``; local ``uvicorn`` starts
    typically did not, so the database never migrated until ``mise run migrate``. Direct
    ``uvicorn mailroom.api.main:application`` (without this entrypoint) still requires an
    explicit env or Containerfile default.
    """
    if os.environ.get("MAILROOM_RUN_STARTUP_MIGRATIONS", "").strip() == "":
        os.environ["MAILROOM_RUN_STARTUP_MIGRATIONS"] = "1"


def _parse_api_port() -> int:
    raw = os.environ.get("MAILROOM_API_PORT")
    if raw is None or not raw.strip():
        return 8100
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid MAILROOM_API_PORT value {raw!r}; expected an integer.") from exc


def main() -> None:
    load_local_environment()
    ensure_development_default_startup_migrations()
    host = (os.environ.get("MAILROOM_API_HOST") or "").strip() or "127.0.0.1"
    port = _parse_api_port()
    uvicorn.run(
        "mailroom.api.main:application",
        host=host,
        port=port,
        reload=True,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
