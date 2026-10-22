"""SQLite storage: one shared connection, the schema and the settings table."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 3

_SCHEMA_V1 = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  trigger TEXT NOT NULL,
  recipe_path TEXT,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  outcome TEXT,
  exit_code INTEGER,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  duration_seconds REAL,
  log_path TEXT,
  commit_sha TEXT,
  token_source TEXT,
  pid INTEGER,
  stop_requested INTEGER NOT NULL DEFAULT 0,
  note TEXT
);
CREATE INDEX runs_recipe_started ON runs(recipe_path, started_at);
CREATE INDEX runs_started ON runs(started_at);
CREATE INDEX runs_job ON runs(job_id);
CREATE TABLE schedule_state (name TEXT PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0, changed_at TEXT NOT NULL);
CREATE TABLE sessions (id_hash TEXT PRIMARY KEY, csrf_token TEXT NOT NULL, created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL);
CREATE TABLE token_status (name TEXT PRIMARY KEY, checked_at TEXT NOT NULL, state TEXT NOT NULL,
  label TEXT, expires_at TEXT, days_left INTEGER, error TEXT);
CREATE TABLE notices_sent (key TEXT PRIMARY KEY, sent_at TEXT NOT NULL);
CREATE TABLE webhooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, format TEXT NOT NULL,
  created_at TEXT NOT NULL, last_status TEXT, last_sent_at TEXT);
CREATE TABLE permissions_reports (recipe_path TEXT PRIMARY KEY, commit_sha TEXT, created_at TEXT NOT NULL,
  report_json TEXT, error TEXT);
"""

_SCHEMA_V2 = """
CREATE TABLE ui_schedules (name TEXT PRIMARY KEY, cron TEXT NOT NULL, run_json TEXT NOT NULL,
  repeat INTEGER NOT NULL DEFAULT 1, stop_on_error INTEGER NOT NULL DEFAULT 0, changed_at TEXT NOT NULL);
"""

_SCHEMA_V3 = "ALTER TABLE runs ADD COLUMN branch TEXT;"

_MIGRATIONS = {1: _SCHEMA_V1, 2: _SCHEMA_V2, 3: _SCHEMA_V3}


class DatabaseError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        # Autocommit, so every write is one statement and the CLI and the server can share the file.
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def migrate(self) -> None:
        with self._lock:
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise DatabaseError(f"cockpit.db was written by a newer cockpit (schema {version})")
            if version == SCHEMA_VERSION:
                return
            steps = "\n".join(_MIGRATIONS[v] for v in range(version + 1, SCHEMA_VERSION + 1))
            # executescript commits any open transaction first, so make this one atomic by hand.
            self._conn.executescript(f"BEGIN;\n{steps}\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()  # type: ignore[no-any-return]

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return default if row is None else str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def delete_setting(self, key: str) -> None:
        self.execute("DELETE FROM settings WHERE key = ?", (key,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
