import asyncio
import base64
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from privacyfence_cockpit.crypto import MasterKeyError, SecretDecryptError, missing_key_message
from privacyfence_cockpit.gitrepo import GitError
from privacyfence_cockpit.jobs import Job, JobKind
from privacyfence_cockpit.models import iso
from privacyfence_cockpit.services import Cockpit
from tests.cockpit_factory import make_cockpit
from tests.gitutil import make_remote

NOW = datetime(2026, 10, 20, 6, 15, 7, tzinfo=UTC)
RECIPE = "name: fixture\n"


def write_repo(cockpit: Cockpit, files: dict[str, str]) -> None:
    for name, text in files.items():
        target = cockpit.paths.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


async def until(predicate, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


def quiet(cockpit: Cockpit) -> None:
    """Keep the loops that a test does not exercise out of the way."""
    cockpit.fetch_poll_seconds = 3600
    cockpit.token_start_delay_seconds = 3600
    cockpit.retention_poll_seconds = 3600


def test_open_creates_layout_canary_and_schema(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    paths = cockpit.paths
    assert paths.root == tmp_path / "data"
    for folder in (paths.venvs, paths.logs, paths.secrets, paths.home, paths.home / ".ssh"):
        assert folder.is_dir()
    assert (paths.secrets / "canary.bin").is_file()
    assert (paths.home / ".ssh" / "known_hosts_github").is_file()
    assert cockpit.db.query_one("PRAGMA user_version")[0] == 2  # type: ignore[index]
    assert cockpit.notifier is not None


def test_open_wires_the_notifier_as_event_sink(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    assert cockpit.venvs.events is cockpit.notifier
    assert cockpit.git._events is cockpit.notifier
    assert cockpit.scheduler._events is cockpit.notifier
    assert cockpit.worker._events is cockpit.notifier
    assert cockpit.token_checker._events is cockpit.notifier


def test_open_without_key_file_raises(tmp_path: Path) -> None:
    key = tmp_path / "missing"
    with pytest.raises(MasterKeyError) as caught:
        make_cockpit(tmp_path, environ={"COCKPIT_MASTER_KEY_FILE": str(key)})
    assert str(caught.value) == missing_key_message(key)


def test_open_with_another_key_raises_canary_error(tmp_path: Path) -> None:
    make_cockpit(tmp_path)
    (tmp_path / "cockpit_master_key").write_text(base64.b64encode(os.urandom(32)).decode())
    with pytest.raises(MasterKeyError, match="does not match"):
        make_cockpit(tmp_path)


def test_reopen_with_the_same_key_works(tmp_path: Path) -> None:
    first = make_cockpit(tmp_path)
    first.store.set("token", "default", "pfst_" + "a" * 30)
    second = make_cockpit(tmp_path)
    assert second.store.get("token", "default") == "pfst_" + "a" * 30


def test_env_token_reaches_the_resolver(tmp_path: Path) -> None:
    environ = {"PRIVACYFENCE_TOKEN": "pfst_" + "e" * 30}
    cockpit = make_cockpit(tmp_path, environ=environ)
    assert cockpit.tokens.resolve_name("x") is None
    assert cockpit.env.env_token == "pfst_" + "e" * 30


def test_recipes_empty_before_clone_and_sorted_after(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    assert cockpit.recipes() == []
    write_repo(
        cockpit,
        {
            "workflows/20-exports/10-b/recipe.yaml": RECIPE,
            "workflows/10-imports/10-a/recipe.yaml": RECIPE,
        },
    )
    assert [r.path for r in cockpit.recipes()] == ["imports/10-a", "exports/10-b"]


def test_recipes_survive_a_discovery_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cockpit = make_cockpit(tmp_path)
    write_repo(
        cockpit,
        {
            "workflows/10-imports/10-a/recipe.yaml": RECIPE,
            "workflows/40-imports/10-b/recipe.yaml": RECIPE,
        },
    )
    with caplog.at_level(logging.WARNING):
        assert cockpit.recipes() == []
    assert "both have the group name imports" in caplog.text


def test_config_or_default_with_a_broken_cockpit_yaml(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    config, error = cockpit.config_or_default()
    assert config.schedules == () and error is None
    write_repo(cockpit, {"cockpit.yaml": "version: 2\n"})
    config, error = cockpit.config_or_default()
    assert config.schedules == ()
    assert error is not None and error.startswith("cockpit.yaml: ")
    with pytest.raises(Exception, match="cockpit.yaml"):
        cockpit.config()
    # A broken file never takes the effective settings down with it.
    assert cockpit.effective().timezone == "UTC"


def test_effective_merges_yaml_settings_and_environment(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path, environ={"COCKPIT_SERVER_URL": "http://env.example/mcp"})
    assert cockpit.effective().server_url == "http://env.example/mcp"
    assert cockpit.effective().server_source == "COCKPIT_SERVER_URL"
    cockpit.db.set_setting("server_url", "http://setting.example/mcp")
    cockpit.db.set_setting("timezone", "Europe/Budapest")
    assert cockpit.effective().server_source == "settings"
    assert cockpit.effective().timezone == "Europe/Budapest"
    write_repo(cockpit, {"cockpit.yaml": "version: 1\nserver: http://yaml.example/mcp\ntimeout_minutes: 5\n"})
    eff = cockpit.effective()
    assert (eff.server_url, eff.server_source, eff.timeout_minutes) == ("http://yaml.example/mcp", "cockpit.yaml", 5)


async def test_start_then_stop_leaves_no_task_running(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    quiet(cockpit)
    before = asyncio.all_tasks()
    await cockpit.start()
    started = asyncio.all_tasks() - before
    assert {t.get_name() for t in started} == {
        f"cockpit-{n}" for n in ("worker", "scheduler", "notifier", "fetch", "tokens", "retention")
    }
    await cockpit.start()  # a second start changes nothing
    assert asyncio.all_tasks() - before == started
    await cockpit.stop()
    assert all(t.done() for t in started)
    assert asyncio.all_tasks() == before
    await cockpit.stop()  # and stopping twice is harmless


async def test_fetch_loop_fetches_when_cloned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cockpit = make_cockpit(tmp_path)
    quiet(cockpit)
    cockpit.fetch_poll_seconds = 0.01
    remote = make_remote(tmp_path, {"README.md": "x\n"})
    cockpit.git.configure(str(remote), "main", "none")
    cockpit.git.clone()
    calls: list[int] = []
    monkeypatch.setattr(cockpit.git, "fetch", lambda: calls.append(1))
    await cockpit.start()
    try:
        await until(lambda: len(calls) >= 2)
    finally:
        await cockpit.stop()


async def test_fetch_loop_skips_an_unconfigured_or_uncloned_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cockpit = make_cockpit(tmp_path)
    quiet(cockpit)
    cockpit.fetch_poll_seconds = 0.01
    calls: list[int] = []
    monkeypatch.setattr(cockpit.git, "fetch", lambda: calls.append(1))
    statuses: list[int] = []
    real_status = cockpit.git.status
    monkeypatch.setattr(cockpit.git, "status", lambda: (statuses.append(1), real_status())[1])
    await cockpit.start()
    try:
        await until(lambda: len(statuses) >= 3)
        cockpit.git.configure("/nowhere", "main", "none")  # configured, not cloned
        n = len(statuses)
        await until(lambda: len(statuses) >= n + 3)
    finally:
        await cockpit.stop()
    assert calls == []


async def test_fetch_loop_keeps_going_after_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cockpit = make_cockpit(tmp_path)
    quiet(cockpit)
    cockpit.fetch_poll_seconds = 0.01
    remote = make_remote(tmp_path, {"README.md": "x\n"})
    cockpit.git.configure(str(remote), "main", "none")
    cockpit.git.clone()
    calls: list[int] = []

    def failing() -> None:
        calls.append(1)
        raise GitError("git fetch failed: offline")

    monkeypatch.setattr(cockpit.git, "fetch", failing)
    with caplog.at_level(logging.WARNING):
        await cockpit.start()
        try:
            await until(lambda: len(calls) >= 3)
        finally:
            await cockpit.stop()
    assert "git fetch failed: offline" in caplog.text


async def test_fetch_loop_uses_the_effective_interval_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cockpit = make_cockpit(tmp_path)
    quiet(cockpit)
    cockpit.fetch_poll_seconds = None
    cockpit.db.set_setting("git.fetch_interval_minutes", "7")
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0.01)

    monkeypatch.setattr("privacyfence_cockpit.services.asyncio.sleep", fake_sleep)
    await cockpit.start()
    try:
        await until(lambda: 420 in sleeps)
    finally:
        await cockpit.stop()


async def run_token_loop(cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch, polls: int = 2) -> list[int]:
    quiet(cockpit)
    cockpit.token_start_delay_seconds = 0
    cockpit.token_poll_seconds = 0.01
    calls: list[int] = []

    async def fake_check_all() -> dict[str, object]:
        calls.append(1)
        return {}

    monkeypatch.setattr(cockpit.token_checker, "check_all", fake_check_all)
    await cockpit.start()
    try:
        await asyncio.sleep(0.2)
    finally:
        await cockpit.stop()
    return calls


async def test_token_loop_checks_when_the_last_check_is_25_hours_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cockpit = make_cockpit(tmp_path, clock=lambda: NOW)
    cockpit.db.set_setting("tokens.last_check_at", iso(NOW - timedelta(hours=25)))
    assert await run_token_loop(cockpit, monkeypatch) != []


async def test_token_loop_checks_when_never_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cockpit = make_cockpit(tmp_path, clock=lambda: NOW)
    assert await run_token_loop(cockpit, monkeypatch) != []


async def test_token_loop_skips_when_the_last_check_is_one_hour_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cockpit = make_cockpit(tmp_path, clock=lambda: NOW)
    cockpit.db.set_setting("tokens.last_check_at", iso(NOW - timedelta(hours=1)))
    assert await run_token_loop(cockpit, monkeypatch) == []


async def test_token_loop_treats_an_unreadable_timestamp_as_due(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cockpit = make_cockpit(tmp_path, clock=lambda: NOW)
    cockpit.db.set_setting("tokens.last_check_at", "garbage")
    assert await run_token_loop(cockpit, monkeypatch) != []


async def test_token_loop_survives_an_undecryptable_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cockpit = make_cockpit(tmp_path, clock=lambda: NOW)
    quiet(cockpit)
    cockpit.token_start_delay_seconds = 0
    cockpit.token_poll_seconds = 0.01
    calls: list[int] = []

    async def broken() -> dict[str, object]:
        calls.append(1)
        raise SecretDecryptError("cannot decrypt")

    monkeypatch.setattr(cockpit.token_checker, "check_all", broken)
    with caplog.at_level(logging.ERROR):
        await cockpit.start()
        try:
            await until(lambda: len(calls) >= 3)
        finally:
            await cockpit.stop()
    assert "the token check failed" in caplog.text


async def test_retention_loop_prunes_old_logs(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path, clock=lambda: NOW)
    quiet(cockpit)
    cockpit.retention_poll_seconds = 0.01
    cockpit.db.set_setting("log_retention_days", "30")
    old = cockpit.paths.logs / "2026-01-01"
    recent = cockpit.paths.logs / "2026-10-19"
    for folder in (old, recent):
        folder.mkdir(parents=True)
        (folder / "run.log").write_text("x")
    await cockpit.start()
    try:
        await until(lambda: not old.exists())
    finally:
        await cockpit.stop()
    assert recent.exists()


async def test_retention_loop_survives_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cockpit = make_cockpit(tmp_path, clock=lambda: NOW)
    quiet(cockpit)
    cockpit.retention_poll_seconds = 0.01
    calls: list[int] = []

    def failing(*args: object) -> int:
        calls.append(1)
        raise OSError("disk")

    monkeypatch.setattr(cockpit.history, "prune_logs", failing)
    with caplog.at_level(logging.ERROR):
        await cockpit.start()
        try:
            await until(lambda: len(calls) >= 2)
        finally:
            await cockpit.stop()
    assert "pruning old logs failed" in caplog.text


async def test_retention_loop_logs_what_it_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cockpit = make_cockpit(tmp_path, clock=lambda: NOW)
    quiet(cockpit)
    cockpit.retention_poll_seconds = 3600
    monkeypatch.setattr(cockpit.history, "prune_logs", lambda *args: 2)
    with caplog.at_level(logging.INFO):
        await cockpit.start()
        try:
            await until(lambda: "removed 2 expired" in caplog.text)
        finally:
            await cockpit.stop()


async def test_a_loop_that_crashes_is_restarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cockpit = make_cockpit(tmp_path)
    quiet(cockpit)
    calls: list[int] = []

    async def crashing() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        await asyncio.sleep(3600)

    monkeypatch.setattr(cockpit.notifier, "run_forever", crashing)
    monkeypatch.setattr("privacyfence_cockpit.services.asyncio.sleep", _fast_sleep(asyncio.sleep))
    with caplog.at_level(logging.ERROR):
        await cockpit.start()
        try:
            await until(lambda: len(calls) >= 2)
        finally:
            await cockpit.stop()
    assert "the notifier loop crashed" in caplog.text


def _fast_sleep(real_sleep):  # type: ignore[no-untyped-def]
    async def fast(seconds: float) -> None:
        await real_sleep(min(seconds, 0.01))

    return fast


async def test_a_loop_that_returns_is_not_restarted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cockpit = make_cockpit(tmp_path)
    quiet(cockpit)
    calls: list[int] = []

    async def finishing() -> None:
        calls.append(1)

    monkeypatch.setattr(cockpit.notifier, "run_forever", finishing)
    await cockpit.start()
    try:
        await until(lambda: calls)
        await asyncio.sleep(0.05)
    finally:
        await cockpit.stop()
    assert calls == [1]


async def test_the_worker_runs_a_queued_job_end_to_end(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    quiet(cockpit)
    remote = make_remote(tmp_path, {"README.md": "x\n"})
    cockpit.git.configure(str(remote), "main", "none")
    cockpit.git.clone()
    # No requirements.txt in the repository, so the venv step fails and becomes the job's error.
    await cockpit.start()
    try:
        cockpit.queue.submit(Job(JobKind.GIT_TRACK, "manual"))
        await until(lambda: cockpit.db.get_setting("venv.last_error") is not None)
    finally:
        await cockpit.stop()
