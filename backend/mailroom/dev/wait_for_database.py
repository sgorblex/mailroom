from __future__ import annotations

import argparse
import logging
import os

from mailroom.database import normalize_sync_database_url
from mailroom.database_connectivity import wait_for_sync_database


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Block until the database at --url-env is reachable, then exit 0."
    )
    parser.add_argument(
        "--url-env",
        default="DATABASE_URL",
        help="Environment variable holding the database URL to wait for (default: DATABASE_URL)",
    )
    args = parser.parse_args()

    raw = os.environ.get(args.url_env, "").strip()
    if not raw:
        raise SystemExit(f"{args.url_env} is not set; nothing to wait for.")

    wait_for_sync_database(normalize_sync_database_url(raw)).dispose()
    logging.getLogger(__name__).info("Database reachable via %s.", args.url_env)


if __name__ == "__main__":
    main()
