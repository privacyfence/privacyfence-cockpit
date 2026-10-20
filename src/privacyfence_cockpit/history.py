"""Run history: typed rows of the `runs` table, queries and orphan handling."""

from __future__ import annotations

import csv
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TextIO

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.models import Mode, Outcome, iso, parse_iso
from privacyfence_cockpit.paths import DataPaths

ORPHAN_NOTE = "the cockpit stopped during this run"
CSV_COLUMNS = (
    "id",
    "started_at",
    "finished_at",
    "trigger",
    "recipe",
    "mode",
    "outcome",
    "exit_code",
    "duration_seconds",
    "commit",
    "token_source",
    "log_path",
    "note",
)


@dataclass(frozen=True)
class RunRow:
    id: int
    job_id: str | None
    trigger: str
    recipe_path: str | None
    mode: Mode
    status: str
    outcome: Outcome | None
    exit_code: int | None
    started_at: str
    finished_at: str | None
    duration_seconds: float | None
    log_path: str | None
    commit_sha: str | None
    token_source: str | None
    pid: int | None
    stop_requested: bool
    note: str | None


@dataclass(frozen=True)
class HistoryFilter:
    recipe: str | None = None
    outcome: Outcome | None = None
    trigger: str | None = None
    mode: Mode | None = None
    since: date | None = None
    until: date | None = None
    job_id: str | None = None


def _row(r: sqlite3.Row) -> RunRow:
    return RunRow(
        id=r["id"],
        job_id=r["job_id"],
        trigger=r["trigger"],
        recipe_path=r["recipe_path"],
        mode=Mode(r["mode"]),
        status=r["status"],
        outcome=None if r["outcome"] is None else Outcome(r["outcome"]),
        exit_code=r["exit_code"],
        started_at=r["started_at"],
        finished_at=r["finished_at"],
        duration_seconds=r["duration_seconds"],
        log_path=r["log_path"],
        commit_sha=r["commit_sha"],
        token_source=r["token_source"],
        pid=r["pid"],
        stop_requested=bool(r["stop_requested"]),
        note=r["note"],
    )


def _where(f: HistoryFilter) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if f.recipe is not None:
        clauses.append("recipe_path = ?")
        params.append(f.recipe)
    if f.outcome is not None:
        clauses.append("outcome = ?")
        params.append(f.outcome.value)
    if f.trigger is not None:
        clauses.append("trigger = ?")
        params.append(f.trigger)
    if f.mode is not None:
        clauses.append("mode = ?")
        params.append(f.mode.value)
    if f.since is not None:
        clauses.append("started_at >= ?")
        params.append(f"{f.since.isoformat()}T00:00:00Z")
    if f.until is not None:
        clauses.append("started_at < ?")
        params.append(f"{(f.until + timedelta(days=1)).isoformat()}T00:00:00Z")
    if f.job_id is not None:
        clauses.append("job_id = ?")
        params.append(f.job_id)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


