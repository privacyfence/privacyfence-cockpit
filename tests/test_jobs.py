import asyncio
import json
import logging
import os
import shutil
import threading
from datetime import UTC, datetime

import pytest

from privacyfence_cockpit import jobs
from privacyfence_cockpit.db import Database
from privacyfence_cockpit.effective import Effective
from privacyfence_cockpit.events import EventKind, ListSink
from privacyfence_cockpit.gitrepo import GitManager, no_credentials
from privacyfence_cockpit.history import History, HistoryFilter
from privacyfence_cockpit.jobs import Job, JobKind, JobQueue, Worker
from privacyfence_cockpit.models import Mode, Outcome
from privacyfence_cockpit.paths import DataPaths
from privacyfence_cockpit.recipes import discover
from privacyfence_cockpit.runlock import RunLock
from privacyfence_cockpit.runner import Runner
from privacyfence_cockpit.secrets_store import SecretStore
from privacyfence_cockpit.tokens import TokenResolver
from privacyfence_cockpit.venvs import VenvError, VenvManager, VenvResult
from tests.fakesync import install_fake_venv
from tests.gitutil import make_remote, push_branch, push_commit

NOW = datetime(2026, 10, 20, 6, 15, 7, tzinfo=UTC)
RECIPE = "name: fixture\n"
FILES = {
    "workflows/10-imports/10-one/recipe.yaml": RECIPE,
    "workflows/10-imports/20-two/recipe.yaml": RECIPE,
    "workflows/20-exports/10-three/recipe.yaml": RECIPE,
    "README.md": "one\n",
}


class CountingVenvs(VenvManager):
    """Stands in for pip: counts ensure() calls and keeps the fake environment."""

    ensured = 0

    def ensure(self) -> VenvResult:
        self.ensured += 1
        return VenvResult(self.current(), False, None)

    def ensure_for(self, repo):
        return VenvResult(self.current(), False, None)


class ScriptedRunner(Runner):
    """Rewrites the fake before each run so a job can see a different outcome per run."""

    def __init__(self, env, outcomes, **kwargs):
        super().__init__(**kwargs)
        self.env = env
        self.outcomes = list(outcomes)

    async def run(self, recipe, **kwargs):
        if self.outcomes:
            self.env.venv(outcome=self.outcomes.pop(0))
        return await super().run(recipe, **kwargs)


class Env:
    def __init__(self, tmp_path):
        self.paths = DataPaths(tmp_path / "data").ensure()
        self.db = Database(self.paths.db_file)
        self.db.migrate()
        self.history = History(self.db)
        self.events = ListSink()
        self.record = tmp_path / "record.jsonl"
        self.auto_update = False
        self.remote = make_remote(tmp_path, FILES)
        self.git = GitManager(self.paths, self.db, no_credentials, self.events)
        self.git.configure(str(self.remote), "main", "none")
        self.git.clone()
        self.queue = JobQueue()
        self.venvs = CountingVenvs(self.paths, self.db, self.events)
        self.lock = RunLock(self.paths.run_lock)
        self.lines: list[str] = []
        self.on_wait_calls = 0
        self.venv(outcome="ok")

    def venv(self, **kwargs):
        kwargs.setdefault("record", self.record)
        return install_fake_venv(self.paths, self.db, **kwargs)

    def effective(self):
        return Effective(
            server_url=None,
            server_source="none",
            timezone="UTC",
            timezone_source="default",
            timeout_minutes=60,
            retention_days=90,
            fetch_interval_minutes=15,
            auto_update=self.auto_update,
        )

    def runner(self, outcomes=()):
        return ScriptedRunner(
            self,
            outcomes,
            paths=self.paths,
            history=self.history,
            venvs=self.venvs,
            git=self.git,
            tokens=TokenResolver(SecretStore(self.paths.secrets, os.urandom(32)), None),
            effective=self.effective,
            clock=lambda: NOW,
            kill_grace_seconds=0.5,
        )

    def worker(self, outcomes=(), recipes=None, **kwargs):
        return Worker(
            queue=self.queue,
            runner=self.runner(outcomes),
            git=self.git,
            venvs=self.venvs,
            history=self.history,
            lock=self.lock,
            recipes=recipes or (lambda: discover(self.paths.repo)),
            effective=self.effective,
            events=self.events,
            on_line=self.lines.append,
            **kwargs,
        )

    def rows(self):
        return list(reversed(self.history.query(HistoryFilter())))

    def records(self):
        return [json.loads(line) for line in self.record.read_text().splitlines()]


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


