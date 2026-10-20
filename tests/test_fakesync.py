import json
import os
import subprocess

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.paths import DataPaths
from tests.fakesync import install_fake_venv, make_fake_sync


def call(script, *args, cwd=None):
    return subprocess.run([str(script), *args], capture_output=True, text=True, cwd=cwd)


def test_help_texts_and_script_mode(tmp_path):
    script = make_fake_sync(tmp_path / "bin")
    assert script == tmp_path / "bin" / "privacyfence-sync"
    assert os.access(script, os.X_OK) and script.read_text().startswith("#!")
    assert call(script, "--help").returncode == 0
    assert "--result-file" in call(script, "run", "--help").stdout
    permissions = call(script, "permissions", "--help")
    assert permissions.returncode == 0 and "--json" in permissions.stdout


def test_help_switches_off(tmp_path):
    script = make_fake_sync(tmp_path / "bin", help_result_file=False, help_permissions=False)
    assert "--result-file" not in call(script, "run", "--help").stdout
    denied = call(script, "permissions", "--help")
    assert denied.returncode == 2 and "invalid choice: 'permissions'" in denied.stderr
    assert call(script, "permissions", "r.yaml", "--json").returncode == 2


def test_run_writes_result_file_and_exit_code(tmp_path):
    script = make_fake_sync(tmp_path / "bin", exit_code=3, outcome="denied", lines=["one", "two"])
    result = tmp_path / "result.json"
    done = call(script, "run", "recipe.yaml", "--result-file", str(result))
    assert done.returncode == 3 and done.stdout.splitlines() == ["one", "two"]
    assert json.loads(result.read_text()) == {
        "version": 1,
        "recipe": "fixture",
        "outcome": "denied",
        "exit_code": 3,
        "stopped_at_step": "build",
        "started_at": "2026-10-20T06:15:00Z",
        "finished_at": "2026-10-20T06:15:42Z",
        "privacyfence_sync_version": "0.0.0+fake",
    }


def test_run_without_outcome_or_result_option_writes_nothing(tmp_path):
    result = tmp_path / "result.json"
    silent = make_fake_sync(tmp_path / "a", outcome=None)
    assert call(silent, "run", "r.yaml", "--result-file", str(result)).returncode == 0
    assert not result.exists()
    plain = make_fake_sync(tmp_path / "b")
    assert call(plain, "run", "r.yaml").returncode == 0
    assert not result.exists()


def test_check_preflight_and_permissions(tmp_path):
    report = {"version": 1, "steps": []}
    script = make_fake_sync(tmp_path / "bin", exit_code=4, lines=["hello"], permissions_json=report)
    for command in ("check", "preflight"):
        done = call(script, command, "r.yaml")
        assert (done.returncode, done.stdout) == (4, "hello\n")
    done = call(script, "permissions", "r.yaml", "--json")
    assert done.returncode == 4 and json.loads(done.stdout) == {"version": 1, "steps": []}
    quiet = make_fake_sync(tmp_path / "quiet")
    assert call(quiet, "permissions", "r.yaml", "--json").stdout == ""


def test_unknown_command(tmp_path):
    script = make_fake_sync(tmp_path / "bin")
    done = call(script, "frobnicate")
    assert done.returncode == 2 and "invalid choice" in done.stderr
    assert call(script).returncode == 0


def test_sleep_delays_the_run(tmp_path):
    script = make_fake_sync(tmp_path / "bin", sleep=0.2)
    assert call(script, "run", "r.yaml").returncode == 0


def test_record_appends_argv_env_and_cwd(tmp_path):
    record = tmp_path / "calls.jsonl"
    script = make_fake_sync(tmp_path / "bin", record=record)
    work = tmp_path / "work"
    work.mkdir()
    call(script, "check", "r.yaml", cwd=work)
    call(script, "preflight", "r.yaml", "--attended", cwd=work)
    first, second = [json.loads(line) for line in record.read_text().splitlines()]
    assert first["argv"] == ["check", "r.yaml"] and second["argv"] == ["preflight", "r.yaml", "--attended"]
    assert first["cwd"] == str(work.resolve()) and "PATH" in first["env"]


def test_install_fake_venv(tmp_path):
    paths = DataPaths(tmp_path / "data").ensure()
    db = Database(paths.db_file)
    db.migrate()
    venv = install_fake_venv(paths, db, exit_code=2)
    assert venv == paths.venvs / "fake"
    assert (venv / ".cockpit-ready").exists() and db.get_setting("venv.current") == "fake"
    assert call(venv / "bin" / "privacyfence-sync", "check", "r.yaml").returncode == 2
    db.close()
