"""The job queue and the worker that runs jobs one at a time under the run lock."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from privacyfence_cockpit.effective import Effective
from privacyfence_cockpit.events import Event, EventKind, EventSink
from privacyfence_cockpit.gitrepo import GitError, GitManager
from privacyfence_cockpit.history import History
from privacyfence_cockpit.models import NOTIFY_OUTCOMES, SUCCESS_OUTCOMES, Mode, Outcome, utc_now
from privacyfence_cockpit.recipes import Recipe, SelectionError, select
from privacyfence_cockpit.runlock import RunLock
from privacyfence_cockpit.runner import Runner
from privacyfence_cockpit.venvs import VenvError, VenvManager

log = logging.getLogger(__name__)

SCHEDULE_PREFIX = "schedule:"


class JobKind(StrEnum):
    RUN = "run"
    GIT_UPDATE = "git_update"
    GIT_PIN = "git_pin"
    GIT_TRACK = "git_track"
    GIT_CLONE = "git_clone"


@dataclass(frozen=True)
class Job:
    kind: JobKind
    trigger: str
    selectors: tuple[str, ...] = ()
    mode: Mode = Mode.UNATTENDED
    repeat: int = 1
    stop_on_error: bool = False
    sha: str | None = None
    attended: bool = False  # passed to Runner.run; only preflight uses it
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    submitted_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class JobSummary:
    job_id: str
    run_ids: tuple[int, ...]
    error: str | None


class JobQueue:
    def __init__(self) -> None:
        self._queued: list[Job] = []
        self._running: Job | None = None
        self._wakeup = asyncio.Event()

    def submit(self, job: Job) -> bool:
        """Queue a job; False when a job with the same schedule trigger is already queued or running."""
        if job.trigger.startswith(SCHEDULE_PREFIX) and any(
            other.trigger == job.trigger for other in (*self._queued, *([self._running] if self._running else []))
        ):
            return False
        self._queued.append(job)
        self._wakeup.set()
        return True

    def queued(self) -> list[Job]:
        return list(self._queued)

    def running(self) -> Job | None:
        return self._running

    def cancel(self, job_id: str) -> bool:
        for job in self._queued:
            if job.id == job_id:
                self._queued.remove(job)
                return True
        return False

    async def next(self) -> Job:
        while not self._queued:
            self._wakeup.clear()
            await self._wakeup.wait()
        job = self._queued.pop(0)
        self._running = job
        return job

    def done(self, job: Job) -> None:
        if self._running is job:
            self._running = None


class Worker:
    def __init__(
        self,
        *,
        queue: JobQueue,
        runner: Runner,
        git: GitManager,
        venvs: VenvManager,
        history: History,
        lock: RunLock,
        recipes: Callable[[], list[Recipe]],
        effective: Callable[[], Effective],
        events: EventSink,
        on_lock_wait: Callable[[], None] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> None:
        self._queue = queue
        self._runner = runner
        self._git = git
        self._venvs = venvs
        self._history = history
        self._lock = lock
        self._recipes = recipes
        self._effective = effective
        self._events = events
        self._on_lock_wait = on_lock_wait
        self._on_line = on_line

    async def execute(self, job: Job) -> JobSummary:
        await asyncio.to_thread(self._lock.acquire, self._on_lock_wait)
        try:
            if job.kind is JobKind.RUN:
                return await self._run_job(job)
            return await self._git_job(job)
        finally:
            self._lock.release()

    async def run_forever(self) -> None:
        while True:
            job = await self._queue.next()
            try:
                await self.execute(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("job %s (%s) crashed", job.id, job.kind)
            finally:
                self._queue.done(job)

    async def _git_job(self, job: Job) -> JobSummary:
        try:
            if job.kind is JobKind.GIT_UPDATE:
                await asyncio.to_thread(self._git.update)
            elif job.kind is JobKind.GIT_PIN:
                await asyncio.to_thread(self._git.pin, job.sha or "")
            elif job.kind is JobKind.GIT_TRACK:
                await asyncio.to_thread(self._git.track)
            else:
                await asyncio.to_thread(self._reclone)
            await asyncio.to_thread(self._venvs.ensure)
        except (GitError, VenvError) as exc:
            log.warning("%s failed: %s", job.kind, exc)
            return JobSummary(job.id, (), str(exc))
        return JobSummary(job.id, (), None)

    def _reclone(self) -> None:
        paths = self._venvs.paths
        # The git lock keeps a fetch from running inside the checkout while it is deleted.
        with paths.git_lock.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            shutil.rmtree(paths.repo, ignore_errors=True)
        self._venvs.db.delete_setting("git.pinned_sha")
        self._git.clone()

    async def _run_job(self, job: Job) -> JobSummary:
        scheduled = job.trigger.startswith(SCHEDULE_PREFIX)
        if scheduled and self._effective().auto_update:
            await self._auto_update()
        await asyncio.to_thread(self._venvs.ensure)
        try:
            chosen = select(self._recipes(), job.selectors)
        except SelectionError as exc:
            return JobSummary(job.id, (self._record_selection_error(job, str(exc)),), None)
        run_ids: list[int] = []
        emitted: set[tuple[str, Outcome]] = set()
        for recipe in chosen:
            outcome: Outcome | None = None
            for _ in range(job.repeat):
                result = await self._runner.run(
                    recipe,
                    mode=job.mode,
                    trigger=job.trigger,
                    job_id=job.id,
                    on_line=self._on_line,
                    attended=job.attended,
                )
                run_ids.append(result.run_id)
                outcome = result.outcome
                if scheduled and outcome in NOTIFY_OUTCOMES and (recipe.path, outcome) not in emitted:
                    emitted.add((recipe.path, outcome))
                    self._events.emit(
                        Event(
                            EventKind.RUN_FAILED,
                            utc_now(),
                            recipe=recipe.path,
                            schedule=job.trigger.removeprefix(SCHEDULE_PREFIX),
                            outcome=outcome,
                            exit_code=result.exit_code,
                            run_id=result.run_id,
                        )
                    )
                if outcome is not Outcome.OK:
                    break
            if job.stop_on_error and outcome not in SUCCESS_OUTCOMES:
                break
        return JobSummary(job.id, tuple(run_ids), None)

    async def _auto_update(self) -> None:
        try:
            status = await asyncio.to_thread(self._git.status)
            if status.pinned_sha is None:
                await asyncio.to_thread(self._git.update)
        except GitError as exc:
            log.warning("update before the scheduled run failed: %s", exc)

    def _record_selection_error(self, job: Job, message: str) -> int:
        now = utc_now()
        run_id = self._history.start(
            job_id=job.id,
            trigger=job.trigger,
            recipe_path=", ".join(job.selectors),
            mode=job.mode,
            started_at=now,
            log_path=None,
            commit_sha=None,
            token_source=None,
        )
        self._history.finish(run_id, outcome=Outcome.RECIPE_ERROR, exit_code=None, finished_at=now, note=message)
        return run_id
