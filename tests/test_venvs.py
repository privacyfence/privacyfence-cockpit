import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.events import EventKind, ListSink
from privacyfence_cockpit.models import utc_now
from privacyfence_cockpit.paths import DataPaths
from privacyfence_cockpit.venvs import (
    GATE_MESSAGE,
    PIN_MESSAGE,
    VenvError,
    VenvManager,
    VenvResult,
    pin_problem,
    requirement_files,
    venv_hash,
)
from tests.fakesync import make_fake_sync

PIN = "privacyfence-sync==1.0.0\n"


class FakeRun:
    """Stands in for subprocess.run: `-m venv` lays out a fake, pip returns a chosen code, the rest really runs."""

    def __init__(self, **fake_kwargs):
        self.fake_kwargs = fake_kwargs
        self.pip_code = 0
        self.calls = []
        self.override = None

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.override is not None:
            outcome = self.override(argv, kwargs)
            if outcome is not None:
                return outcome
        if argv[1:3] == ["-m", "venv"]:
            make_fake_sync(Path(argv[3]) / "bin", **self.fake_kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:3] == ["-m", "pip"]:
            return subprocess.CompletedProcess(argv, self.pip_code, "pip noise\n", "")
        return subprocess.run(argv, **kwargs)

    def builds(self):
        return [c for c, _ in self.calls if c[1:3] == ["-m", "venv"]]


@pytest.fixture
def env(tmp_path, request):
    paths = DataPaths(tmp_path / "data").ensure()
    paths.repo.mkdir()
    (paths.repo / "requirements.txt").write_text(PIN)
    db = Database(paths.db_file)
    db.migrate()
    request.addfinalizer(db.close)
    sink = ListSink()
    run = FakeRun()
    manager = VenvManager(paths, db, sink, run=run)
    return manager, paths, db, sink, run


def set_requirements(paths, text):
    (paths.repo / "requirements.txt").write_text(text)


def test_requirement_files(tmp_path):
    repo = tmp_path / "repo"
    (repo / "lib/x").mkdir(parents=True)
    (repo / "lib/y").mkdir()
    (repo / "lib/z").mkdir()
    (repo / "lib/x/pyproject.toml").write_text("[project]\nname='x'\n")
    (repo / "lib/y/pyproject.toml").write_text("[project]\nname='y'\n")
    (repo / "requirements.txt").write_text(
        "# comment\nrequests==2  # trailing\n-e ./lib/x\n--editable lib/y\n./lib/z\n./lib/x\n"
    )
    assert requirement_files(repo) == [
        repo / "lib/x/pyproject.toml",
        repo / "lib/y/pyproject.toml",
        repo / "requirements.txt",
    ]


