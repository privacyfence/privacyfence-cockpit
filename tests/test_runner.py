import asyncio
import io
import json
import os
import shutil
import sys
import threading
import time
from datetime import UTC, datetime

import pytest

from privacyfence_cockpit import runner as runner_module
from privacyfence_cockpit.db import Database
from privacyfence_cockpit.effective import Effective
from privacyfence_cockpit.events import NullSink
from privacyfence_cockpit.gitrepo import GitManager, no_credentials
from privacyfence_cockpit.history import History
from privacyfence_cockpit.models import RESULT_FILE_OUTCOMES, Mode, Outcome
from privacyfence_cockpit.paths import DataPaths
from privacyfence_cockpit.recipes import discover
from privacyfence_cockpit.runner import LINE_TRUNCATED, SHUTDOWN_NOTE, Runner
from privacyfence_cockpit.secrets_store import SecretStore
from privacyfence_cockpit.tokens import TokenResolver
from privacyfence_cockpit.venvs import VenvManager
from tests.fakesync import install_fake_venv
from tests.gitutil import git

TOKEN = "pfst_" + "x" * 30
NOW = datetime(2026, 10, 20, 6, 15, 7, tzinfo=UTC)


class Env:
    def __init__(self, tmp_path):
        self.paths = DataPaths(tmp_path / "data").ensure()
        self.db = Database(self.paths.db_file)
        self.db.migrate()
        self.history = History(self.db)
        self.store = SecretStore(self.paths.secrets, os.urandom(32))
        self.record = tmp_path / "record.jsonl"
        self.server_url = "https://pf.example.invalid/mcp"
        self.timeout_minutes = 60
        self.env_token = None
        self.repo = self.paths.repo
        recipe_dir = self.repo / "workflows" / "10-imports" / "10-sf"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "recipe.yaml").write_text("name: fixture\n")
        (self.repo / ".gitignore").write_text("work/\n")
        git(self.repo, "init", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", "initial")
        self.sha = git(self.repo, "rev-parse", "HEAD")
        self.recipe = discover(self.repo)[0]

    def venv(self, **kwargs):
        kwargs.setdefault("record", self.record)
        return install_fake_venv(self.paths, self.db, **kwargs)

    def effective(self):
        return Effective(
            server_url=self.server_url,
            server_source="settings" if self.server_url else "none",
            timezone="Europe/Budapest",
            timezone_source="settings",
            timeout_minutes=self.timeout_minutes,
            retention_days=90,
            fetch_interval_minutes=15,
            auto_update=False,
        )

    def runner(self, **kwargs):
        kwargs.setdefault("kill_grace_seconds", 0.5)
        return Runner(
            paths=self.paths,
            history=self.history,
            venvs=VenvManager(self.paths, self.db, NullSink()),
            git=GitManager(self.paths, self.db, no_credentials, NullSink()),
            tokens=TokenResolver(self.store, self.env_token),
            effective=self.effective,
            clock=lambda: NOW,
            **kwargs,
        )

    def records(self):
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    async def run(self, runner=None, mode=Mode.UNATTENDED, **kwargs):
        return await (runner or self.runner()).run(
            self.recipe, mode=mode, trigger="manual", job_id="job1", **kwargs
        )


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


def log_lines(result):
    return result.log_path.read_text().splitlines()


async def wait_for_pid(env, run_id=1):
    for _ in range(200):
        row = env.history.get(run_id)
        if row is not None and row.pid:
            return row.pid
        await asyncio.sleep(0.02)
    raise AssertionError("the run never started")


def assert_group_gone(pgid):
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)