def run_job(**kwargs):
    kwargs.setdefault("trigger", "manual")
    return Job(JobKind.RUN, **kwargs)


# -- queue ---------------------------------------------------------------------------------------


async def test_queue_hands_out_jobs_in_order_and_tracks_the_running_one():
    queue = JobQueue()
    first, second = run_job(), run_job()
    getter = asyncio.create_task(queue.next())
    await asyncio.sleep(0)
    assert queue.running() is None
    assert queue.submit(first) and queue.submit(second)
    assert await getter is first
    assert queue.running() is first
    assert queue.queued() == [second]
    assert await queue.next() is second
    queue.done(first)  # a stale completion does not clear the newer running job
    assert queue.running() is second
    queue.done(second)
    assert queue.running() is None


async def test_schedule_triggers_are_deduplicated_over_queued_and_running():
    queue = JobQueue()
    assert queue.submit(run_job(trigger="schedule:nightly"))
    assert not queue.submit(run_job(trigger="schedule:nightly"))
    assert queue.submit(run_job(trigger="schedule:weekly"))
    running = await queue.next()
    assert running.trigger == "schedule:nightly"
    assert not queue.submit(run_job(trigger="schedule:nightly"))
    queue.done(running)
    assert queue.submit(run_job(trigger="schedule:nightly"))


async def test_manual_jobs_are_never_deduplicated():
    queue = JobQueue()
    assert queue.submit(run_job()) and queue.submit(run_job())
    assert len(queue.queued()) == 2


async def test_cancel_removes_queued_jobs_only():
    queue = JobQueue()
    job = run_job()
    queue.submit(job)
    assert queue.cancel(job.id)
    assert not queue.cancel(job.id)
    assert queue.queued() == []
    queue.submit(running := run_job())
    await queue.next()
    assert not queue.cancel(running.id)


# -- run jobs ------------------------------------------------------------------------------------


async def test_run_one_recipe(env):
    env.venv(lines=["hello"])
    summary = await env.worker().execute(run_job(selectors=("imports/10-one",)))
    assert summary.error is None and len(summary.run_ids) == 1
    [row] = env.rows()
    assert (row.recipe_path, row.outcome, row.trigger) == ("imports/10-one", Outcome.OK, "manual")
    assert row.job_id == summary.job_id
    assert env.venvs.ensured == 1
    assert env.lines == ["hello"]


async def test_no_selectors_runs_every_recipe_in_order(env):
    await env.worker().execute(run_job())
    assert [r.recipe_path for r in env.rows()] == ["imports/10-one", "imports/20-two", "exports/10-three"]


async def test_repeat_runs_again_while_ok(env):
    summary = await env.worker().execute(run_job(selectors=("imports/10-one",), repeat=3))
    assert len(summary.run_ids) == 3


async def test_repeat_stops_at_no_change(env):
    env.venv(outcome="no_change")
    summary = await env.worker().execute(run_job(selectors=("imports/10-one",), repeat=3))
    assert len(summary.run_ids) == 1


async def test_repeat_stops_at_a_failure(env):
    worker = env.worker(outcomes=["ok", "failed", "ok"])
    summary = await worker.execute(run_job(selectors=("imports/10-one",), repeat=3))
    assert [r.outcome for r in env.rows()] == [Outcome.OK, Outcome.FAILED]
    assert len(summary.run_ids) == 2


