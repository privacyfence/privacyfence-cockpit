import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from privacyfence_cockpit import scheduler as scheduler_module
from privacyfence_cockpit.cockpit_yaml import CockpitConfig, CockpitConfigError, Schedule
from privacyfence_cockpit.db import Database
from privacyfence_cockpit.effective import Effective
from privacyfence_cockpit.events import EventKind, ListSink
from privacyfence_cockpit.history import History, HistoryFilter
from privacyfence_cockpit.jobs import JobKind, JobQueue
from privacyfence_cockpit.models import Mode, Outcome, iso
from privacyfence_cockpit.recipes import discover
from privacyfence_cockpit.scheduler import (
    MISSED_AFTER_SECONDS,
    TICK_SECONDS,
    Scheduler,
    fires_between,
    next_fire,
    schedules_for,
)

TZ = "Europe/Budapest"


def utc(text):
    return datetime.fromisoformat(text).astimezone(UTC)


class Clock:
    """A list-backed clock: each call returns the current entry; `set` moves it on."""

    def __init__(self, start):
        self.times = [utc(start)]

    def __call__(self):
        return self.times[-1]

    def set(self, text):
        self.times.append(utc(text))


class Env:
    def __init__(self, tmp_path, schedules, start="2026-10-20T10:00:00+00:00"):
        self.db = Database(tmp_path / "cockpit.db")
        self.db.migrate()
        self.history = History(self.db)
        self.queue = JobQueue()
        self.events = ListSink()
        self.clock = Clock(start)
        self.schedules = list(schedules)
        self.error = None
        self.scheduler = Scheduler(
            db=self.db,
            history=self.history,
            queue=self.queue,
            config=self.config,
            effective=self.effective,
            events=self.events,
            clock=self.clock,
        )

    def config(self):
        if self.error is not None:
            raise self.error
        return CockpitConfig(schedules=tuple(self.schedules))

    def effective(self):
        return Effective(
            server_url=None,
            server_source="none",
            timezone=TZ,
            timezone_source="settings",
            timeout_minutes=60,
            retention_days=90,
            fetch_interval_minutes=15,
            auto_update=False,
        )

    def tick_at(self, text):
        self.clock.set(text)
        return self.scheduler.tick()


@pytest.fixture
def make_env(tmp_path):
    made = []

    def make(schedules, **kwargs):
        env = Env(tmp_path, schedules, **kwargs)
        made.append(env)
        return env

    yield make
    for env in made:
        env.db.close()


DAILY = Schedule("daily", "0 3 * * *", ("imports",), repeat=2, stop_on_error=True)


# -- cron arithmetic -----------------------------------------------------------------------------


def test_constants():
    assert (TICK_SECONDS, MISSED_AFTER_SECONDS) == (30, 90)


def test_next_fire_is_in_utc_and_after_the_given_time():
    fire = next_fire(DAILY, TZ, utc("2026-10-20T10:00:00+00:00"))
    assert fire == utc("2026-10-21T01:00:00+00:00")
    assert fire.tzinfo is UTC
    assert next_fire(DAILY, TZ, fire) == utc("2026-10-22T01:00:00+00:00")


def test_fires_between_excludes_start_and_includes_end():
    start, end = utc("2026-10-20T01:00:00+00:00"), utc("2026-10-21T01:00:00+00:00")
    assert fires_between(DAILY, TZ, start, end) == [end]
    assert fires_between(DAILY, TZ, start, end - timedelta(seconds=1)) == []


def test_autumn_clock_change_fires_a_repeated_local_time_once():
    schedule = Schedule("night", "30 2 * * *", ("imports",))
    fires = fires_between(schedule, TZ, utc("2026-10-24T12:00:00+00:00"), utc("2026-10-26T00:00:00+00:00"))
    assert fires == [utc("2026-10-25T00:30:00+00:00")]


def test_spring_clock_change_fires_a_skipped_local_time_once_at_the_shifted_time():
    schedule = Schedule("night", "30 2 * * *", ("imports",))
    start = utc("2026-03-28T12:00:00+00:00")
    assert fires_between(schedule, TZ, start, utc("2026-03-30T00:00:00+00:00")) == [utc("2026-03-29T01:00:00+00:00")]
    # the next day's 02:30 is back to normal, 00:30Z
    assert fires_between(schedule, TZ, start, utc("2026-03-31T00:00:00+00:00")) == [
        utc("2026-03-29T01:00:00+00:00"),
        utc("2026-03-30T00:30:00+00:00"),
    ]


