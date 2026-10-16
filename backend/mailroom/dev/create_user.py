"""Create a user: ``create_user <username> [--admin]``, the password prompted for.

Prompts on a terminal; otherwise reads the password from the first line of stdin. The first user
has to be made this way, as an admin; until the app can manage users, so do the rest.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy.exc import SQLAlchemyError

from mailroom import database
from mailroom.auth.users import UserError, create_user, normalized_username


def _password() -> str | None:
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\r\n")
    password = getpass.getpass("Password: ")
    if getpass.getpass("Again: ") != password:
        print("The passwords differ.", file=sys.stderr)
        return None
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="create-user", description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--admin", action="store_true", help="may manage other users")
    args = parser.parse_args(argv)
    try:
        normalized_username(args.username)
    except UserError as exc:
        print(exc, file=sys.stderr)
        return 1

    password = _password()
    if password is None:
        return 1
    database.load_local_environment()
    try:
        engine = database.create_database_engine(database.get_sync_database_url())
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        with database.create_session_factory(engine)() as db:
            user = create_user(db, args.username, password, is_admin=args.admin)
    except UserError as exc:
        print(exc, file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"Could not reach the database: {type(exc).__name__}.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"Created {'admin ' if user.is_admin else ''}{user.username!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
