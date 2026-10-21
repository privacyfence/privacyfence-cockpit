import base64
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fakesync import install_fake_venv
from gitutil import make_remote

from privacyfence_cockpit.cli import main
from privacyfence_cockpit.config import EnvConfig
from privacyfence_cockpit.events import Event
from privacyfence_cockpit.history import HistoryFilter
from privacyfence_cockpit.models import Mode
from privacyfence_cockpit.notify import Notifier
from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.venvs import VenvManager, VenvResult

TOKEN = "pfst_" + "s3cr3t" * 5
FILES = {
    ".gitignore": "work/\n",
    "workflows/10-imports/10-sf/recipe.yaml": "steps: []\n",
    "workflows/10-imports/20-jira/recipe.yaml": "steps: []\n",
    "workflows/20-exports/10-out/recipe.yaml": "steps: []\n",
}
# A fake whose outcome depends on the recipe folder: "jira" is denied, everything else is ok.
SELECTIVE = """#!{python}
import json, sys
from pathlib import Path
args = sys.argv[1:]
if args[0] == "run":
    path = Path(args[1])
    denied = path.parent.name == "20-jira"
    print("running " + path.parent.name, flush=True)
    target = Path(args[args.index("--result-file") + 1])
    target.write_text(json.dumps({{"outcome": "denied" if denied else "ok"}}))
    sys.exit(3 if denied else 0)
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    key = tmp_path / "key"
    key.write_text(base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COCKPIT_MASTER_KEY_FILE", str(key))
    monkeypatch.delenv("PRIVACYFENCE_TOKEN", raising=False)
    monkeypatch.delenv("COCKPIT_PUBLIC_URL", raising=False)
    return tmp_path


def open_cockpit() -> Cockpit:
    return Cockpit.open(EnvConfig.from_environ(dict(os.environ)))


@pytest.fixture
def cloned(env: Path) -> Path:
    remote = make_remote(env, FILES)
    cockpit = open_cockpit()
    cockpit.git.configure(str(remote), "main", "none")
    cockpit.git.clone()
    cockpit.db.close()
    return remote


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def build_venv_on_ensure(monkeypatch: pytest.MonkeyPatch, **fake: object) -> list[int]:
    """Replaces the pip build by laying out a fake venv; the returned list records each call."""
    calls: list[int] = []

    def ensure(self: VenvManager) -> VenvResult:
        calls.append(1)
        venv = install_fake_venv(self.paths, self.db, **fake)  # type: ignore[arg-type]
        return VenvResult(venv, True, None)

    monkeypatch.setattr(VenvManager, "ensure", ensure)
    return calls


def history_rows() -> list[tuple[str | None, str, str | None]]:
    cockpit = open_cockpit()
    try:
        rows = cockpit.db.query("SELECT recipe_path, trigger, outcome FROM runs ORDER BY id")
        return [(r["recipe_path"], r["trigger"], r["outcome"]) for r in rows]
    finally:
        cockpit.db.close()


def records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


# run


def test_dry_run_prints_the_recipes_and_records_nothing(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = build_venv_on_ensure(monkeypatch)
    code, out, err = run(["run", "-n", "imports"], capsys)
    assert (code, err) == (0, "")
    assert out == "would run: imports/10-sf\nwould run: imports/20-jira\n"
    assert history_rows() == []
    assert calls == []


def test_run_with_one_failing_recipe_after_an_ok_one(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cockpit = open_cockpit()
    venv = install_fake_venv(cockpit.paths, cockpit.db)
    (venv / "bin" / "privacyfence-sync").write_text(SELECTIVE.format(python=sys.executable))
    cockpit.db.close()
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(venv, False, None))
    code, out, _ = run(["run", "imports"], capsys)
    assert code == 3
    summary = [line for line in out.splitlines() if line.startswith("imports/")]
    assert summary == ["imports/10-sf: ok (exit 0)", "imports/20-jira: denied (exit 3)"]
    assert history_rows() == [("imports/10-sf", "manual", "ok"), ("imports/20-jira", "manual", "denied")]


def test_run_stops_on_error_and_repeats(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    cockpit = open_cockpit()
    record = tmp_path / "record.jsonl"
    venv = install_fake_venv(cockpit.paths, cockpit.db, record=record)
    cockpit.db.close()
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(venv, False, None))
    code, out, _ = run(["run", "--repeat", "3", "imports/10-sf"], capsys)
    assert code == 0
    assert out.count("imports/10-sf: ok (exit 0)") == 3
    # A recipe that does not succeed ends the whole job with --stop-on-error.
    (venv / "bin" / "privacyfence-sync").write_text(SELECTIVE.format(python=sys.executable))
    code, out, _ = run(["run", "--stop-on-error", "imports"], capsys)
    assert code == 3
    assert "imports/20-jira: denied (exit 3)" in out
    assert history_rows()[-1] == ("imports/20-jira", "manual", "denied")
    (venv / "bin" / "privacyfence-sync").write_text(SELECTIVE.format(python=sys.executable))
    code, out, _ = run(["run", "imports/20-jira", "exports"], capsys)
    assert code == 3
    assert "exports/10-out" in out
    code, out, _ = run(["run", "--stop-on-error", "imports/20-jira", "exports"], capsys)
    assert code == 3
    assert "exports/10-out" not in out


def test_run_attended_uses_the_attended_mode(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    record = tmp_path / "record.jsonl"
    cockpit = open_cockpit()
    venv = install_fake_venv(cockpit.paths, cockpit.db, record=record)
    cockpit.db.close()
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(venv, False, None))
    assert run(["run", "--attended", "imports/10-sf"], capsys)[0] == 0
    assert "--attended" in records(record)[0]["argv"]  # type: ignore[operator]
    cockpit = open_cockpit()
    assert cockpit.history.query(HistoryFilter())[0].mode is Mode.ATTENDED
    cockpit.db.close()


def test_unknown_selector_exits_1_without_a_run(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    build_venv_on_ensure(monkeypatch)
    for command in (["run"], ["check"], ["preflight"], ["test", "--live"]):
        code, out, err = run([*command, "nope"], capsys)
        assert (code, out) == (1, "")
        assert err == "no recipe or group matches 'nope'\n"
    assert history_rows() == []


def test_repeat_must_be_a_sensible_number(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for bad in ("0", "101", "x"):
        assert main(["run", "--repeat", bad]) == 1
    capsys.readouterr()


def test_run_failure_mapping(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cockpit = open_cockpit()
    venv = install_fake_venv(cockpit.paths, cockpit.db, outcome="not_signed_in", exit_code=4)
    cockpit.db.close()
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(venv, False, None))
    code, out, _ = run(["run", "imports/10-sf"], capsys)
    assert code == 4
    assert out.splitlines()[-1] == "imports/10-sf: not signed in (exit 4)"
    cockpit = open_cockpit()
    install_fake_venv(cockpit.paths, cockpit.db, outcome="recipe_error", exit_code=1)
    cockpit.db.close()
    assert run(["run", "imports/10-sf"], capsys)[0] == 1
    cockpit = open_cockpit()
    install_fake_venv(cockpit.paths, cockpit.db, outcome=None, exit_code=0)
    cockpit.db.close()
    code, out, _ = run(["run", "imports/10-sf"], capsys)
    assert code == 2
    assert out.splitlines()[-1] == "imports/10-sf: failed (exit 0)"


def test_run_without_a_recipe_environment_fails(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(None, False, "no pin"))
    code, out, _ = run(["run", "imports/10-sf"], capsys)
    assert code == 2
    assert "# the recipe environment is not ready: no environment has been built" in out
    assert out.splitlines()[-1] == "imports/10-sf: failed (no exit code)"


def test_run_with_no_recipes(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["run"], capsys) == (0, "", "")


# check and preflight


def test_check_builds_the_venv_first_and_prints_the_summary(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cockpit = open_cockpit()
    assert cockpit.db.get_setting("venv.current") is None
    cockpit.db.close()
    calls = build_venv_on_ensure(monkeypatch, lines=["checked"])
    code, out, err = run(["check", "imports"], capsys)
    assert (code, err) == (0, "")
    assert calls == [1]
    assert "checked" in out
    assert out.splitlines()[-1] == "check: 2 recipe(s), 0 failed"
    cockpit = open_cockpit()
    assert cockpit.db.get_setting("venv.current") == "fake"
    cockpit.db.close()
    assert [row[2] for row in history_rows()] == ["ok", "ok"]


def test_check_counts_failures(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    build_venv_on_ensure(monkeypatch, exit_code=1)
    code, out, _ = run(["check"], capsys)
    assert code == 1
    assert out.splitlines()[-1] == "check: 3 recipe(s), 3 failed"


def test_preflight_streams_lines_and_passes_attended(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    record = tmp_path / "record.jsonl"
    build_venv_on_ensure(monkeypatch, lines=["step one", "step two"], record=record)
    code, out, _ = run(["preflight", "imports/10-sf"], capsys)
    assert code == 0
    assert "step one\nstep two\n" in out
    assert "--attended" not in records(record)[-1]["argv"]  # type: ignore[operator]
    code, out, _ = run(["preflight", "--attended", "imports/10-sf"], capsys)
    assert code == 0
    last = records(record)[-1]["argv"]
    assert last[0] == "preflight" and "--attended" in last  # type: ignore[index, operator]
    assert out.splitlines()[-1] == "imports/10-sf: ok (exit 0)"


def test_preflight_denied_exits_3(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    build_venv_on_ensure(monkeypatch, exit_code=3)
    assert run(["preflight", "imports/10-sf"], capsys)[0] == 3


# test


def fake_python(venv: Path, *, with_pytest: bool, record: Path) -> None:
    python = venv / "bin" / "python"
    python.unlink(missing_ok=True)
    if with_pytest:
        python.symlink_to(sys.executable)
    else:
        python.write_text(
            f"#!{sys.executable}\nimport os, sys\n"
            f"open({str(record)!r}, 'a').write(repr(sorted(os.environ)) + '\\n')\n"
            "sys.stderr.write('No module named pytest\\n')\nsys.exit(1)\n"
        )
        python.chmod(0o755)


def test_test_runs_the_repository_tests(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "")
    cockpit = open_cockpit()
    venv = install_fake_venv(cockpit.paths, cockpit.db)
    cockpit.db.close()
    fake_python(venv, with_pytest=True, record=cloned.parent / "r")
    (cloned.parent / "data" / "repo" / "test_ok.py").write_text(
        "import os\n\ndef test_env():\n    assert 'PRIVACYFENCE_TOKEN' not in os.environ\n"
        "    assert 'COCKPIT_MASTER_KEY_FILE' not in os.environ\n\ndef test_k():\n    pass\n"
    )
    monkeypatch.setenv("PRIVACYFENCE_TOKEN", TOKEN)
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(venv, False, None))
    code, _, err = run(["test", "--", "-k", "test_env", "-p", "no:cacheprovider"], capsys)
    assert (code, err) == (0, "")
    code, _, _ = run(["test"], capsys)
    assert code == 0


def test_test_failure_exits_1(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cockpit = open_cockpit()
    venv = install_fake_venv(cockpit.paths, cockpit.db)
    cockpit.db.close()
    fake_python(venv, with_pytest=True, record=cloned.parent / "r")
    (cloned.parent / "data" / "repo" / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(venv, False, None))
    assert run(["test", "--", "-p", "no:cacheprovider"], capsys)[0] == 1


def test_test_without_pytest_in_the_venv(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cockpit = open_cockpit()
    venv = install_fake_venv(cockpit.paths, cockpit.db)
    cockpit.db.close()
    fake_python(venv, with_pytest=False, record=cloned.parent / "r")
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(venv, False, None))
    code, out, err = run(["test"], capsys)
    assert (code, out) == (1, "")
    assert err == "Add pytest to requirements.txt to use cockpit test.\n"
    cockpit = open_cockpit()
    assert cockpit.lock.try_acquire()
    cockpit.lock.release()
    cockpit.db.close()


def test_test_without_a_recipe_environment(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(None, False, None))
    assert run(["test"], capsys) == (2, "", "The recipe environment is not ready.\n")
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(None, False, "no pin"))
    assert run(["test"], capsys) == (2, "", "no pin\n")


def test_test_live_replays_the_recipes(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    build_venv_on_ensure(monkeypatch)
    cockpit = open_cockpit()
    repo = cockpit.paths.repo
    cockpit.db.close()
    (repo / "workflows/10-imports/10-sf/recipe.yaml").write_text(
        "steps:\n  - {id: a, tool: drive_list_files}\n  - {id: b, tool: drive_create_file}\n"
    )
    code, out, _ = run(["test", "--live", "imports/10-sf"], capsys)
    assert code == 0
    assert out.splitlines()[-1] == "imports/10-sf: ok (exit 0)"
    cockpit = open_cockpit()
    rows = cockpit.history.query(HistoryFilter())
    assert [row.mode for row in rows] == [Mode.REPLAY]
    assert rows[0].trigger == "manual"
    cockpit.db.close()


# the run lock, events and interruption


HOLDER = """
import fcntl, sys, time
handle = open(sys.argv[1], "a+")
fcntl.flock(handle, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(0.6)
"""


def test_run_waits_for_the_run_lock(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    build_venv_on_ensure(monkeypatch)
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(cloned.parent / "data" / "run.lock")], stdout=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    try:
        code, out, err = run(["run", "imports/10-sf"], capsys)
    finally:
        proc.wait(timeout=5)
        proc.stdout.close()
    assert code == 0
    assert err == "Waiting for the cockpit's current run to finish…\n"
    assert out.splitlines()[-1] == "imports/10-sf: ok (exit 0)"


def test_test_waits_for_the_run_lock(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(None, False, "x"))
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(cloned.parent / "data" / "run.lock")], stdout=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    try:
        code, _, err = run(["test"], capsys)
    finally:
        proc.wait(timeout=5)
        proc.stdout.close()
    assert code == 2
    assert err.startswith("Waiting for the cockpit's current run to finish…\n")


def test_events_are_delivered_after_a_run(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[Event] = []
    delivered: list[int] = []
    monkeypatch.setattr(Notifier, "emit", lambda self, event: seen.append(event))

    async def deliver(self: Notifier) -> int:
        delivered.append(len(seen))
        return 0

    monkeypatch.setattr(Notifier, "deliver_pending", deliver)

    def failing(self: VenvManager) -> VenvResult:
        from privacyfence_cockpit.events import EventKind
        from privacyfence_cockpit.models import utc_now

        self.events.emit(Event(EventKind.VENV_FAILED, utc_now()))
        return VenvResult(None, False, "x")

    monkeypatch.setattr(VenvManager, "ensure", failing)
    assert run(["run", "imports/10-sf"], capsys)[0] == 2
    assert len(seen) == 1
    assert delivered == [1]


def test_a_venv_error_is_one_line(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from privacyfence_cockpit.venvs import VenvError

    def broken(self: VenvManager) -> VenvResult:
        raise VenvError("the recipe repository has no requirements.txt")

    monkeypatch.setattr(VenvManager, "ensure", broken)
    code, _, err = run(["check"], capsys)
    assert code == 2
    assert err == "the recipe repository has no requirements.txt\n"


def test_ctrl_c_stops_the_run_and_still_delivers_notifications(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cockpit = open_cockpit()
    venv = install_fake_venv(cockpit.paths, cockpit.db, sleep=5.0, lines=["started"])
    cockpit.db.close()
    monkeypatch.setattr(VenvManager, "ensure", lambda self: VenvResult(venv, False, None))
    delivered: list[int] = []

    async def deliver(self: Notifier) -> int:
        delivered.append(1)
        return 0

    monkeypatch.setattr(Notifier, "deliver_pending", deliver)
    timer = threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        code, out, err = run(["run", "imports/10-sf"], capsys)
    finally:
        timer.cancel()
    assert code == 130
    assert err.endswith("Interrupted.\n")
    assert delivered == [1]
    assert history_rows() == [("imports/10-sf", "manual", "stopped")]
    cockpit = open_cockpit()
    assert cockpit.lock.try_acquire()
    cockpit.lock.release()
    cockpit.db.close()


def test_no_command_prints_the_token(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cockpit = open_cockpit()
    cockpit.store.set("token", "default", TOKEN)
    cockpit.db.close()
    build_venv_on_ensure(monkeypatch)
    for command in (["run"], ["check"], ["preflight"], ["test", "--live"]):
        _, out, err = run(command, capsys)
        assert TOKEN not in out + err