async def test_stop_on_error_skips_the_remaining_recipes(env):
    summary = await env.worker(outcomes=["failed", "ok"]).execute(
        run_job(selectors=("imports",), stop_on_error=True)
    )
    assert [r.recipe_path for r in env.rows()] == ["imports/10-one"]
    assert len(summary.run_ids) == 1


async def test_without_stop_on_error_the_remaining_recipes_run(env):
    await env.worker(outcomes=["failed", "ok"]).execute(run_job(selectors=("imports",)))
    assert [r.outcome for r in env.rows()] == [Outcome.FAILED, Outcome.OK]


async def test_stop_on_error_continues_after_no_change(env):
    env.venv(outcome="no_change")
    await env.worker().execute(run_job(selectors=("imports",), stop_on_error=True))
    assert len(env.rows()) == 2


async def test_scheduled_failures_emit_run_failed(env):
    summary = await env.worker(outcomes=["failed", "denied", "ok"]).execute(
        run_job(trigger="schedule:nightly", selectors=("imports", "exports"))
    )
    failed = [e for e in env.events.events if e.kind is EventKind.RUN_FAILED]
    assert [(e.recipe, e.outcome, e.schedule) for e in failed] == [
        ("imports/10-one", Outcome.FAILED, "nightly"),
        ("imports/20-two", Outcome.DENIED, "nightly"),
    ]
    assert failed[0].run_id == summary.run_ids[0]
    assert failed[0].exit_code == 0


async def test_manual_failures_emit_nothing(env):
    await env.worker(outcomes=["failed"]).execute(run_job(selectors=("imports/10-one",)))
    assert env.events.events == []


async def test_scheduled_success_and_unnotified_outcomes_emit_nothing(env):
    await env.worker(outcomes=["ok", "no_change"]).execute(
        run_job(trigger="schedule:nightly", selectors=("imports",))
    )
    assert env.events.events == []


async def test_unknown_selector_records_a_recipe_error_row(env):
    summary = await env.worker().execute(run_job(selectors=("nope", "imports/zzz")))
    [row] = env.rows()
    assert (row.outcome, row.status, row.recipe_path) == (Outcome.RECIPE_ERROR, "finished", "nope, imports/zzz")
    assert row.note == "no recipe or group matches 'nope'"
    assert summary.run_ids == (row.id,)
    assert not env.record.exists()


async def test_scheduled_unknown_selector_emits_one_run_failed(env):
    summary = await env.worker().execute(run_job(trigger="schedule:nightly", selectors=("nope", "imports/zzz")))
    [event] = env.events.events
    assert event.kind is EventKind.RUN_FAILED
    assert (event.recipe, event.schedule, event.outcome) == ("nope, imports/zzz", "nightly", Outcome.RECIPE_ERROR)
    assert (event.exit_code, event.run_id) == (None, summary.run_ids[0])
    [row] = env.rows()
    assert row.outcome is Outcome.RECIPE_ERROR


async def test_manual_unknown_selector_emits_nothing(env):
    await env.worker().execute(run_job(selectors=("nope",)))
    assert env.events.events == []


async def test_attended_flag_reaches_preflight(env):
    await env.worker().execute(
        Job(JobKind.RUN, "manual", selectors=("imports/10-one",), mode=Mode.PREFLIGHT, attended=True)
    )
    assert env.records()[0]["argv"][0] == "preflight"
    assert "--attended" in env.records()[0]["argv"]


async def test_the_lock_is_held_during_a_run_and_released_after(env):
    env.venv(lines=["hello"])
    other = RunLock(env.paths.run_lock)
    seen = []
    worker = env.worker()
    worker._on_line = lambda line: seen.append(other.try_acquire())
    await worker.execute(run_job(selectors=("imports/10-one",)))
    assert seen and not any(seen)
    assert other.try_acquire()
    other.release()


