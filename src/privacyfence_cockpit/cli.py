"""The `cockpit` command."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import subprocess  # nosec B404  # fixed argv, no shell; only `cockpit test` runs the recipe repository's pytest
import sys
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from privacyfence_cockpit.config import ConfigError, EnvConfig

if TYPE_CHECKING:
    from privacyfence_cockpit.models import Mode, Outcome
    from privacyfence_cockpit.recipes import Recipe
    from privacyfence_cockpit.services import Cockpit

Handler = Callable[[argparse.Namespace], int]
Body = Callable[["Cockpit", argparse.Namespace], int]

WAITING = "Waiting for the cockpit's current run to finish…"
INTERRUPTED = 130
MAX_REPEAT = 100
PYTEST_MISSING = "Add pytest to requirements.txt to use cockpit test."
UNDECRYPTABLE = "A stored secret cannot be decrypted with this master key. Delete it and set it again."


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


def _command(body: Body) -> Handler:
    """Wraps a command body: opens the services, maps expected failures to one line and exit 2, delivers the
    notifications the command raised, and closes the database."""

    def handler(args: argparse.Namespace) -> int:
        from privacyfence_cockpit.crypto import SecretDecryptError
        from privacyfence_cockpit.gitrepo import GitError
        from privacyfence_cockpit.venvs import VenvError

        cockpit = _open_cockpit()
        if cockpit is None:
            return 2
        try:
            try:
                code = body(cockpit, args)
            except SecretDecryptError:
                print(UNDECRYPTABLE, file=sys.stderr)
                code = 2
            except (GitError, VenvError) as exc:
                print(str(exc), file=sys.stderr)
                code = 2
            except KeyboardInterrupt:
                # The runner has already stopped the run; the notifications it raised still go out below.
                print("Interrupted.", file=sys.stderr)
                code = INTERRUPTED
            asyncio.run(cockpit.notifier.deliver_pending())
        finally:
            cockpit.db.close()
        return code

    return handler


def _select(cockpit: Cockpit, selectors: Sequence[str]) -> list[Recipe] | None:
    from privacyfence_cockpit.recipes import SelectionError, select

    try:
        return select(cockpit.recipes(), selectors)
    except SelectionError as exc:
        print(str(exc), file=sys.stderr)
        return None


def _list(cockpit: Cockpit, args: argparse.Namespace) -> int:
    from itertools import groupby

    from privacyfence_cockpit.models import OUTCOME_LABELS
    from privacyfence_cockpit.web.app import localtime

    recipes = cockpit.recipes()
    if not recipes:
        print("No recipes found.")
        return 0
    tz = cockpit.effective().timezone
    last = cockpit.history.last_by_recipe()
    for group, members in groupby(recipes, key=lambda r: r.group_folder):
        print(group)
        for recipe in members:
            row = last.get(recipe.path)
            if row is None or row.outcome is None:
                label, when = "", "never"
            else:
                label, when = OUTCOME_LABELS[row.outcome], localtime(row.finished_at or row.started_at, tz)
            print(f"  {recipe.folder:<32} {label:<14} {when}")
    return 0


def _permissions(cockpit: Cockpit, args: argparse.Namespace) -> int:
    from privacyfence_cockpit.permissions import PermissionsReport, collect, merge, render_merged, render_recipe

    chosen = _select(cockpit, args.paths)
    if chosen is None:
        return 1

    async def collect_all() -> list[PermissionsReport]:
        return [await collect(recipe, cockpit.runner) for recipe in chosen]

    reports = asyncio.run(collect_all())
    if args.json:
        print(
            json.dumps(
                {"recipes": [{"recipe": r.recipe, "report": r.data, "error": r.error} for r in reports]},
                indent=2,
            )
        )
    elif args.union or args.by_group:
        groups = {r.path: r.group for r in cockpit.recipes()}
        print("\n".join(render_merged(merge(reports, "union" if args.union else "group", groups))))
    else:
        print("\n\n".join("\n".join(render_recipe(report)) for report in reports))
    return 0


def _read_token() -> str:
    if sys.stdin.isatty():
        try:
            return getpass.getpass("Token: ")
        except EOFError:
            return ""
    return sys.stdin.readline()


def _token_set(cockpit: Cockpit, args: argparse.Namespace) -> int:
    from privacyfence_cockpit.tokens import TokenNameError, TokenValueError, check_token_name, clean_token_value
    from privacyfence_cockpit.web.routes_tokens import STATE_LABELS

    try:
        check_token_name(args.name, cockpit.recipes())
        value = clean_token_value(_read_token())
    except (TokenNameError, TokenValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    cockpit.store.set("token", args.name, value)
    cockpit.token_checker.forget(args.name)
    result = asyncio.run(cockpit.token_checker.check_one(args.name))
    parts = [f"{args.name}: {STATE_LABELS[result.state]}"]
    if result.label:
        parts.append(result.label)
    if result.expires_at:
        parts.append(f"expires {result.expires_at}")
    if result.days_left is not None:
        parts.append(f"({result.days_left} day(s) left)")
    print(" ".join(parts))
    if result.error:
        print(result.error)
    return 0


def _token_show(cockpit: Cockpit, args: argparse.Namespace) -> int:
    from privacyfence_cockpit.token_check import TokenState
    from privacyfence_cockpit.tokens import valid_token_names
    from privacyfence_cockpit.web.routes_tokens import STATE_LABELS

    stored = set(cockpit.store.names("token"))
    status = {row["name"]: row for row in cockpit.db.query("SELECT * FROM token_status")}
    for name in valid_token_names(cockpit.recipes()):
        row = status.get(name)
        details: list[str] = []
        if name in stored and row is not None:
            details.append(STATE_LABELS[TokenState(row["state"])])
            if row["label"]:
                details.append(row["label"])
            if row["days_left"] is not None:
                details.append(str(row["days_left"]))
        print(f"{name:<40} {'present' if name in stored else 'missing'}  {' '.join(details)}".rstrip())
    return 0


def _token_delete(cockpit: Cockpit, args: argparse.Namespace) -> int:
    if not cockpit.store.delete("token", args.name):
        print(f"no token named {args.name}", file=sys.stderr)
        return 1
    cockpit.token_checker.forget(args.name)
    print(f"deleted {args.name}")
    return 0


def _schedule_list(cockpit: Cockpit, args: argparse.Namespace) -> int:
    from privacyfence_cockpit.cockpit_yaml import CockpitConfigError
    from privacyfence_cockpit.models import iso
    from privacyfence_cockpit.scheduler import next_fire
    from privacyfence_cockpit.web.app import localtime

    try:
        config = cockpit.config()
    except CockpitConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not config.schedules:
        print("No schedules.")
        return 0
    tz = cockpit.effective().timezone
    now = cockpit.clock()
    for schedule in config.schedules:
        paused = cockpit.scheduler.is_paused(schedule.name)
        upcoming = "-" if paused else localtime(iso(next_fire(schedule, tz, now)), tz)
        print(
            f"{schedule.name}  {schedule.cron}  {tz}  {'paused' if paused else 'active'}"
            f"  next {upcoming}  run: {', '.join(schedule.run)}"
        )
    return 0


def _git_status(cockpit: Cockpit, args: argparse.Namespace) -> int:
    from privacyfence_cockpit.web.app import localtime
    from privacyfence_cockpit.web.routes_code import display_url

    status = cockpit.git.status()
    if not status.configured:
        print("No recipe repository is configured. Set one on the Code page.")
        return 0
    tz = cockpit.effective().timezone
    print(f"Repository:  {display_url(cockpit.db.get_setting('git.repo_url') or '')}")
    print(f"Branch:      {status.branch}")
    if not status.cloned:
        checkout = "not cloned yet"
    elif status.pinned_sha:
        checkout = f"pinned to {status.pinned_sha[:7]}"
    else:
        checkout = f"tracking {status.branch} at {(status.head or '')[:7]}"
    if status.dirty:
        checkout += " (has changes to tracked files)"
    print(f"Checkout:    {checkout}")
    if status.cloned:
        print(f"Remote tip:  {(status.remote_tip or 'unknown')[:7]}")
        print(f"New commits: {status.new_commits}")
    last = localtime(status.last_fetch_ok_at, tz) if status.last_fetch_ok_at else "never"
    print(f"Last fetch:  {last}")
    if status.last_fetch_error:
        print(f"Error:       {status.last_fetch_error}")
    return 0


def _git_update(cockpit: Cockpit, args: argparse.Namespace) -> int:
    before = cockpit.git.head()
    cockpit.lock.acquire(lambda: print(WAITING, file=sys.stderr))
    try:
        cockpit.git.update()
        result = cockpit.venvs.ensure()
    finally:
        cockpit.lock.release()
    after = cockpit.git.head()
    print("already up to date" if after == before else f"updated to {(after or '')[:7]}")
    if result.error:
        print(result.error, file=sys.stderr)
        return 2
    print("recipe environment built" if result.built else "recipe environment ready")
    return 0


def _repeat(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if not 1 <= value <= MAX_REPEAT:
        raise argparse.ArgumentTypeError(f"must be a whole number between 1 and {MAX_REPEAT}")
    return value


def _exit_code(outcome: Outcome) -> int:
    """The exit code `cockpit run` reports for a recipe outcome (plan §L); stopped and timed out are 2."""
    from privacyfence_cockpit.models import EXIT_CODE_OUTCOMES

    for code, mapped in EXIT_CODE_OUTCOMES.items():
        if mapped is outcome:
            return code
    return 2


def _execute(
    cockpit: Cockpit,
    chosen: Sequence[Recipe],
    mode: Mode,
    *,
    attended: bool = False,
    repeat: int = 1,
    stop_on_error: bool = False,
) -> tuple[int, int]:
    """Runs one manual job over the recipes in-process, printing the log lines as they come and one summary line
    per run. The worker takes the run lock itself. Returns (exit code, number of runs that did not succeed)."""
    from privacyfence_cockpit.jobs import Job, JobKind, Worker
    from privacyfence_cockpit.models import OUTCOME_LABELS, SUCCESS_OUTCOMES

    worker = Worker(
        queue=cockpit.queue,
        runner=cockpit.runner,
        git=cockpit.git,
        venvs=cockpit.venvs,
        history=cockpit.history,
        lock=cockpit.lock,
        recipes=cockpit.recipes,
        effective=cockpit.effective,
        events=cockpit.notifier,
        on_lock_wait=lambda: print(WAITING, file=sys.stderr, flush=True),
        on_line=lambda line: print(line, flush=True),
    )
    job = Job(
        JobKind.RUN,
        "manual",
        selectors=tuple(recipe.path for recipe in chosen),
        mode=mode,
        repeat=repeat,
        stop_on_error=stop_on_error,
        attended=attended,
    )
    summary = asyncio.run(worker.execute(job))
    code = 0
    failed = 0
    for run_id in summary.run_ids:
        row = cockpit.history.get(run_id)
        if row is None or row.outcome is None:
            continue
        exit_text = f"exit {row.exit_code}" if row.exit_code is not None else "no exit code"
        print(f"{row.recipe_path}: {OUTCOME_LABELS[row.outcome]} ({exit_text})")
        if row.outcome not in SUCCESS_OUTCOMES:
            failed += 1
            code = code or _exit_code(row.outcome)
    return code, failed


def _check(cockpit: Cockpit, args: argparse.Namespace) -> int:
    from privacyfence_cockpit.models import Mode

    chosen = _select(cockpit, args.paths)
    if chosen is None:
        return 1
    code, failed = _execute(cockpit, chosen, Mode.CHECK)
    print(f"check: {len(chosen)} recipe(s), {failed} failed")
    return code


def _preflight(cockpit: Cockpit, args: argparse.Namespace) -> int:
    from privacyfence_cockpit.models import Mode

    chosen = _select(cockpit, args.paths)
    if chosen is None:
        return 1
    return _execute(cockpit, chosen, Mode.PREFLIGHT, attended=args.attended)[0]


def _run(cockpit: Cockpit, args: argparse.Namespace) -> int:
    from privacyfence_cockpit.models import Mode

    chosen = _select(cockpit, args.paths)
    if chosen is None:
        return 1
    if args.dry_run:
        for recipe in chosen:
            print(f"would run: {recipe.path}")
        return 0
    mode = Mode.ATTENDED if args.attended else Mode.UNATTENDED
    return _execute(cockpit, chosen, mode, repeat=args.repeat, stop_on_error=args.stop_on_error)[0]


def _test(cockpit: Cockpit, args: argparse.Namespace) -> int:
    from privacyfence_cockpit.models import Mode

    rest = list(args.args)
    if rest[:1] == ["--"]:
        rest = rest[1:]
    if args.live:
        chosen = _select(cockpit, rest)
        if chosen is None:
            return 1
        return _execute(cockpit, chosen, Mode.REPLAY)[0]
    return _pytest(cockpit, rest)


def _pytest(cockpit: Cockpit, pytest_args: Sequence[str]) -> int:
    """Runs the recipe repository's own tests with the recipe environment's Python, holding the run lock."""
    cockpit.lock.acquire(lambda: print(WAITING, file=sys.stderr, flush=True))
    try:
        result = cockpit.venvs.ensure()
        if result.path is None:
            print(result.error or "The recipe environment is not ready.", file=sys.stderr)
            return 2
        python = str(result.path / "bin" / "python")
        # No token and nothing else from the cockpit's own environment reaches the tests.
        env = {
            "PATH": f"{result.path}/bin:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(cockpit.paths.home),
            "LANG": "C.UTF-8",
        }
        cwd = cockpit.paths.repo
        probe = subprocess.run(  # noqa: S603  # nosec B603  # fixed argv, no shell; the recipe environment's python
            [python, "-c", "import pytest"], cwd=cwd, env=env, capture_output=True, check=False
        )
        if probe.returncode != 0:
            print(PYTEST_MISSING, file=sys.stderr)
            return 1
        tests = subprocess.run(  # noqa: S603  # nosec B603  # fixed argv, no shell; arguments are the owner's own
            [python, "-m", "pytest", "-q", *pytest_args], cwd=cwd, env=env, check=False
        )
    finally:
        cockpit.lock.release()
    return 0 if tests.returncode == 0 else 1


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
    commands.add_parser("list", help="list the recipes and their last run").set_defaults(handler=_command(_list))
    perms = commands.add_parser("permissions", help="show the rules the recipes need on PrivacyFence")
    perms.add_argument("paths", nargs="*", metavar="PATH")
    view = perms.add_mutually_exclusive_group()
    view.add_argument("--union", action="store_true", help="one set of rules for all selected recipes")
    view.add_argument("--by-group", action="store_true", help="one set of rules per group")
    perms.add_argument("--json", action="store_true", help="print the raw reports as JSON")
    perms.set_defaults(handler=_command(_permissions))
    check = commands.add_parser("check", help="check the recipes without running them")
    check.add_argument("paths", nargs="*", metavar="PATH")
    check.set_defaults(handler=_command(_check))
    preflight = commands.add_parser("preflight", help="ask PrivacyFence which steps the recipes would be denied")
    preflight.add_argument("paths", nargs="*", metavar="PATH")
    preflight.add_argument("--attended", action="store_true", help="wait for approvals")
    preflight.set_defaults(handler=_command(_preflight))
    run = commands.add_parser("run", help="run recipes now")
    run.add_argument("paths", nargs="*", metavar="PATH")
    run.add_argument("--attended", action="store_true", help="wait for approvals")
    run.add_argument("-n", "--dry-run", action="store_true", help="list the recipes without running them")
    run.add_argument("--stop-on-error", action="store_true", help="skip the rest after a recipe that did not succeed")
    run.add_argument("--repeat", type=_repeat, default=1, metavar="N", help="run each recipe up to N times while ok")
    run.set_defaults(handler=_command(_run))
    test = commands.add_parser("test", help="run the recipe repository's tests, or --live to replay the read steps")
    test.add_argument("--live", action="store_true", help="replay the read-only steps of the recipes (PATH…)")
    test.add_argument("args", nargs="*", metavar="ARGS", help="pytest arguments after --, or recipe PATHs with --live")
    test.set_defaults(handler=_command(_test))
    token = commands.add_parser("token", help="manage the stored PrivacyFence tokens").add_subparsers(
        dest="token_command", required=True, metavar="COMMAND"
    )
    token_set = token.add_parser("set", help="store a token, read from the terminal or one line of stdin")
    token_set.add_argument("name", metavar="NAME")
    token_set.set_defaults(handler=_command(_token_set))
    token.add_parser("show", help="list the token names and whether each is stored").set_defaults(
        handler=_command(_token_show)
    )
    token_delete = token.add_parser("delete", help="delete a stored token")
    token_delete.add_argument("name", metavar="NAME")
    token_delete.set_defaults(handler=_command(_token_delete))
    schedule = commands.add_parser("schedule", help="inspect the schedules").add_subparsers(
        dest="schedule_command", required=True, metavar="COMMAND"
    )
    schedule.add_parser("list", help="list the schedules (cockpit.yaml and the dashboard)").set_defaults(
        handler=_command(_schedule_list)
    )
    git = commands.add_parser("git", help="inspect and update the recipe checkout").add_subparsers(
        dest="git_command", required=True, metavar="COMMAND"
    )
    git.add_parser("status", help="show the checkout and its tracking state").set_defaults(
        handler=_command(_git_status)
    )
    git.add_parser("update", help="fast-forward the checkout and rebuild the recipe environment").set_defaults(
        handler=_command(_git_update)
    )
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
