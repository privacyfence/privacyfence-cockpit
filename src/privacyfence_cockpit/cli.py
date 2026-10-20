"""The `cockpit` command."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence

from privacyfence_cockpit.config import ConfigError, EnvConfig

Handler = Callable[[argparse.Namespace], int]


def _serve(args: argparse.Namespace) -> int:
    from privacyfence_cockpit.web.server import serve

    try:
        env = EnvConfig.from_environ(os.environ)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return serve(env)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cockpit", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("serve", help="run the web dashboard and the scheduler").set_defaults(handler=_serve)
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
