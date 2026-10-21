"""A stand-in for privacyfence-sync: just enough for the cockpit's version gate and ``check``."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args == ["--help"]:
        print("usage: privacyfence-sync [--help] {run,check,preflight,permissions} ...")
        return 0
    command, rest = args[0], args[1:]
    if command == "run" and rest == ["--help"]:
        print("usage: privacyfence-sync run RECIPE [--result-file PATH]")
        return 0
    if command == "permissions" and rest == ["--help"]:
        print("usage: privacyfence-sync permissions RECIPE [--json]")
        return 0
    if command == "check" and len(rest) == 1:
        print("fixture: 1 step(s), OK")
        return 0
    if command == "run" and rest:
        if "--result-file" in rest:
            path = rest[rest.index("--result-file") + 1]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "recipe": "fixture", "outcome": "ok", "exit_code": 0}, handle)
        return 0
    print(f"invalid choice: {command!r}", file=sys.stderr)
    return 2
