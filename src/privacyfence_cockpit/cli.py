"""The `cockpit` command."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from privacyfence_cockpit.config import ConfigError, EnvConfig

if TYPE_CHECKING:
    from privacyfence_cockpit.services import Cockpit

Handler = Callable[[argparse.Namespace], int]


def _serve(args: argparse.Namespace) -> int:
    from privacyfence_cockpit.web.server import serve

    try:
        env = EnvConfig.from_environ(os.environ)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return serve(env)


def _open_cockpit() -> Cockpit | None:
    """Opens the services from the environment; prints the error and returns None when that fails."""
    from privacyfence_cockpit.crypto import MasterKeyError
    from privacyfence_cockpit.db import DatabaseError
    from privacyfence_cockpit.services import Cockpit

    try:
        return Cockpit.open(EnvConfig.from_environ(os.environ))
    except (MasterKeyError, ConfigError, DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return None


def _user_reset_password(args: argparse.Namespace) -> int:
    from privacyfence_cockpit.web.auth import OWNER_HASH_SETTING, delete_all_except, new_setup_code
    from privacyfence_cockpit.web.server import setup_hint

    cockpit = _open_cockpit()
    if cockpit is None:
        return 2
    try:
        cockpit.db.delete_setting(OWNER_HASH_SETTING)
        delete_all_except(cockpit.db, None)
        code = new_setup_code(cockpit.db)
        # Printed for the owner at the terminal only; it is never logged.
        print(f"Password cleared. Setup code: {code}")
        print(setup_hint(cockpit.env))
        asyncio.run(cockpit.notifier.deliver_pending())
    finally:
        cockpit.db.close()
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cockpit", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("serve", help="run the web dashboard and the scheduler").set_defaults(handler=_serve)
    user = commands.add_parser("user", help="manage the owner's password").add_subparsers(
        dest="user_command", required=True, metavar="COMMAND"
    )
    user.add_parser(
        "reset-password", help="clear the password and every session, and print a new setup code"
    ).set_defaults(handler=_user_reset_password)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 for a usage error; the cockpit's own code for that is 1 (plan §L).
        return 0 if exc.code in (0, None) else 1
    handler: Handler = args.handler
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