def test_schedules_for_a_recipe(tmp_path):
    for folder in ("10-imports/10-one", "10-imports/20-two", "20-exports/10-three"):
        (tmp_path / "workflows" / folder).mkdir(parents=True)
        (tmp_path / "workflows" / folder / "recipe.yaml").write_text("name: x\n")
    one, two, three = discover(tmp_path)
    config = CockpitConfig(
        schedules=(
            Schedule("all", "0 3 * * *", ()),
            Schedule("group", "0 4 * * *", ("imports",)),
            Schedule("single", "0 5 * * *", ("exports/10-three",)),
            Schedule("broken", "0 6 * * *", ("nowhere",)),
        )
    )
    names = lambda recipe: [s.name for s in schedules_for(recipe, config, [one, two, three])]  # noqa: E731
    assert names(one) == ["all", "group"]
    assert names(two) == ["all", "group"]
    assert names(three) == ["all", "single"]


# -- pause state ---------------------------------------------------------------------------------


def test_pause_state_is_stored_per_schedule(make_env):
    env = make_env([DAILY])
    assert not env.scheduler.is_paused("daily")
    env.scheduler.set_paused("daily", True)
    assert env.scheduler.is_paused("daily")
    assert not env.scheduler.is_paused("other")
    env.scheduler.set_paused("daily", False)
    assert not env.scheduler.is_paused("daily")
    row = env.db.query_one("SELECT changed_at FROM schedule_state WHERE name = 'daily'")
    assert row["changed_at"] == iso(env.clock())


# -- tick ----------------------------------------------------------------------------------------


def test_the_first_tick_ever_fires_nothing_and_misses_nothing(make_env):
    env = make_env([Schedule("minutely", "* * * * *", ("imports",))])
    assert env.scheduler.tick() == []
    assert env.queue.queued() == [] and env.events.events == []
    assert env.history.count(HistoryFilter()) == 0
    assert env.db.get_setting("scheduler.last_tick_at") == iso(env.clock())


def test_a_fire_inside_the_window_submits_one_job(make_env):
    env = make_env([DAILY], start="2026-10-20T10:00:00+00:00")
    env.scheduler.tick()
    assert env.tick_at("2026-10-21T00:59:40+00:00") == []
    [job] = env.tick_at("2026-10-21T01:00:10+00:00")
    assert env.queue.queued() == [job]
    assert (job.kind, job.trigger, job.selectors, job.mode) == (
        JobKind.RUN,
        "schedule:daily",
        ("imports",),
        Mode.UNATTENDED,
    )
    assert (job.repeat, job.stop_on_error) == (2, True)
    assert env.history.count(HistoryFilter()) == 0
    assert env.db.get_setting("scheduler.last_tick_at") == "2026-10-21T01:00:10Z"


def test_a_fire_is_not_repeated_on_the_next_tick(make_env):
    env = make_env([DAILY], start="2026-10-21T00:59:40+00:00")
    env.scheduler.tick()
    assert len(env.tick_at("2026-10-21T01:00:10+00:00")) == 1
    assert env.tick_at("2026-10-21T01:00:40+00:00") == []


def test_two_fires_in_one_tick_make_one_job(make_env):
    env = make_env([Schedule("minutely", "* * * * *", ("imports",))], start="2026-10-20T09:59:30+00:00")
    env.scheduler.tick()
    jobs = env.tick_at("2026-10-20T10:01:00+00:00")
    assert len(jobs) == 1
    assert env.history.count(HistoryFilter()) == 0


def test_a_schedule_already_queued_is_not_submitted_again(make_env, caplog):
    env = make_env([Schedule("minutely", "* * * * *", ("imports",))], start="2026-10-20T09:59:50+00:00")
    env.scheduler.tick()
    caplog.set_level(logging.INFO)
    assert len(env.tick_at("2026-10-20T10:00:10+00:00")) == 1
    assert env.tick_at("2026-10-20T10:01:10+00:00") == []
    assert len(env.queue.queued()) == 1
    assert "still queued or running" in caplog.text


