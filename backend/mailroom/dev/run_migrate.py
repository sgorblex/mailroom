from __future__ import annotations

import logging

from mailroom.db_migrations import upgrade_head


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    upgrade_head(wait_for_database=True)


if __name__ == "__main__":
    main()
