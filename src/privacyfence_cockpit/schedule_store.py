"""Schedules created in the dashboard: stored in the database, merged with those of cockpit.yaml."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from privacyfence_cockpit.cockpit_yaml import Schedule
from privacyfence_cockpit.db import Database
from privacyfence_cockpit.models import iso, utc_now


class ScheduleStore:
    def __init__(self, db: Database, clock: Callable[[], datetime] = utc_now) -> None:
        self._db = db
        self._clock = clock

    def all(self) -> tuple[Schedule, ...]:
        rows = self._db.query("SELECT * FROM ui_schedules ORDER BY name")
        return tuple(
            Schedule(
                row["name"],
                row["cron"],
                tuple(json.loads(row["run_json"])),
                row["repeat"],
                bool(row["stop_on_error"]),
                source="ui",
            )
            for row in rows
        )

    def get(self, name: str) -> Schedule | None:
        return next((s for s in self.all() if s.name == name), None)

    def save(self, schedule: Schedule) -> None:
        self._db.execute(
            "INSERT INTO ui_schedules (name, cron, run_json, repeat, stop_on_error, changed_at)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET cron = excluded.cron,"
            " run_json = excluded.run_json, repeat = excluded.repeat, stop_on_error = excluded.stop_on_error,"
            " changed_at = excluded.changed_at",
            (
                schedule.name,
                schedule.cron,
                json.dumps(list(schedule.run)),
                schedule.repeat,
                int(schedule.stop_on_error),
                iso(self._clock()),
            ),
        )

    def delete(self, name: str) -> None:
        self._db.execute("DELETE FROM ui_schedules WHERE name = ?", (name,))
        self._db.execute("DELETE FROM schedule_state WHERE name = ?", (name,))