@pytest.mark.parametrize(
    ("mode", "attended", "expected"),
    [
        (Mode.UNATTENDED, False, ["run", "{yaml}", "--result-file", "{result}"]),
        (Mode.ATTENDED, False, ["run", "{yaml}", "--result-file", "{result}", "--attended"]),
        (Mode.PREFLIGHT, False, ["preflight", "{yaml}"]),
        (Mode.PREFLIGHT, True, ["preflight", "{yaml}", "--attended"]),
        (Mode.CHECK, True, ["check", "{yaml}"]),
    ],
)
async def test_argv_per_mode(env, mode, attended, expected):
    env.venv()
    await env.run(mode=mode, attended=attended)
    argv = env.records()[0]["argv"]
    yaml = str(env.recipe.yaml_path)
    if "--result-file" in argv:
        result = argv[argv.index("--result-file") + 1]
        assert result.endswith("/result.json")
        assert not os.path.exists(os.path.dirname(result))
    else:
        result = ""
    assert argv == [a.format(yaml=yaml, result=result) for a in expected]


def write_recipe(env, text):
    env.recipe.yaml_path.write_text(text)


async def test_replay_argv_env_and_file_cleanup(env):
    env.venv()
    write_recipe(env, "name: x\nsteps:\n- id: a\n  tool: drive_list_files\n- id: b\n  tool: gmail_send\n")
    result = await env.run(mode=Mode.REPLAY)
    record = env.records()[0]
    replay = str(env.recipe.dir / ".replay.yaml")
    assert record["argv"][:2] == ["run", replay]
    assert "--result-file" in record["argv"]
    assert "--attended" not in record["argv"]
    assert record["env"]["COCKPIT_REPLAY"] == "1"
    assert result.outcome is Outcome.OK
    assert not (env.recipe.dir / ".replay.yaml").exists()


async def test_replay_file_deleted_after_a_failing_run(env):
    env.venv(exit_code=2, outcome="failed")
    write_recipe(env, "name: x\nsteps:\n- id: a\n  tool: drive_list_files\n")
    result = await env.run(mode=Mode.REPLAY)
    assert result.outcome is Outcome.FAILED
    assert not (env.recipe.dir / ".replay.yaml").exists()


async def test_replay_nothing_to_replay(env):
    env.venv()
    write_recipe(env, "name: x\nsteps:\n- id: b\n  tool: gmail_send\n")
    result = await env.run(mode=Mode.REPLAY)
    assert result.outcome is Outcome.OK
    assert result.exit_code is None
    assert not env.record.exists()
    assert not (env.recipe.dir / ".replay.yaml").exists()
    assert env.history.get(result.run_id).note == "nothing to replay: the first step writes"
    assert "# nothing to replay: the first step writes" in log_lines(result)


async def test_check_mode_problem_is_recipe_error(env):
    env.venv(exit_code=0)
    (env.repo / ".gitignore").write_text("")
    write_recipe(env, "name: x\n")
    result = await env.run(mode=Mode.CHECK)
    assert result.outcome is Outcome.RECIPE_ERROR
    assert result.exit_code == 0
    expected = "# cockpit check: work_dir workflows/10-imports/10-sf/work is not git-ignored"
    assert any(line.startswith(expected) for line in log_lines(result))


async def test_check_mode_without_problem_uses_exit_code(env):
    env.venv(exit_code=0)
    write_recipe(env, "name: x\n")
    result = await env.run(mode=Mode.CHECK)
    assert result.outcome is Outcome.OK
    assert not any(line.startswith("# cockpit check:") for line in log_lines(result))


def test_command_env(env):
    runner = env.runner()
    assert runner.command_env(env.recipe) is None
    venv = env.venv()
    found_venv, found_env = runner.command_env(env.recipe)
    assert found_venv == venv
    assert found_env["PATH"].startswith(f"{venv}/bin:")
    assert "COCKPIT_REPLAY" not in found_env