class History:
    def __init__(self, db: Database) -> None:
        self._db = db

    def start(
        self,
        *,
        job_id: str | None,
        trigger: str,
        recipe_path: str | None,
        mode: Mode,
        started_at: datetime,
        log_path: str | None,
        commit_sha: str | None,
        token_source: str | None,
    ) -> int:
        cursor = self._db.execute(
            "INSERT INTO runs (job_id, trigger, recipe_path, mode, status, started_at, log_path, commit_sha,"
            " token_source) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)",
            (job_id, trigger, recipe_path, mode.value, iso(started_at), log_path, commit_sha, token_source),
        )
        assert cursor.lastrowid is not None  # noqa: S101  # nosec B101  # an INSERT always sets it
        return cursor.lastrowid

    def set_log_path(self, run_id: int, log_path: str) -> None:
        self._db.execute("UPDATE runs SET log_path = ? WHERE id = ?", (log_path, run_id))

    def set_pid(self, run_id: int, pid: int | None) -> None:
        self._db.execute("UPDATE runs SET pid = ? WHERE id = ?", (pid, run_id))

    def finish(
        self,
        run_id: int,
        *,
        outcome: Outcome,
        exit_code: int | None,
        finished_at: datetime,
        note: str | None = None,
    ) -> None:
        row = self._db.query_one("SELECT started_at FROM runs WHERE id = ?", (run_id,))
        if row is None:
            return
        duration = (finished_at - parse_iso(row["started_at"])).total_seconds()
        self._db.execute(
            "UPDATE runs SET status = 'finished', outcome = ?, exit_code = ?, finished_at = ?,"
            " duration_seconds = ?, note = ? WHERE id = ?",
            (outcome.value, exit_code, iso(finished_at), duration, note, run_id),
        )

    def request_stop(self, run_id: int) -> RunRow | None:
        self._db.execute("UPDATE runs SET stop_requested = 1 WHERE id = ? AND status = 'running'", (run_id,))
        return self.get(run_id)

    def get(self, run_id: int) -> RunRow | None:
        row = self._db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
        return None if row is None else _row(row)

    def query(self, f: HistoryFilter, limit: int = 50, offset: int = 0) -> list[RunRow]:
        where, params = _where(f)
        rows = self._db.query(
            f"SELECT * FROM runs{where} ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?",  # noqa: S608  # nosec B608  # fixed clauses, values bound
            (*params, limit, offset),
        )
        return [_row(r) for r in rows]

    def count(self, f: HistoryFilter) -> int:
        where, params = _where(f)
        row = self._db.query_one(f"SELECT COUNT(*) FROM runs{where}", params)  # noqa: S608  # nosec B608  # fixed clauses, values bound
        assert row is not None  # noqa: S101  # nosec B101  # COUNT always returns a row
        return int(row[0])

    def last_by_recipe(self) -> dict[str, RunRow]:
        rows = self._db.query(
            "SELECT * FROM runs WHERE status = 'finished' AND recipe_path IS NOT NULL AND outcome != ?"
            " ORDER BY started_at DESC, id DESC",
            (Outcome.MISSED.value,),
        )
        latest: dict[str, RunRow] = {}
        for r in rows:
            latest.setdefault(r["recipe_path"], _row(r))
        return latest

    def record_missed(self, schedule: str, due_at: datetime, note: str) -> int:
        when = iso(due_at)
        cursor = self._db.execute(
            "INSERT INTO runs (trigger, recipe_path, mode, status, outcome, started_at, finished_at,"
            " duration_seconds, note) VALUES (?, NULL, ?, 'finished', ?, ?, ?, 0, ?)",
            (f"schedule:{schedule}", Mode.UNATTENDED.value, Outcome.MISSED.value, when, when, note),
        )
        assert cursor.lastrowid is not None  # noqa: S101  # nosec B101  # an INSERT always sets it
        return cursor.lastrowid

    def mark_orphans(self, now: datetime) -> int:
        rows = self._db.query("SELECT id FROM runs WHERE status = 'running'")
        for r in rows:
            self.finish(r["id"], outcome=Outcome.FAILED, exit_code=None, finished_at=now, note=ORPHAN_NOTE)
            self.set_pid(r["id"], None)
        return len(rows)

    def export_csv(self, f: HistoryFilter, out: TextIO) -> int:
        where, params = _where(f)
        rows = self._db.query(f"SELECT * FROM runs{where} ORDER BY started_at ASC, id ASC", params)  # noqa: S608  # nosec B608  # fixed clauses, values bound
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for r in map(_row, rows):
            writer.writerow(
                [
                    r.id,
                    r.started_at,
                    r.finished_at,
                    r.trigger,
                    r.recipe_path,
                    r.mode.value,
                    None if r.outcome is None else r.outcome.value,
                    r.exit_code,
                    r.duration_seconds,
                    r.commit_sha,
                    r.token_source,
                    r.log_path,
                    r.note,
                ]
            )
        return len(rows)

    def prune_logs(self, paths: DataPaths, retention_days: int, now: datetime) -> int:
        cutoff = (now - timedelta(days=retention_days)).date()
        removed = 0
        if paths.logs.is_dir():
            for folder in paths.logs.iterdir():
                try:
                    day = date.fromisoformat(folder.name)
                except ValueError:
                    continue
                if folder.is_dir() and day < cutoff:
                    shutil.rmtree(folder, ignore_errors=True)
                    removed += 1
        venv_logs = paths.logs / "venv"
        if venv_logs.is_dir():
            limit = (now - timedelta(days=retention_days)).timestamp()
            for log in venv_logs.glob("*.log"):
                if log.is_file() and log.stat().st_mtime < limit:
                    log.unlink()
                    removed += 1
        return removed