async def test_the_lock_is_released_when_the_job_raises(env):
    def broken():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await env.worker(recipes=broken).execute(run_job())
    other = RunLock(env.paths.run_lock)
    assert other.try_acquire()
    other.release()


async def test_on_lock_wait_is_called_when_another_holder_has_the_lock(env):
    holder = RunLock(env.paths.run_lock)
    assert holder.try_acquire()

    def waiting():
        env.on_wait_calls += 1
        holder.release()

    summary = await env.worker(on_lock_wait=waiting).execute(run_job(selectors=("imports/10-one",)))
    assert env.on_wait_calls == 1
    assert len(summary.run_ids) == 1


@pytest.fixture
def fast_poll(monkeypatch):
    monkeypatch.setattr(jobs, "LOCK_POLL_SECONDS", 0.01)


async def test_a_job_waits_for_the_lock_and_runs_when_the_holder_releases(env, fast_poll):
    holder = RunLock(env.paths.run_lock)
    assert holder.try_acquire()
    task = asyncio.create_task(env.worker().execute(run_job(selectors=("imports/10-one",))))
    await asyncio.sleep(0.05)
    assert not task.done()
    holder.release()
    summary = await asyncio.wait_for(task, 2)
    assert len(summary.run_ids) == 1


async def test_on_lock_wait_is_called_once_however_long_the_wait(env, fast_poll):
    holder = RunLock(env.paths.run_lock)
    assert holder.try_acquire()
    task = asyncio.create_task(
        env.worker(on_lock_wait=lambda: setattr(env, "on_wait_calls", env.on_wait_calls + 1)).execute(
            run_job(selectors=("imports/10-one",))
        )
    )
    await asyncio.sleep(0.1)
    holder.release()
    await asyncio.wait_for(task, 2)
    assert env.on_wait_calls == 1


async def test_cancelling_a_worker_waiting_for_the_lock_ends_at_once_without_a_thread(env):
    holder = RunLock(env.paths.run_lock)
    assert holder.try_acquire()
    threads = threading.active_count()
    task = asyncio.create_task(env.worker().execute(run_job()))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert threading.active_count() == threads
    holder.release()
    other = RunLock(env.paths.run_lock)
    assert other.try_acquire()  # nothing took the lock after the cancel
    other.release()


async def test_the_lock_is_released_when_cancelled_right_after_acquiring(env):
    started = asyncio.Event()

    async def hang(job):
        started.set()
        await asyncio.Event().wait()

    worker = env.worker()
    worker._run_job = hang
    task = asyncio.create_task(worker.execute(run_job()))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    other = RunLock(env.paths.run_lock)
    assert other.try_acquire()
    other.release()


# -- auto update ---------------------------------------------------------------------------------


async def test_auto_update_fast_forwards_before_a_scheduled_run(env):
    env.auto_update = True
    sha = push_commit(env.remote, {"new.txt": "x"}, "second")
    await env.worker().execute(run_job(trigger="schedule:nightly", selectors=("imports/10-one",)))
    assert env.git.head() == sha
    assert env.rows()[0].commit_sha == sha


async def test_manual_runs_do_not_auto_update(env):
    env.auto_update = True
    before = env.git.head()
    push_commit(env.remote, {"new.txt": "x"}, "second")
    await env.worker().execute(run_job(selectors=("imports/10-one",)))
    assert env.git.head() == before


async def test_auto_update_is_off_by_default(env):
    before = env.git.head()
    push_commit(env.remote, {"new.txt": "x"}, "second")
    await env.worker().execute(run_job(trigger="schedule:nightly", selectors=("imports/10-one",)))
    assert env.git.head() == before


async def test_auto_update_is_skipped_when_pinned(env):
    env.auto_update = True
    pinned = env.git.head()
    push_commit(env.remote, {"new.txt": "x"}, "second")
    env.git.fetch()
    env.git.pin(pinned)
    await env.worker().execute(run_job(trigger="schedule:nightly", selectors=("imports/10-one",)))
    assert env.git.head() == pinned
    assert env.rows()[0].outcome is Outcome.OK