async def test_env_has_exactly_the_documented_keys(env):
    venv = env.venv()
    env.store.set("token", "default", TOKEN)
    result = await env.run()
    assert result.outcome is Outcome.OK
    record = env.records()[0]
    # the fake itself adds nothing; the interpreter may add LC_CTYPE when it coerces the C locale
    seen = {k for k in record["env"] if k != "LC_CTYPE"}
    assert seen == {"PATH", "HOME", "LANG", "TZ", "PRIVACYFENCE_MCP_URL", "PRIVACYFENCE_TOKEN"}
    assert record["env"]["PATH"] == f"{venv}/bin:/usr/local/bin:/usr/bin:/bin"
    assert record["env"]["HOME"] == str(env.paths.home)
    assert record["env"]["LANG"] == "C.UTF-8"
    assert record["env"]["TZ"] == "Europe/Budapest"
    assert record["env"]["PRIVACYFENCE_MCP_URL"] == env.server_url
    assert record["env"]["PRIVACYFENCE_TOKEN"] == TOKEN
    assert os.path.samefile(record["cwd"], env.recipe.dir)


async def test_env_omits_token_and_server_when_absent(env):
    env.venv()
    env.server_url = None
    await env.run()
    seen = {k for k in env.records()[0]["env"] if k != "LC_CTYPE"}
    assert seen == {"PATH", "HOME", "LANG", "TZ"}


@pytest.mark.parametrize("name", sorted(RESULT_FILE_OUTCOMES))
async def test_outcome_from_result_file(env, name):
    env.venv(outcome=name, exit_code=0)
    result = await env.run()
    assert result.outcome is RESULT_FILE_OUTCOMES[name]
    assert env.history.get(result.run_id).outcome is RESULT_FILE_OUTCOMES[name]


async def test_missing_or_unknown_result_file_is_failed(env):
    env.venv(outcome=None)
    assert (await env.run()).outcome is Outcome.FAILED
    env.venv(outcome="surprise")
    assert (await env.run()).outcome is Outcome.FAILED


async def test_unreadable_result_file_is_failed(env):
    runner = env.runner()
    assert runner._result_outcome(None) is Outcome.FAILED
    assert runner._result_outcome(env.paths.root / "missing.json") is Outcome.FAILED
    broken = env.paths.root / "broken.json"
    broken.write_text("[1]")
    assert runner._result_outcome(broken) is Outcome.FAILED


@pytest.mark.parametrize(
    ("code", "expected"),
    [(0, Outcome.OK), (1, Outcome.RECIPE_ERROR), (2, Outcome.FAILED), (3, Outcome.DENIED),
     (4, Outcome.NOT_SIGNED_IN), (9, Outcome.FAILED)],
)
@pytest.mark.parametrize("mode", [Mode.PREFLIGHT, Mode.CHECK])
async def test_preflight_and_check_outcome_from_exit_code(env, mode, code, expected):
    env.venv(exit_code=code, outcome="ok")
    result = await env.run(mode=mode)
    assert result.outcome is expected
    assert result.exit_code == code


async def test_run_mode_exit_code_is_recorded_but_result_file_decides(env):
    env.venv(exit_code=3, outcome="denied")
    result = await env.run()
    assert (result.outcome, result.exit_code) == (Outcome.DENIED, 3)
    row = env.history.get(result.run_id)
    assert (row.exit_code, row.status, row.pid) == (3, "finished", None)


async def test_timeout(env):
    env.venv(sleep=2.0)
    env.timeout_minutes = 1
    started = time.monotonic()
    result = await env.run(runner=env.runner(timeout_unit_seconds=0.05))
    assert time.monotonic() - started < 3
    assert result.outcome is Outcome.TIMED_OUT
    assert env.history.get(result.run_id).pid is None


async def test_stop_from_another_task(env):
    env.venv(sleep=2.0)
    runner = env.runner()
    task = asyncio.create_task(env.run(runner=runner))
    pgid = await wait_for_pid(env)
    assert runner.stop(1) is True
    result = await asyncio.wait_for(task, 3)
    assert result.outcome is Outcome.STOPPED
    assert env.history.get(1).stop_requested is True
    assert_group_gone(pgid)


