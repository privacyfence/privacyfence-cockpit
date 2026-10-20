"""A fake `privacyfence-sync` executable, so no test needs the real one, pip or a network."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.paths import DataPaths

_SCRIPT = '''#!{python}
import json, os, sys, time
from pathlib import Path

OPTS = json.loads({options})


def say(text, stream=sys.stdout):
    print(text, file=stream, flush=True)


def lines():
    for line in OPTS["lines"]:
        say(line)


args = sys.argv[1:]
if OPTS["record"]:
    with open(OPTS["record"], "a") as handle:
        handle.write(json.dumps({{"argv": args, "env": dict(os.environ), "cwd": os.getcwd()}}) + "\\n")

command = args[0] if args else None
rest = args[1:]
if command in (None, "--help"):
    say("usage: privacyfence-sync [-h] {{run,check,preflight,login}} ...")
    sys.exit(0)
if command == "run" and "--help" in rest:
    say("usage: privacyfence-sync run [-h] [--server URL] [--attended]"
        + (" [--result-file PATH]" if OPTS["help_result_file"] else "") + " recipe")
    sys.exit(0)
if command == "permissions":
    if not OPTS["help_permissions"]:
        say("privacyfence-sync: error: argument command: invalid choice: 'permissions'", sys.stderr)
        sys.exit(2)
    if "--help" in rest:
        say("usage: privacyfence-sync permissions [-h] [--json] recipe")
        sys.exit(0)
    if OPTS["permissions_json"] is not None:
        say(json.dumps(OPTS["permissions_json"]))
    sys.exit(OPTS["exit_code"])
if command in ("check", "preflight"):
    lines()
    sys.exit(OPTS["exit_code"])
if command == "run":
    lines()
    time.sleep(OPTS["sleep"])
    if "--result-file" in rest and OPTS["outcome"] is not None:
        target = Path(rest[rest.index("--result-file") + 1])
        target.write_text(json.dumps({{
            "version": 1,
            "recipe": "fixture",
            "outcome": OPTS["outcome"],
            "exit_code": OPTS["exit_code"],
            "stopped_at_step": "build",
            "started_at": "2026-10-20T06:15:00Z",
            "finished_at": "2026-10-20T06:15:42Z",
            "privacyfence_sync_version": "0.0.0+fake",
        }}))
    sys.exit(OPTS["exit_code"])
say("privacyfence-sync: error: argument command: invalid choice: " + repr(command), sys.stderr)
sys.exit(2)
'''


def make_fake_sync(
    bin_dir: Path,
    *,
    exit_code: int = 0,
    outcome: str | None = "ok",
    lines: Sequence[str] = (),
    sleep: float = 0.0,
    help_result_file: bool = True,
    help_permissions: bool = True,
    permissions_json: dict[str, Any] | None = None,
    record: Path | None = None,
) -> Path:
    options = {
        "exit_code": exit_code,
        "outcome": outcome,
        "lines": list(lines),
        "sleep": sleep,
        "help_result_file": help_result_file,
        "help_permissions": help_permissions,
        "permissions_json": permissions_json,
        "record": str(record) if record is not None else None,
    }
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "privacyfence-sync"
    script.write_text(_SCRIPT.format(python=sys.executable, options=repr(json.dumps(options))))
    script.chmod(0o755)
    return script


def install_fake_venv(paths: DataPaths, db: Database, **fake_kwargs: Any) -> Path:
    """Lay out a ready, current venv-shaped folder around a fake; returns the venv folder."""
    venv = paths.venvs / "fake"
    make_fake_sync(venv / "bin", **fake_kwargs)
    (venv / ".cockpit-ready").write_text("")
    db.set_setting("venv.current", "fake")
    return venv
