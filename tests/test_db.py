import sqlite3

import pytest

from privacyfence_cockpit.db import _SCHEMA_V1, _SCHEMA_V2, Database, DatabaseError

TABLES = {
    "settings",
    "runs",
    "schedule_state",
    "ui_schedules",
    "sessions",
    "token_status",
    "notices_sent",
    "webhooks",
    "permissions_reports",
}
INDEXES = {"runs_recipe_started", "runs_started", "runs_job"}


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "cockpit.db")
    database.migrate()
    yield database
    database.close()


def test_migrate_creates_schema_version_3(db):
    assert db.query_one("PRAGMA user_version")[0] == 3
    names = {r["name"]: r["type"] for r in db.query("SELECT name, type FROM sqlite_master")}
    assert TABLES <= {n for n, t in names.items() if t == "table"}
    assert INDEXES <= {n for n, t in names.items() if t == "index"}


def test_migrate_twice_is_a_noop(db):
    db.set_setting("a", "1")
    db.migrate()
    assert db.get_setting("a") == "1"
    assert db.query_one("PRAGMA user_version")[0] == 3


def test_a_version_1_database_is_upgraded_keeping_its_data(tmp_path):
    path = tmp_path / "cockpit.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_V1)
    conn.executescript("INSERT INTO settings VALUES ('a', '1'); PRAGMA user_version = 1;")
    conn.close()
    database = Database(path)
    database.migrate()
    assert database.query_one("PRAGMA user_version")[0] == 3
    assert database.get_setting("a") == "1"
    assert database.query("SELECT * FROM ui_schedules") == []
    database.close()


def test_a_version_2_database_gets_the_branch_column(tmp_path):
    path = tmp_path / "cockpit.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_V1)
    conn.executescript(_SCHEMA_V2)
    conn.executescript(
        "INSERT INTO runs (trigger, mode, status, started_at) VALUES ('manual', 'unattended', 'running', 't');"
        " PRAGMA user_version = 2;"
    )
    conn.close()
    database = Database(path)
    database.migrate()
    assert database.query_one("PRAGMA user_version")[0] == 3
    assert database.query_one("SELECT branch FROM runs")["branch"] is None
    database.execute(
        "INSERT INTO runs (trigger, mode, status, started_at, branch)"
        " VALUES ('manual', 'unattended', 'running', 't', 'x')"
    )
    assert database.query_one("SELECT branch FROM runs WHERE branch IS NOT NULL")["branch"] == "x"
    database.close()


def test_pragmas(db):
    assert db.query_one("PRAGMA journal_mode")[0] == "wal"
    assert db.query_one("PRAGMA busy_timeout")[0] == 5000
    assert db.query_one("PRAGMA foreign_keys")[0] == 1


def test_settings_round_trip(db):
    assert db.get_setting("timezone") is None
    assert db.get_setting("timezone", "UTC") == "UTC"
    db.set_setting("timezone", "Europe/Budapest")
    db.set_setting("timezone", "UTC")
    assert db.get_setting("timezone", "x") == "UTC"
    db.delete_setting("timezone")
    assert db.get_setting("timezone") is None
    db.delete_setting("timezone")


def test_execute_query_and_rows(db):
    cursor = db.execute(
        "INSERT INTO runs (trigger, mode, status, started_at) VALUES (?, ?, ?, ?)",
        ("manual", "attended", "running", "2026-10-20T06:15:00Z"),
    )
    assert cursor.lastrowid == 1
    rows = db.query("SELECT * FROM runs")
    assert len(rows) == 1
    assert rows[0]["stop_requested"] == 0
    assert db.query_one("SELECT * FROM runs WHERE id = ?", (99,)) is None


def test_newer_schema_raises(tmp_path):
    path = tmp_path / "cockpit.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 9")
    conn.close()
    database = Database(path)
    with pytest.raises(DatabaseError, match=r"cockpit.db was written by a newer cockpit \(schema 9\)"):
        database.migrate()
    database.close()


def test_two_databases_share_one_file(tmp_path):
    first = Database(tmp_path / "cockpit.db")
    first.migrate()
    second = Database(tmp_path / "cockpit.db")
    second.migrate()
    first.set_setting("k", "from-first")
    assert second.get_setting("k") == "from-first"
    second.set_setting("k", "from-second")
    assert first.get_setting("k") == "from-second"
    first.close()
    second.close()
