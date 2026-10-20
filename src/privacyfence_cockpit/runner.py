"""Runs one recipe through privacyfence-sync in its own process group and records the outcome."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import shutil
import signal
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO

from privacyfence_cockpit.checks import cockpit_problems
from privacyfence_cockpit.effective import Effective
from privacyfence_cockpit.gitrepo import GitManager
from privacyfence_cockpit.history import History
from privacyfence_cockpit.models import (
    EXIT_CODE_OUTCOMES,
    OUTCOME_LABELS,
    RESULT_FILE_OUTCOMES,
    Mode,
    Outcome,
    iso,
    utc_now,
)
from privacyfence_cockpit.paths import DataPaths
from privacyfence_cockpit.recipes import Recipe
from privacyfence_cockpit.replay import REPLAY_FILE, cut_replay
from privacyfence_cockpit.tokens import ResolvedToken, TokenResolver
from privacyfence_cockpit.venvs import VenvManager

SHUTDOWN_NOTE = "the cockpit was shut down"
_POLL_SECONDS = 0.02
_RUN_MODES = frozenset({Mode.ATTENDED, Mode.UNATTENDED, Mode.REPLAY})
NOTHING_TO_REPLAY = "nothing to replay: the first step writes"


@dataclass(frozen=True)
class RunResult:
    run_id: int
    outcome: Outcome
    exit_code: int | None
    log_path: Path


class Runner:
    def __init__(
        self,
        *,
        paths: DataPaths,
        history: History,
        venvs: VenvManager,
        git: GitManager,
        tokens: TokenResolver,
        effective: Callable[[], Effective],
        clock: Callable[[], datetime] = utc_now,
        kill_grace_seconds: float = 10.0,
        timeout_unit_seconds: float = 60.0,
    ) -> None:
        self._paths = paths
        self._history = history
        self._venvs = venvs
        self._git = git
        self._tokens = tokens
        self._effective = effective
        self._clock = clock
        self._grace = kill_grace_seconds
        self._timeout_unit = timeout_unit_seconds

    async def run(
        self,
        recipe: Recipe,
        *,
        mode: Mode,
        trigger: str,
        job_id: str | None,
        on_line: Callable[[str], None] | None = None,
        attended: bool = False,
    ) -> RunResult:
        token = self._tokens.resolve(recipe)
        eff = self._effective()
        start = self._clock()
        commit = self._git.head()
        run_id = self._history.start(
            job_id=job_id,
            trigger=trigger,
            recipe_path=recipe.path,
            mode=mode,
            started_at=start,
            log_path=None,
            commit_sha=commit,
            token_source=token.source,
        )
        log_file = self._log_file(recipe, start, run_id)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        self._history.set_log_path(run_id, log_file.relative_to(self._paths.root).as_posix())
        venv = self._venvs.current()
        result_dir = Path(tempfile.mkdtemp(prefix="cockpit-run-")) if mode in _RUN_MODES else None
        replay_file = recipe.dir / REPLAY_FILE
        try:
            result_file = None if result_dir is None else result_dir / "result.json"
            sync = self._venvs.sync_bin(venv) if venv is not None else self._venvs.sync_bin(Path("venv"))
            nothing_to_replay = mode is Mode.REPLAY and cut_replay(recipe.yaml_path) is None
            argv = self._argv(sync, recipe, mode, attended, result_file)
            with log_file.open("w", encoding="utf-8") as log:
                self._write_header(log, run_id, recipe, mode, trigger, argv, token, commit, start)
                problems = cockpit_problems(recipe, self._paths.repo) if mode is Mode.CHECK else []
                for problem in problems:
                    self._emit(log, on_line, f"# cockpit check: {problem}")
                if nothing_to_replay:
                    self._emit(log, on_line, f"# {NOTHING_TO_REPLAY}")
                    outcome, exit_code, note = Outcome.OK, None, NOTHING_TO_REPLAY
                elif venv is None:
                    reason = self._history_db_setting("venv.last_error") or "no environment has been built"
                    self._emit(log, on_line, f"# the recipe environment is not ready: {reason}")
                    outcome, exit_code, note = Outcome.FAILED, None, None
                else:
                    outcome, exit_code, note = await self._execute(
                        run_id, recipe, mode, argv, self._env(venv, eff, token, mode), eff, result_file, log, on_line
                    )
                    if problems:
                        outcome = Outcome.RECIPE_ERROR
                finished = self._clock()
                self._history.finish(run_id, outcome=outcome, exit_code=exit_code, finished_at=finished, note=note)
                self._history.set_pid(run_id, None)
                self._write_footer(log, exit_code, outcome, start, finished)
        finally:
            if result_dir is not None:
                shutil.rmtree(result_dir, ignore_errors=True)
            if mode is Mode.REPLAY:
                replay_file.unlink(missing_ok=True)
        return RunResult(run_id=run_id, outcome=outcome, exit_code=exit_code, log_path=log_file)

    def stop(self, run_id: int) -> bool:
        row = self._history.request_stop(run_id)
        if row is None or row.status != "running" or row.pid is None:
            return False
        threading.Thread(target=self._terminate_group, args=(row.pid,), daemon=True).start()
        return True

    def _history_db_setting(self, key: str) -> str | None:
        return self._history._db.get_setting(key)

    def _log_file(self, recipe: Recipe, start: datetime, run_id: int) -> Path:
        name = f"{start:%H%M%S}_{recipe.group}_{recipe.folder}_{run_id}.log"
        return self._paths.logs / f"{start:%Y-%m-%d}" / name

    def _argv(
        self, sync: Path, recipe: Recipe, mode: Mode, attended: bool, result_file: Path | None
    ) -> list[str]:
        yaml = str(recipe.dir / REPLAY_FILE if mode is Mode.REPLAY else recipe.yaml_path)
        if mode is Mode.CHECK:
            return [str(sync), "check", yaml]
        if mode is Mode.PREFLIGHT:
            return [str(sync), "preflight", yaml, *(["--attended"] if attended else [])]
        argv = [str(sync), "run", yaml, "--result-file", str(result_file)]
        if mode is Mode.ATTENDED:
            argv.append("--attended")
        return argv

    def command_env(self, recipe: Recipe) -> tuple[Path, dict[str, str]] | None:
        """The current venv and the environment a privacyfence-sync process gets; None without a venv."""
        venv = self._venvs.current()
        if venv is None:
            return None
        return venv, self._env(venv, self._effective(), self._tokens.resolve(recipe))

    def _env(
        self, venv: Path, eff: Effective, token: ResolvedToken, mode: Mode | None = None
    ) -> dict[str, str]:
        env = {
            "PATH": f"{venv}/bin:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self._paths.home),
            "LANG": "C.UTF-8",
            "TZ": eff.timezone,
        }
        if eff.server_url:
            env["PRIVACYFENCE_MCP_URL"] = eff.server_url
        if token.value:
            env["PRIVACYFENCE_TOKEN"] = token.value
        if mode is Mode.REPLAY:
            env["COCKPIT_REPLAY"] = "1"
        return env

    def _write_header(
        self,
        log: IO[str],
        run_id: int,
        recipe: Recipe,
        mode: Mode,
        trigger: str,
        argv: list[str],
        token: ResolvedToken,
        commit: str | None,
        start: datetime,
    ) -> None:
        shown = ["privacyfence-sync", *argv[1:]]
        shown[2] = os.path.relpath(argv[2], self._paths.repo)
        for line in (
            f"# PrivacyFence Cockpit run {run_id}",
            f"# recipe: {recipe.path}",
            f"# mode: {mode}   trigger: {trigger}",
            f"# command: {shlex.join(shown)}",
            f"# token: {token.source}",
            f"# commit: {commit or 'unknown'}",
            f"# started: {iso(start)}",
        ):
            log.write(line + "\n")
        log.flush()

    def _write_footer(
        self, log: IO[str], exit_code: int | None, outcome: Outcome, start: datetime, finished: datetime
    ) -> None:
        log.write(f"# exit code: {exit_code if exit_code is not None else 'none'}\n")
        log.write(f"# outcome: {OUTCOME_LABELS[outcome]}\n")
        log.write(f"# duration: {(finished - start).total_seconds():.1f} s\n")
        log.flush()

    def _emit(self, log: IO[str], on_line: Callable[[str], None] | None, line: str) -> None:
        log.write(line + "\n")
        log.flush()
        if on_line is not None:
            on_line(line)

    async def _execute(
        self,
        run_id: int,
        recipe: Recipe,
        mode: Mode,
        argv: list[str],
        env: dict[str, str],
        eff: Effective,
        result_file: Path | None,
        log: IO[str],
        on_line: Callable[[str], None] | None,
    ) -> tuple[Outcome, int | None, str | None]:
        proc: asyncio.subprocess.Process | None = None
        timed_out = False
        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=recipe.dir,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as error:
                self._emit(log, on_line, f"# could not start privacyfence-sync: {error.strerror or 'error'}")
                return Outcome.FAILED, None, None
            self._history.set_pid(run_id, proc.pid)
            try:
                await asyncio.wait_for(
                    self._pump(proc, log, on_line), eff.timeout_minutes * self._timeout_unit
                )
            except TimeoutError:
                timed_out = True
                await asyncio.to_thread(self._terminate_group, proc.pid)
                await proc.wait()
        except asyncio.CancelledError:
            if proc is not None:
                self._terminate_group(proc.pid, reap=True)
            finished = self._clock()
            self._history.finish(
                run_id, outcome=Outcome.STOPPED, exit_code=None, finished_at=finished, note=SHUTDOWN_NOTE
            )
            self._history.set_pid(run_id, None)
            self._write_footer(log, None, Outcome.STOPPED, finished, finished)
            raise
        exit_code = proc.returncode
        row = self._history.get(run_id)
        if row is not None and row.stop_requested:
            return Outcome.STOPPED, exit_code, None
        if timed_out:
            return Outcome.TIMED_OUT, exit_code, None
        if mode in _RUN_MODES:
            return self._result_outcome(result_file), exit_code, None
        return EXIT_CODE_OUTCOMES.get(exit_code if exit_code is not None else -1, Outcome.FAILED), exit_code, None

    async def _pump(
        self, proc: asyncio.subprocess.Process, log: IO[str], on_line: Callable[[str], None] | None
    ) -> None:
        assert proc.stdout is not None  # noqa: S101  # nosec B101  # stdout is a PIPE
        async for raw in proc.stdout:
            self._emit(log, on_line, raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        await proc.wait()

    def _result_outcome(self, result_file: Path | None) -> Outcome:
        if result_file is None:
            return Outcome.FAILED
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
            return RESULT_FILE_OUTCOMES.get(data["outcome"], Outcome.FAILED)
        except (OSError, ValueError, KeyError, TypeError):
            return Outcome.FAILED

    def _terminate_group(self, pgid: int, reap: bool = False) -> None:
        """SIGTERM the group, wait for the grace period, then SIGKILL what is left.

        `reap` is for shutdown, where the event loop is not running to collect the leader's exit.
        """
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        if self._wait_gone(pgid, self._grace, reap):
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
        self._wait_gone(pgid, 2.0, reap)

    def _wait_gone(self, pgid: int, seconds: float, reap: bool) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            if reap:
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(pgid, os.WNOHANG)
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_POLL_SECONDS)