async def test_a_failing_auto_update_does_not_stop_the_run(env, caplog):
    env.auto_update = True
    (env.paths.repo / "README.md").write_text("edited\n")
    caplog.set_level(logging.WARNING)
    await env.worker().execute(run_job(trigger="schedule:nightly", selectors=("imports/10-one",)))
    assert env.rows()[0].outcome is Outcome.OK
    assert "update before the scheduled run failed" in caplog.text


# -- git jobs ------------------------------------------------------------------------------------


async def test_git_update_job(env):
    sha = push_commit(env.remote, {"new.txt": "x"}, "second")
    summary = await env.worker().execute(Job(JobKind.GIT_UPDATE, "manual"))
    assert (summary.run_ids, summary.error) == ((), None)
    assert env.git.head() == sha
    assert env.venvs.ensured == 1


async def test_git_pin_and_track_jobs(env):
    first = env.git.head()
    push_commit(env.remote, {"new.txt": "x"}, "second")
    env.git.fetch()
    summary = await env.worker().execute(Job(JobKind.GIT_PIN, "manual", sha=first))
    assert summary.error is None
    assert env.db.get_setting("git.pinned_sha") == first
    summary = await env.worker().execute(Job(JobKind.GIT_TRACK, "manual"))
    assert summary.error is None
    assert env.db.get_setting("git.pinned_sha") is None
    assert env.venvs.ensured == 2


async def test_git_clone_job_replaces_the_checkout(env):
    first = env.git.head()
    push_commit(env.remote, {"new.txt": "x"}, "second")
    env.git.fetch()
    env.git.pin(first)
    (env.paths.repo / "untracked.txt").write_text("gone after a re-clone")
    summary = await env.worker().execute(Job(JobKind.GIT_CLONE, "manual"))
    assert summary.error is None
    assert not (env.paths.repo / "untracked.txt").exists()
    assert env.git.head() != first
    assert env.db.get_setting("git.pinned_sha") is None
    assert env.venvs.ensured == 1


async def test_git_clone_job_works_without_a_checkout(env):
    shutil.rmtree(env.paths.repo)
    summary = await env.worker().execute(Job(JobKind.GIT_CLONE, "manual"))
    assert summary.error is None
    assert (env.paths.repo / ".git").exists()


async def test_a_git_error_becomes_the_summary_error(env, caplog):
    caplog.set_level(logging.WARNING)
    summary = await env.worker().execute(Job(JobKind.GIT_PIN, "manual", sha="a" * 40))
    assert summary.error == "unknown commit"
    assert summary.run_ids == ()
    assert "unknown commit" in caplog.text
    assert env.venvs.ensured == 0


async def test_git_update_refuses_when_pinned(env):
    env.git.pin(env.git.head())
    summary = await env.worker().execute(Job(JobKind.GIT_UPDATE, "manual"))
    assert summary.error is not None and "pinned" in summary.error


async def test_a_venv_error_becomes_the_summary_error(env):
    def failing():
        raise VenvError("no requirements.txt")

    env.venvs.ensure = failing
    summary = await env.worker().execute(Job(JobKind.GIT_UPDATE, "manual"))
    assert summary.error == "no requirements.txt"


# -- run_forever ---------------------------------------------------------------------------------


async def wait_for(predicate):
    for _ in range(250):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition never became true")


async def test_run_forever_runs_queued_jobs_and_survives_a_crash(env, caplog):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return discover(env.paths.repo)

    worker = env.worker(recipes=flaky)
    task = asyncio.create_task(worker.run_forever())
    env.queue.submit(run_job(selectors=("imports/10-one",)))
    env.queue.submit(run_job(selectors=("imports/10-one",)))
    await wait_for(lambda: len(env.rows()) == 1 and env.queue.running() is None)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "crashed" in caplog.text
    assert env.queue.queued() == []
    assert env.rows()[0].outcome is Outcome.OK