async def test_stop_escalates_to_sigkill(env):
    bin_dir = env.paths.venvs / "stubborn" / "bin"
    bin_dir.mkdir(parents=True)
    script = bin_dir / "privacyfence-sync"
    script.write_text(
        f"#!{sys.executable}\nimport signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True:\n    time.sleep(0.1)\n"
    )
    script.chmod(0o755)
    (bin_dir.parent / ".cockpit-ready").write_text("")
    env.db.set_setting("venv.current", "stubborn")
    runner = env.runner()
    task = asyncio.create_task(env.run(runner=runner))
    pgid = await wait_for_pid(env)
    await asyncio.sleep(0.2)
    assert runner.stop(1) is True
    result = await asyncio.wait_for(task, 4)
    assert result.outcome is Outcome.STOPPED
    assert_group_gone(pgid)


async def test_stop_without_a_running_process(env):
    env.venv()
    runner = env.runner()
    assert runner.stop(404) is False
    result = await env.run(runner=runner)
    assert runner.stop(result.run_id) is False


async def test_cancellation_stops_the_run_and_reraises(env):
    env.venv(sleep=2.0)
    task = asyncio.create_task(env.run())
    pgid = await wait_for_pid(env)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = env.history.get(1)
    assert (row.status, row.outcome, row.note) == ("finished", Outcome.STOPPED, SHUTDOWN_NOTE)
    assert row.pid is None
    assert_group_gone(pgid)
    log = next(env.paths.logs.rglob("*.log")).read_text().splitlines()
    assert log[-3:] == ["# exit code: none", "# outcome: stopped", "# duration: 0.0 s"]