def test_paused_schedules_neither_fire_nor_count_as_missed(make_env):
    env = make_env([DAILY])
    env.scheduler.tick()
    env.scheduler.set_paused("daily", True)
    assert env.tick_at("2026-10-23T10:00:00+00:00") == []
    assert env.queue.queued() == [] and env.events.events == []
    assert env.history.count(HistoryFilter()) == 0
    env.scheduler.set_paused("daily", False)
    assert env.tick_at("2026-10-23T10:00:30+00:00") == []  # the pause window is not made up


def test_a_restart_after_three_days_records_one_missed_row_and_no_job(make_env):
    env = make_env([DAILY], start="2026-10-20T10:00:00+00:00")
    env.scheduler.tick()
    assert env.tick_at("2026-10-23T10:00:00+00:00") == []
    note = (
        "3 run(s) between 2026-10-21 03:00 and 2026-10-23 03:00 Europe/Budapest "
        "were missed while the cockpit was not running"
    )
    [row] = env.history.query(HistoryFilter())
    assert (row.outcome, row.recipe_path, row.trigger, row.note) == (
        Outcome.MISSED,
        None,
        "schedule:daily",
        note,
    )
    assert row.started_at == "2026-10-23T01:00:00Z"
    [event] = env.events.events
    assert (event.kind, event.schedule, event.detail, event.time) == (
        EventKind.SCHEDULE_MISSED,
        "daily",
        note,
        env.clock(),
    )
    assert env.queue.queued() == []


def test_missed_and_due_fires_in_one_tick(make_env):
    env = make_env([Schedule("often", "*/10 * * * *", ("imports",))], start="2026-10-20T10:00:00+00:00")
    env.scheduler.tick()
    [job] = env.tick_at("2026-10-20T10:20:30+00:00")
    assert job.trigger == "schedule:often"
    [row] = env.history.query(HistoryFilter())
    assert row.note.startswith("1 run(s) between 2026-10-20 12:10 and 2026-10-20 12:10 Europe/Budapest")
    assert [e.kind for e in env.events.events] == [EventKind.SCHEDULE_MISSED]


def test_a_fire_exactly_at_the_missed_boundary_is_missed(make_env):
    env = make_env([Schedule("often", "*/10 * * * *", ("imports",))], start="2026-10-20T10:00:00+00:00")
    env.scheduler.tick()
    assert env.tick_at("2026-10-20T10:11:30+00:00") == []
    assert env.history.count(HistoryFilter()) == 1


def test_each_schedule_is_handled_on_its_own(make_env):
    env = make_env([DAILY, Schedule("other", "0 3 * * *", ("exports",))])
    env.scheduler.tick()
    env.scheduler.set_paused("other", True)
    env.tick_at("2026-10-22T10:00:00+00:00")
    [row] = env.history.query(HistoryFilter())
    assert row.trigger == "schedule:daily"


def test_a_cockpit_yaml_error_submits_nothing_and_moves_the_last_tick(make_env, caplog):
    env = make_env([DAILY], start="2026-10-20T10:00:00+00:00")
    env.scheduler.tick()
    env.error = CockpitConfigError("cockpit.yaml: version must be 1")
    assert env.tick_at("2026-10-23T10:00:00+00:00") == []
    assert env.db.get_setting("scheduler.last_tick_at") == "2026-10-23T10:00:00Z"
    assert "cockpit.yaml: version must be 1" in caplog.text
    env.error = None
    assert env.tick_at("2026-10-23T10:00:30+00:00") == []  # the broken window is not made up
    assert env.history.count(HistoryFilter()) == 0


# -- run_forever ---------------------------------------------------------------------------------


async def test_run_forever_ticks_repeatedly_and_survives_an_error(make_env, monkeypatch, caplog):
    monkeypatch.setattr(scheduler_module, "TICK_SECONDS", 0.01)
    env = make_env([DAILY])
    env.error = RuntimeError("boom")
    task = asyncio.create_task(env.scheduler.run_forever())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if "scheduler tick failed" in caplog.text:
            break
    env.error = None
    for _ in range(100):
        await asyncio.sleep(0.01)
        if env.db.get_setting("scheduler.last_tick_at"):
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "scheduler tick failed" in caplog.text
    assert env.db.get_setting("scheduler.last_tick_at") == iso(env.clock())