async def test_run_forever_marks_the_queue_idle_after_each_job(env):
    task = asyncio.create_task(env.worker().run_forever())
    job = run_job(trigger="schedule:nightly", selectors=("imports/10-one",))
    assert env.queue.submit(job)
    await wait_for(lambda: len(env.rows()) == 1 and env.queue.running() is None)
    assert env.queue.submit(run_job(trigger="schedule:nightly", selectors=("imports/10-one",)))
    await wait_for(lambda: len(env.rows()) == 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# -- branch jobs ---------------------------------------------------------------------------------


def test_job_rejects_a_branch_for_scheduled_or_git_jobs():
    with pytest.raises(ValueError, match="only manual run jobs can choose a branch"):
        Job(JobKind.RUN, "schedule:nightly", branch="feature/x")
    with pytest.raises(ValueError, match="only manual run jobs can choose a branch"):
        Job(JobKind.GIT_UPDATE, "manual", branch="feature/x")
    assert Job(JobKind.RUN, "manual", branch="feature/x").branch == "feature/x"


async def test_branch_job_runs_the_branch_recipes(env):
    sha = push_branch(env.remote, {"workflows/30-new/10-only/recipe.yaml": RECIPE}, "feature/x")
    head, current = env.git.head(), env.db.get_setting("venv.current")
    summary = await env.worker().execute(run_job(selectors=("new/10-only",), branch="feature/x"))
    assert summary.error is None and len(summary.run_ids) == 1
    [row] = env.rows()
    assert (row.recipe_path, row.outcome, row.branch, row.commit_sha) == (
        "new/10-only",
        Outcome.OK,
        "feature/x",
        sha,
    )
    assert env.venvs.ensured == 0
    assert env.git.head() == head
    assert env.db.get_setting("venv.current") == current
    assert (env.paths.branches / "feature%2Fx" / "workflows/30-new/10-only/recipe.yaml").exists()


async def test_branch_job_with_an_unknown_branch_records_a_failed_row(env):
    summary = await env.worker().execute(run_job(selectors=("imports",), branch="nope"))
    [row] = env.rows()
    assert summary.run_ids == (row.id,)
    assert row.outcome is Outcome.FAILED
    assert row.note == "There is no branch nope on the remote."
    assert row.branch == "nope"


async def test_branch_job_with_an_unknown_selector_records_a_recipe_error_with_the_branch(env):
    push_branch(env.remote, {"README.md": "branch\n"}, "feature/x")
    await env.worker().execute(run_job(selectors=("missing",), branch="feature/x"))
    [row] = env.rows()
    assert row.outcome is Outcome.RECIPE_ERROR
    assert row.branch == "feature/x"


async def test_branch_job_with_clashing_groups_records_a_recipe_error(env):
    push_branch(
        env.remote,
        {
            "workflows/10-imports/10-one/recipe.yaml": RECIPE,
            "workflows/20-imports/10-one/recipe.yaml": RECIPE,
        },
        "feature/x",
    )
    await env.worker().execute(run_job(selectors=("imports",), branch="feature/x"))
    [row] = env.rows()
    assert row.outcome is Outcome.RECIPE_ERROR
    assert row.branch == "feature/x"
    assert row.note


async def test_reclone_removes_branch_worktrees_and_environments(env):
    push_branch(env.remote, {"README.md": "branch\n"}, "feature/x")
    env.git.fetch()
    env.git.branch_worktree("feature/x")
    env.paths.branch_venvs.mkdir()
    (env.paths.branch_venvs / "leftover").mkdir()
    summary = await env.worker().execute(Job(JobKind.GIT_CLONE, "manual"))
    assert summary.error is None
    assert not env.paths.branches.exists()
    assert not env.paths.branch_venvs.exists()
