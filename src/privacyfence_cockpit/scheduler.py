"""Turns the cron schedules of cockpit.yaml into queued jobs; missed fires are recorded, never caught up."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter

from privacyfence_cockpit.cockpit_yaml import CockpitConfig, CockpitConfigError, Schedule
from privacyfence_cockpit.db import Database
from privacyfence_cockpit.effective import Effective
from privacyfence_cockpit.events import Event, EventKind, EventSink
from privacyfence_cockpit.history import History
from privacyfence_cockpit.jobs import SCHEDULE_PREFIX, Job, JobKind, JobQueue
from privacyfence_cockpit.models import Mode, iso, parse_iso, utc_now
from privacyfence_cockpit.recipes import Recipe, SelectionError, select

log = logging.getLogger(__name__)

TICK_SECONDS = 30
MISSED_AFTER_SECONDS = 90
_LAST_TICK = "scheduler.last_tick_at"


def next_fire(schedule: Schedule, tz: str, after: datetime) -> datetime:
    local = ZoneInfo(tz)
    fire: datetime = croniter(schedule.cron, after.astimezone(local)).get_next(datetime)
    return fire.astimezone(UTC)


def fires_between(schedule: Schedule, tz: str, start: datetime, end: datetime) -> list[datetime]:
    """Fires in (start, end]. A local wall time repeated by the autumn clock change fires once."""
    iterator = croniter(schedule.cron, start.astimezone(ZoneInfo(tz)))
    fires: list[datetime] = []
    previous_wall: datetime | None = None
    while True:
        fire: datetime = iterator.get_next(datetime)
        if fire.astimezone(UTC) > end:
            return fires
        wall = fire.replace(tzinfo=None)
        if wall == previous_wall:
            continue
        previous_wall = wall
        fires.append(fire.astimezone(UTC))


def schedules_for(recipe: Recipe, config: CockpitConfig, recipes: Sequence[Recipe]) -> list[Schedule]:
    found: list[Schedule] = []
    for schedule in config.schedules:
        try:
            chosen = select(recipes, schedule.run)
        except SelectionError:
            continue
        if recipe in chosen:
            found.append(schedule)
    return found


class Scheduler:
    def __init__(
        self,
        *,
        db: Database,
        history: History,
        queue: JobQueue,
        config: Callable[[], CockpitConfig],
        effective: Callable[[], Effective],
        events: EventSink,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._history = history
        self._queue = queue
        self._config = config
        self._effective = effective
        self._events = events
        self._clock = clock

    def is_paused(self, name: str) -> bool:
        row = self._db.query_one("SELECT paused FROM schedule_state WHERE name = ?", (name,))
        return row is not None and bool(row["paused"])

    def set_paused(self, name: str, paused: bool) -> None:
        self._db.execute(
            "INSERT INTO schedule_state (name, paused, changed_at) VALUES (?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET paused = excluded.paused, changed_at = excluded.changed_at",
            (name, int(paused), iso(self._clock())),
        )

    def tick(self) -> list[Job]:
        now = self._clock()
        stored = self._db.get_setting(_LAST_TICK)
        last = parse_iso(stored) if stored else now
        submitted: list[Job] = []
        try:
            config = self._config()
        except CockpitConfigError as exc:
            log.warning("scheduler: %s", exc)
            self._db.set_setting(_LAST_TICK, iso(now))
            return submitted
        tz = self._effective().timezone
        local = ZoneInfo(tz)
        missed_before = now - timedelta(seconds=MISSED_AFTER_SECONDS)
        for schedule in config.schedules:
            if self.is_paused(schedule.name):
                continue
            due = fires_between(schedule, tz, last, now)
            missed = [fire for fire in due if fire <= missed_before]
            if missed:
                first, final = missed[0].astimezone(local), missed[-1].astimezone(local)
                note = (
                    f"{len(missed)} run(s) between {first:%Y-%m-%d %H:%M} and {final:%Y-%m-%d %H:%M} {tz} "
                    "were missed while the cockpit was not running"
                )
                self._history.record_missed(schedule.name, missed[-1], note)
                self._events.emit(Event(EventKind.SCHEDULE_MISSED, now, schedule=schedule.name, detail=note))
            if len(missed) < len(due):
                job = Job(
                    kind=JobKind.RUN,
                    trigger=f"{SCHEDULE_PREFIX}{schedule.name}",
                    selectors=schedule.run,
                    mode=Mode.UNATTENDED,
                    repeat=schedule.repeat,
                    stop_on_error=schedule.stop_on_error,
                )
                if self._queue.submit(job):
                    submitted.append(job)
                else:
                    log.info("schedule %s is still queued or running; this fire is skipped", schedule.name)
        self._db.set_setting(_LAST_TICK, iso(now))
        return submitted

    async def run_forever(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(TICK_SECONDS)