def test_requirement_files_errors(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(VenvError, match="has no requirements.txt"):
        requirement_files(repo)
    (repo / "requirements.txt").write_text("-e ../elsewhere\n")
    with pytest.raises(VenvError, match=r"names \.\./elsewhere, which is outside the repository"):
        requirement_files(repo)
    (repo / "requirements.txt").write_text("./../../etc\n")
    with pytest.raises(VenvError, match="outside the repository"):
        requirement_files(repo)


@pytest.mark.parametrize(
    "line",
    ["privacyfence-sync==1.2.3", "PrivacyFence_Sync == 0.9", "privacyfence.sync==1  # pinned"],
)
def test_pin_problem_accepts_a_pin(tmp_path, line):
    (tmp_path / "requirements.txt").write_text(f"requests\n{line}\n")
    assert pin_problem(tmp_path) is None


@pytest.mark.parametrize("name", ["privacyfence-sync", "privacyfence_sync", "PrivacyFence.Sync"])
def test_pin_problem_accepts_a_local_project(tmp_path, name):
    (tmp_path / "sync").mkdir()
    (tmp_path / "sync/pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
    (tmp_path / "requirements.txt").write_text("-e ./sync\n")
    assert pin_problem(tmp_path) is None


def test_pin_problem_rejects(tmp_path):
    message = "requirements.txt must pin privacyfence-sync (privacyfence-sync==X.Y.Z)"
    (tmp_path / "other").mkdir()
    (tmp_path / "other/pyproject.toml").write_text('[project]\nname = "other"\n')
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken/pyproject.toml").write_text("not = [toml")
    (tmp_path / "nameless").mkdir()
    (tmp_path / "nameless/pyproject.toml").write_text("[tool]\nx = 1\n")
    (tmp_path / "requirements.txt").write_text(
        "privacyfence-sync>=1\nprivacyfence-sync\n-e ./other\n-e ./broken\n-e ./nameless\n"
    )
    assert pin_problem(tmp_path) == message


def test_venv_hash_depends_on_inputs_only(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/pyproject.toml").write_text("[project]\nname='lib'\n")
    (tmp_path / "requirements.txt").write_text("privacyfence-sync==1\n-e ./lib\n")
    (tmp_path / "unrelated.txt").write_text("a")
    base = venv_hash(tmp_path, "3.12.1")
    assert len(base) == 64 and venv_hash(tmp_path, "3.12.1") == base
    (tmp_path / "unrelated.txt").write_text("b")
    assert venv_hash(tmp_path, "3.12.1") == base
    assert venv_hash(tmp_path, "3.13.0") != base
    (tmp_path / "lib/pyproject.toml").write_text("[project]\nname='lib'\nversion='2'\n")
    assert venv_hash(tmp_path, "3.12.1") != base
    (tmp_path / "lib/pyproject.toml").write_text("[project]\nname='lib'\n")
    assert venv_hash(tmp_path, "3.12.1") == base
    (tmp_path / "requirements.txt").write_text("privacyfence-sync==2\n-e ./lib\n")
    assert venv_hash(tmp_path, "3.12.1") != base


def test_ensure_builds_once_then_reuses(env):
    manager, paths, db, sink, run = env
    assert manager.current() is None
    first = manager.ensure()
    assert first.built and first.error is None and first.path is not None
    assert first.path.parent == paths.venvs and (first.path / ".cockpit-ready").exists()
    assert manager.current() == first.path and db.get_setting("venv.current") == first.path.name
    assert manager.sync_bin(first.path) == first.path / "bin" / "privacyfence-sync"
    again = manager.ensure()
    assert again == VenvResult(first.path, False, None)
    assert len(run.builds()) == 1 and sink.events == []
    pip = next(call for call in run.calls if call[0][1:3] == ["-m", "pip"])
    assert pip[0][-2:] == ["-r", "requirements.txt"] and pip[1]["cwd"] == paths.repo
    assert pip[1]["timeout"] == 900 and set(pip[1]["env"]) == {"PATH", "HOME", "LANG", "PIP_CACHE_DIR"}
    log = (paths.logs / "venv" / f"{first.path.name}.log").read_text()
    assert "pip noise" in log


def test_ensure_rebuilds_when_requirements_change_and_keeps_previous(env):
    manager, paths, db, _, run = env
    first = manager.ensure().path
    set_requirements(paths, "privacyfence-sync==2.0.0\n")
    second = manager.ensure().path
    assert second != first and len(run.builds()) == 2
    assert json.loads(db.get_setting("venv.previous")) == [first.name]
    assert first.exists()


@pytest.mark.parametrize("fake", [{"help_result_file": False}, {"help_permissions": False}])
def test_gate_refuses_an_old_sync_and_keeps_the_previous_venv(env, fake):
    manager, paths, db, sink, run = env
    good = manager.ensure().path
    run.fake_kwargs = fake
    set_requirements(paths, "privacyfence-sync==0.1.0\n")
    result = manager.ensure()
    assert result == VenvResult(good, False, GATE_MESSAGE)
    assert manager.current() == good and db.get_setting("venv.current") == good.name
    assert db.get_setting("venv.last_error") == GATE_MESSAGE and db.get_setting("venv.last_error_at")
    assert [d.name for d in paths.venvs.iterdir()] == [good.name]
    assert [(e.kind, e.detail) for e in sink.events] == [(EventKind.VENV_FAILED, GATE_MESSAGE)]
    again = manager.ensure()
    assert again.error == GATE_MESSAGE and len(sink.events) == 1
    set_requirements(paths, "privacyfence-sync==0.2.0\n")
    manager.ensure()
    assert len(sink.events) == 2


def test_gate_message_text():
    assert GATE_MESSAGE == (
        "privacyfence-sync in requirements.txt is too old for the cockpit: "
        "pin the first version with run --result-file and permissions"
    )


def test_pip_failure_message_has_no_pip_output(env):
    manager, paths, db, sink, run = env
    run.pip_code = 1
    result = manager.ensure()
    digest = venv_hash(paths.repo, ".".join(map(str, sys.version_info[:3])))
    expected = f"pip install failed with exit code 1; see logs/venv/{digest}.log"
    assert result == VenvResult(None, False, expected)
    assert "noise" not in expected and "noise" in (paths.logs / "venv" / f"{digest}.log").read_text()
    assert db.get_setting("venv.last_error") == expected
    assert not (paths.venvs / digest).exists() and manager.current() is None
    assert sink.events[0].detail == expected


def test_venv_creation_failure_timeout_and_oserror(env):
    manager, paths, db, sink, run = env
    run.override = lambda argv, kw: (
        subprocess.CompletedProcess(argv, 3, "", "boom") if argv[1:3] == ["-m", "venv"] else None
    )
    assert "creating the environment failed with exit code 3" in manager.ensure().error

    def hang(argv, kw):
        if argv[1:3] == ["-m", "pip"]:
            raise subprocess.TimeoutExpired(argv, 900)

    run.override = hang
    assert "pip install did not finish; see logs/venv/" in manager.ensure().error

    def missing(argv, kw):
        if argv[1:3] == ["-m", "venv"]:
            raise FileNotFoundError("no python")

    run.override = missing
    assert "creating the environment did not finish" in manager.ensure().error
    assert len(sink.events) == 1  # one hash, one notice


def test_sync_help_failures(env):
    manager, paths, db, sink, run = env
    run.override = lambda argv, kw: (
        subprocess.CompletedProcess(argv, 1, "", "") if argv[1:] == ["--help"] else None
    )
    assert manager.ensure().error == "privacyfence-sync --help failed in the new environment"

    def vanishes(argv, kw):
        if argv[1:] == ["--help"]:
            raise OSError("exec format error")

    run.override = vanishes
    assert manager.ensure().error == "privacyfence-sync --help failed in the new environment"

    def run_help_hangs(argv, kw):
        if argv[1:] == ["run", "--help"]:
            raise subprocess.TimeoutExpired(argv, 60)

    run.override = run_help_hangs
    assert manager.ensure().error == GATE_MESSAGE
    assert manager.current() is None


def test_repository_problems_are_reported_not_raised(env):
    manager, paths, db, sink, _ = env
    set_requirements(paths, "requests\n")
    pin = manager.ensure()
    assert pin == VenvResult(None, False, "requirements.txt must pin privacyfence-sync (privacyfence-sync==X.Y.Z)")
    (paths.repo / "requirements.txt").unlink()
    gone = manager.ensure()
    assert gone.error == "the recipe repository has no requirements.txt"
    assert db.get_setting("venv.last_error") == gone.error
    manager.ensure()
    assert [e.detail for e in sink.events] == [pin.error, gone.error]


def test_success_clears_the_last_error(env):
    manager, paths, db, _, run = env
    run.pip_code = 1
    assert manager.ensure().error
    run.pip_code = 0
    assert manager.ensure().built
    assert db.get_setting("venv.last_error") is None and db.get_setting("venv.last_error_at") is None


def test_reusing_a_ready_venv_clears_the_error_and_a_partial_target_is_rebuilt(env):
    manager, paths, db, _, run = env
    good = manager.ensure().path
    set_requirements(paths, "privacyfence-sync==9\n")
    run.pip_code = 1
    manager.ensure()
    assert db.get_setting("venv.last_error")
    set_requirements(paths, PIN)
    assert manager.ensure() == VenvResult(good, False, None)
    assert db.get_setting("venv.last_error") is None
    set_requirements(paths, "privacyfence-sync==8\n")
    partial = paths.venvs / venv_hash(paths.repo, ".".join(map(str, sys.version_info[:3])))
    partial.mkdir()
    (partial / "leftover").write_text("x")
    run.pip_code = 0
    assert manager.ensure().built and not (partial / "leftover").exists()


def test_pruning_keeps_current_and_two_previous(env):
    manager, paths, db, _, _ = env
    built = []
    for n in range(4):
        set_requirements(paths, f"privacyfence-sync=={n}.0\n")
        built.append(manager.ensure().path.name)
    (paths.venvs / "stray").mkdir()
    (paths.venvs / "stray-file").write_text("not a folder")
    set_requirements(paths, "privacyfence-sync==3.0\n")
    manager.ensure()
    assert sorted(d.name for d in paths.venvs.iterdir() if d.is_dir()) == sorted(built[1:])
    assert json.loads(db.get_setting("venv.previous")) == [built[2], built[1]]
    assert db.get_setting("venv.current") == built[3]
    # going back to an older one moves the old current to the front
    set_requirements(paths, "privacyfence-sync==2.0\n")
    assert manager.ensure().path.name == built[2]
    assert json.loads(db.get_setting("venv.previous")) == [built[3], built[1]]
    assert (paths.venvs / "stray-file").exists()


def test_current_ignores_unready_or_missing_folders(env):
    manager, paths, db, _, _ = env
    db.set_setting("venv.current", "nowhere")
    assert manager.current() is None
    (paths.venvs / "half").mkdir()
    db.set_setting("venv.current", "half")
    assert manager.current() is None


def test_corrupt_previous_setting_is_ignored(env):
    manager, paths, db, _, _ = env
    for bad in ("not json", '{"a": 1}', '["ok", 3]'):
        db.set_setting("venv.previous", bad)
        assert manager.ensure().error is None


def test_python_version_comes_from_the_other_interpreter(env):
    manager, paths, db, sink, run = env
    other = VenvManager(paths, db, sink, python="/opt/py/bin/python", run=run, clock=utc_now)
    run.override = lambda argv, kw: (
        subprocess.CompletedProcess(argv, 0, "3.9.1\n", "") if argv[1:2] == ["-c"] else None
    )
    other.ensure()
    assert venv_hash(paths.repo, "3.9.1") in {d.name for d in paths.venvs.iterdir()}
    run.override = lambda argv, kw: subprocess.CompletedProcess(argv, 1, "", "") if argv[1:2] == ["-c"] else None
    other.ensure()
    assert venv_hash(paths.repo, "unknown") in {d.name for d in paths.venvs.iterdir()}


def branch_repo(tmp_path, name, requirements=PIN):
    repo = tmp_path / "branches" / name
    repo.mkdir(parents=True)
    (repo / "requirements.txt").write_text(requirements)
    return repo


def test_ensure_for_reuses_the_configured_environment_when_requirements_match(env, tmp_path):
    manager, paths, _db, _sink, _run = env
    configured = manager.ensure().path
    repo = branch_repo(tmp_path, "feature%2Fx")
    result = manager.ensure_for(repo)
    assert result == VenvResult(configured, False, None)
    assert result.path == paths.venvs / venv_hash(repo, ".".join(map(str, sys.version_info[:3])))
    assert not paths.branch_venvs.exists()


def test_ensure_for_with_local_packages_builds_its_own_environment(env, tmp_path):
    manager, paths, _db, _sink, run = env
    text = PIN + "-e ./lib/x\n"
    set_requirements(paths, text)
    (paths.repo / "lib/x").mkdir(parents=True)
    (paths.repo / "lib/x/pyproject.toml").write_text("[project]\nname='x'\n")
    manager.ensure()
    repo = branch_repo(tmp_path, "feature%2Fx", text)
    (repo / "lib/x").mkdir(parents=True)
    (repo / "lib/x/pyproject.toml").write_text("[project]\nname='x'\n")
    result = manager.ensure_for(repo)
    digest = venv_hash(repo, ".".join(map(str, sys.version_info[:3])))
    assert result == VenvResult(paths.branch_venvs / f"feature%2Fx-{digest}", True, None)
    pip_calls = [kw for argv, kw in run.calls if argv[1:3] == ["-m", "pip"]]
    assert pip_calls[-1]["cwd"] == repo


def test_ensure_for_builds_in_branch_venvs_and_leaves_the_current_setting(env, tmp_path):
    manager, paths, db, _sink, _run = env
    manager.ensure()
    set_requirements(paths, PIN + "requests==2\n")
    manager.ensure()
    current, previous = db.get_setting("venv.current"), db.get_setting("venv.previous")
    repo = branch_repo(tmp_path, "feature%2Fx", PIN + "idna==3\n")
    result = manager.ensure_for(repo)
    assert result.built is True and result.error is None
    assert result.path is not None and result.path.parent == paths.branch_venvs
    assert db.get_setting("venv.current") == current
    assert db.get_setting("venv.previous") == previous
    assert (paths.venvs / current).is_dir()


def test_ensure_for_reuses_a_ready_branch_environment(env, tmp_path):
    manager, _paths, _db, _sink, run = env
    repo = branch_repo(tmp_path, "feature%2Fx", PIN + "idna==3\n")
    first = manager.ensure_for(repo)
    builds = len(run.builds())
    second = manager.ensure_for(repo)
    assert second == VenvResult(first.path, False, None)
    assert len(run.builds()) == builds


def test_ensure_for_keeps_two_branch_environments(env, tmp_path):
    _manager, paths, db, sink, run = env
    now = [utc_now()]

    def clock():
        now[0] += timedelta(minutes=1)
        return now[0]

    manager = VenvManager(paths, db, sink, run=run, clock=clock)
    built = []
    for index in range(3):
        repo = branch_repo(tmp_path, f"b{index}", PIN + f"idna=={index}\n")
        built.append(manager.ensure_for(repo).path)
    assert sorted(p.name for p in paths.branch_venvs.iterdir()) == sorted([built[1].name, built[2].name])
    (paths.branch_venvs / "stray").mkdir()
    (paths.branch_venvs / "stray" / "file").write_text("")
    manager.ensure_for(branch_repo(tmp_path, "b3", PIN + "idna==9\n"))
    assert not (paths.branch_venvs / "stray").exists()


def test_ensure_for_failure_is_returned_not_notified(env, tmp_path):
    manager, paths, db, sink, run = env
    run.pip_code = 1
    repo = branch_repo(tmp_path, "feature%2Fx", PIN + "idna==3\n")
    result = manager.ensure_for(repo)
    assert result.path is None and result.built is False
    assert result.error is not None and "pip install failed" in result.error
    assert sink.events == []
    assert db.get_setting("venv.last_error") is None
    assert list(paths.branch_venvs.iterdir()) == []
    no_requirements = tmp_path / "branches" / "empty"
    no_requirements.mkdir()
    missing = manager.ensure_for(no_requirements)
    assert missing == VenvResult(None, False, "the recipe repository has no requirements.txt")
    unpinned = branch_repo(tmp_path, "unpinned", "requests==2\n")
    assert manager.ensure_for(unpinned).error == PIN_MESSAGE
    assert sink.events == []