async def test_cancellation_before_the_process_exists(env):
    env.venv()
    task = asyncio.create_task(env.run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert env.history.get(1).outcome is Outcome.STOPPED


async def test_no_venv_fails_without_a_subprocess(env):
    lines = []
    result = await env.run(on_line=lines.append)
    assert result.outcome is Outcome.FAILED
    assert result.exit_code is None
    assert not env.record.exists()
    expected = "# the recipe environment is not ready: no environment has been built"
    assert lines == [expected]
    assert expected in log_lines(result)
    env.db.set_setting("venv.last_error", "pip install failed with exit code 1; see logs/venv/x.log")
    result = await env.run()
    assert "# the recipe environment is not ready: pip install failed with exit code 1; see logs/venv/x.log" in (
        log_lines(result)
    )


async def test_spawn_failure_is_failed(env):
    venv = env.venv()
    (venv / "bin" / "privacyfence-sync").unlink()
    result = await env.run()
    assert result.outcome is Outcome.FAILED
    assert any(line.startswith("# could not start privacyfence-sync: ") for line in log_lines(result))
    assert env.history.get(result.run_id).outcome is Outcome.FAILED


async def test_header_footer_log_path_and_lines(env):
    env.venv(lines=["first", "ünï"], exit_code=0, outcome="no_change")
    env.store.set("token", "imports", TOKEN)
    seen = []
    result = await env.run(on_line=seen.append)
    assert seen == ["first", "ünï"]
    lines = log_lines(result)
    argv = env.records()[0]["argv"]
    assert lines[:7] == [
        "# PrivacyFence Cockpit run 1",
        "# recipe: imports/10-sf",
        "# mode: unattended   trigger: manual",
        f"# command: privacyfence-sync run workflows/10-imports/10-sf/recipe.yaml --result-file {argv[3]}",
        "# token: group:imports",
        f"# commit: {env.sha}",
        "# started: 2026-10-20T06:15:07Z",
    ]
    assert lines[7:] == ["first", "ünï", "# exit code: 0", "# outcome: no change", "# duration: 0.0 s"]
    assert result.log_path == env.paths.logs / "2026-10-20" / "061507_imports_10-sf_1.log"
    row = env.history.get(1)
    assert row.log_path == "logs/2026-10-20/061507_imports_10-sf_1.log"
    assert (row.commit_sha, row.token_source, row.job_id, row.trigger) == (env.sha, "group:imports", "job1", "manual")
    assert TOKEN not in result.log_path.read_text()


async def test_header_without_commit_or_token(env):
    env.venv(lines=["ok"])
    shutil.rmtree(env.repo / ".git")
    result = await env.run(mode=Mode.CHECK)
    lines = log_lines(result)
    assert "# commit: unknown" in lines
    assert "# token: none" in lines
    assert "# command: privacyfence-sync check workflows/10-imports/10-sf/recipe.yaml" in lines


async def test_environment_token_is_reported_as_env_and_never_logged(env):
    env.venv(lines=["working"])
    env.env_token = TOKEN
    result = await env.run()
    text = result.log_path.read_text()
    assert "# token: env" in text
    assert TOKEN not in text
    assert env.records()[0]["env"]["PRIVACYFENCE_TOKEN"] == TOKEN


async def test_a_line_over_the_stream_limit_is_logged_whole(env):
    env.venv(lines=["before", "x" * 70_000, "after"])
    seen = []
    result = await env.run(on_line=seen.append)
    assert result.outcome is Outcome.OK
    assert "x" * 70_000 in log_lines(result)
    assert [seen[-3], seen[-1]] == ["before", "after"]
    assert env.history.get(result.run_id).pid is None


async def test_a_line_over_the_cap_is_truncated_and_the_rest_consumed(env, monkeypatch):
    monkeypatch.setattr(runner_module, "_MAX_LINE_BYTES", 1000)
    env.venv(lines=["y" * 200_000, "tail"])
    seen = []
    result = await env.run(on_line=seen.append)
    assert result.outcome is Outcome.OK
    assert seen[-2:] == ["y" * 1000 + LINE_TRUNCATED, "tail"]
    assert max(len(line) for line in log_lines(result)) < 2000


async def test_an_invalid_replay_recipe_fails_the_row_without_a_process(env):
    env.venv()
    write_recipe(env, "- just\n- a list\n")
    result = await env.run(mode=Mode.REPLAY)
    row = env.history.get(result.run_id)
    assert result.outcome is Outcome.FAILED
    assert (row.status, row.outcome, row.pid) == ("finished", Outcome.FAILED, None)
    assert row.note == "unexpected error: AttributeError"
    assert not env.record.exists()
    assert log_lines(result)[-2] == "# outcome: failed"
    assert not (env.recipe.dir / ".replay.yaml").exists()


async def test_an_unexpected_error_after_start_stops_the_group(env, monkeypatch):
    env.venv(sleep=5.0, lines=["hello"])
    runner = env.runner()

    pgids = []

    async def boom(proc, log, on_line):
        pgids.append(proc.pid)
        raise RuntimeError(TOKEN)

    monkeypatch.setattr(runner, "_pump", boom)
    result = await asyncio.wait_for(env.run(runner=runner), 5)
    row = env.history.get(result.run_id)
    assert result.outcome is Outcome.FAILED
    assert (row.status, row.outcome, row.pid) == ("finished", Outcome.FAILED, None)
    assert row.note == "unexpected error: RuntimeError"
    text = result.log_path.read_text()
    assert TOKEN not in text
    assert log_lines(result)[-3:][0] == "# exit code: none"
    assert_group_gone(pgids[0])


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, _size):
        return self._chunks.pop(0) if self._chunks else b""


class _FakeProc:
    def __init__(self, chunks):
        self.stdout = _FakeStream(chunks)

    async def wait(self):
        return 0


async def pumped(env, chunks):
    lines = []
    await env.runner()._pump(_FakeProc(chunks), io.StringIO(), lines.append)
    return lines


async def test_pump_multibyte_character_split_across_chunks(env):
    data = "é\n".encode()
    assert await pumped(env, [data[:1], data[1:]]) == ["é"]


async def test_pump_crlf_split_across_chunks(env):
    assert await pumped(env, [b"ab\r", b"\ncd\r\n"]) == ["ab", "cd"]


async def test_pump_final_line_without_newline(env):
    assert await pumped(env, [b"x\ny"]) == ["x", "y"]


async def test_pump_does_not_copy_the_rest_of_the_chunk_per_line(env):
    class Counting(bytes):
        sliced = 0

        def __getitem__(self, key):
            part = super().__getitem__(key)
            if isinstance(key, slice):
                type(self).sliced += len(part)
                return Counting(part)
            return part

    chunk = Counting(b"\n" * 20_000)
    assert await pumped(env, [chunk]) == [""] * 20_000
    assert Counting.sliced <= 2 * len(chunk)


async def test_a_failure_before_the_process_still_finishes_the_row_and_log(env, monkeypatch):
    env.venv()
    runner = env.runner()

    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(runner._venvs, "current", boom)
    result = await env.run(runner=runner)
    row = env.history.get(result.run_id)
    assert result.outcome is Outcome.FAILED
    assert (row.status, row.outcome, row.pid) == ("finished", Outcome.FAILED, None)
    assert row.note == "unexpected error: OSError"
    assert log_lines(result)[-2] == "# outcome: failed"


async def test_an_unwritable_log_directory_still_finishes_the_row(env):
    env.venv()
    shutil.rmtree(env.paths.logs)
    env.paths.logs.write_text("not a directory")
    result = await env.run()
    row = env.history.get(result.run_id)
    assert result.outcome is Outcome.FAILED
    assert (row.status, row.outcome) == ("finished", Outcome.FAILED)
    assert row.note == "unexpected error: NotADirectoryError"


async def test_a_failure_after_the_row_is_finished_keeps_the_real_outcome(env, monkeypatch):
    env.venv()
    runner = env.runner()
    real = env.history.set_pid

    def flaky(run_id, pid):
        if pid is None:
            raise OSError("db gone")
        real(run_id, pid)

    monkeypatch.setattr(env.history, "set_pid", flaky)
    result = await env.run(runner=runner)
    row = env.history.get(result.run_id)
    assert result.outcome is Outcome.OK
    assert (row.status, row.outcome, row.note) == ("finished", Outcome.OK, None)
    assert not any(line.startswith("# outcome:") for line in log_lines(result))


async def test_a_footer_failure_does_not_finish_the_row_twice(env, monkeypatch):
    env.venv()
    runner = env.runner()

    def boom(*args):
        raise OSError("disk full")

    monkeypatch.setattr(runner, "_write_footer", boom)
    result = await env.run(runner=runner)
    row = env.history.get(result.run_id)
    assert result.outcome is Outcome.OK
    assert (row.outcome, row.note) == (Outcome.OK, None)
    assert "# the run failed unexpectedly" not in result.log_path.read_text()


async def test_cancellation_while_stopping_after_an_error_still_finishes_the_row(env, monkeypatch):
    env.venv(sleep=5.0)
    runner = env.runner()
    real = runner._terminate_group
    started, release = threading.Event(), threading.Event()
    pgids = []

    async def boom(proc, log, on_line):
        pgids.append(proc.pid)
        raise RuntimeError("x")

    def slow(pgid, reap=False):
        if not reap:
            started.set()
            release.wait(5)
        real(pgid, reap)

    monkeypatch.setattr(runner, "_pump", boom)
    monkeypatch.setattr(runner, "_terminate_group", slow)
    task = asyncio.create_task(env.run(runner=runner))
    try:
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    row = env.history.get(1)
    assert (row.status, row.outcome, row.note, row.pid) == ("finished", Outcome.STOPPED, SHUTDOWN_NOTE, None)
    assert_group_gone(pgids[0])
    assert next(env.paths.logs.rglob("*.log")).read_text().splitlines()[-2] == "# outcome: stopped"
