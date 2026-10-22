from datetime import UTC, date, datetime, timedelta

import pytest

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.history import ORPHAN_NOTE, History, HistoryFilter
from privacyfence_cockpit.models import Mode, Outcome

T0 = datetime(2026, 10, 20, 6, 0, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "cockpit.db")
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def history(db):
    return History(db)


def start(history, *, recipe="imports/a", at=T0, trigger="manual", mode=Mode.UNATTENDED, job_id=None, branch=None):
    return history.start(
        job_id=job_id,
        trigger=trigger,
        recipe_path=recipe,
        mode=mode,
        started_at=at,
        log_path=None,
        commit_sha="abc",
        token_source="default",
        branch=branch,
    )


def test_start_finish_and_duration(history):
    run_id = start(history)
    row = history.get(run_id)
    assert row.status == "running"
    assert row.outcome is None
    assert row.mode is Mode.UNATTENDED
    assert row.commit_sha == "abc"
    assert row.token_source == "default"
    assert row.stop_requested is False
    history.finish(run_id, outcome=Outcome.OK, exit_code=0, finished_at=T0 + timedelta(seconds=42), note="n")
    row = history.get(run_id)
    assert (row.status, row.outcome, row.exit_code, row.note) == ("finished", Outcome.OK, 0, "n")
    assert row.finished_at == "2026-10-20T06:00:42Z"
    assert row.duration_seconds == 42.0


def test_finish_unknown_run_is_a_noop(history):
    history.finish(99, outcome=Outcome.OK, exit_code=0, finished_at=T0)
    assert history.get(99) is None


def test_set_pid(history):
    run_id = start(history)
    history.set_pid(run_id, 123)
    assert history.get(run_id).pid == 123
    history.set_pid(run_id, None)
    assert history.get(run_id).pid is None


def test_query_filters(history):
    a = start(history, recipe="g/a", at=T0, trigger="manual", mode=Mode.UNATTENDED, job_id="j1")
    history.finish(a, outcome=Outcome.OK, exit_code=0, finished_at=T0)
    b = start(history, recipe="g/b", at=T0 + timedelta(days=1), trigger="schedule:nightly", mode=Mode.CHECK)
    history.finish(b, outcome=Outcome.FAILED, exit_code=2, finished_at=T0)
    c = start(history, recipe="g/a", at=T0 + timedelta(days=2), trigger="manual", mode=Mode.ATTENDED, job_id="j2")

    def ids(**kw):
        return [r.id for r in history.query(HistoryFilter(**kw))]

    assert ids() == [c, b, a]
    assert ids(recipe="g/a") == [c, a]
    assert ids(outcome=Outcome.FAILED) == [b]
    assert ids(trigger="schedule:nightly") == [b]
    assert ids(trigger="schedule") == []
    assert ids(mode=Mode.ATTENDED) == [c]
    assert ids(since=date(2026, 10, 21)) == [c, b]
    assert ids(until=date(2026, 10, 21)) == [b, a]
    assert ids(since=date(2026, 10, 21), until=date(2026, 10, 21)) == [b]
    assert ids(job_id="j1") == [a]
    assert ids(recipe="g/a", mode=Mode.ATTENDED, since=date(2026, 10, 22), job_id="j2") == [c]
    assert ids(recipe="g/b", mode=Mode.ATTENDED) == []


def test_order_and_pagination(history):
    first = start(history, at=T0)
    second = start(history, at=T0)  # same instant: id breaks the tie
    third = start(history, at=T0 + timedelta(seconds=1))
    f = HistoryFilter()
    assert [r.id for r in history.query(f)] == [third, second, first]
    assert [r.id for r in history.query(f, limit=2)] == [third, second]
    assert [r.id for r in history.query(f, limit=2, offset=2)] == [first]
    assert history.count(f) == 3
    assert history.count(HistoryFilter(recipe="nope")) == 0


def test_last_by_recipe_ignores_running_and_missed(history):
    old = start(history, recipe="g/a", at=T0)
    history.finish(old, outcome=Outcome.FAILED, exit_code=2, finished_at=T0)
    new = start(history, recipe="g/a", at=T0 + timedelta(hours=1))
    history.finish(new, outcome=Outcome.OK, exit_code=0, finished_at=T0)
    start(history, recipe="g/a", at=T0 + timedelta(hours=2))  # still running
    start(history, recipe="g/only-running", at=T0)
    history.record_missed("nightly", T0 + timedelta(hours=3), "cockpit was down")
    last = history.last_by_recipe()
    assert set(last) == {"g/a"}
    assert last["g/a"].id == new


def test_start_records_the_branch(history):
    assert history.get(start(history)).branch is None
    assert history.get(start(history, branch="feature/x")).branch == "feature/x"


def test_last_by_recipe_ignores_branch_runs(history):
    configured = start(history, recipe="g/a", at=T0)
    history.finish(configured, outcome=Outcome.OK, exit_code=0, finished_at=T0)
    experiment = start(history, recipe="g/a", at=T0 + timedelta(hours=1), branch="feature/x")
    history.finish(experiment, outcome=Outcome.FAILED, exit_code=1, finished_at=T0)
    assert history.last_by_recipe()["g/a"].id == configured


def test_record_missed(history):
    run_id = history.record_missed("nightly", T0, "the cockpit was not running")
    row = history.get(run_id)
    assert row.recipe_path is None
    assert row.outcome is Outcome.MISSED
    assert row.status == "finished"
    assert row.trigger == "schedule:nightly"
    assert row.note == "the cockpit was not running"
    assert row.started_at == row.finished_at == "2026-10-20T06:00:00Z"


def test_request_stop(history):
    run_id = start(history)
    row = history.request_stop(run_id)
    assert row.id == run_id
    assert row.stop_requested is True
    assert history.get(run_id).stop_requested is True
    assert history.request_stop(999) is None


def test_request_stop_leaves_finished_rows_alone(history):
    run_id = start(history)
    history.finish(run_id, outcome=Outcome.OK, exit_code=0, finished_at=T0)
    assert history.request_stop(run_id).stop_requested is False


def test_mark_orphans(history):
    running = start(history)
    history.set_pid(running, 4242)
    done = start(history, at=T0 + timedelta(seconds=1))
    history.finish(done, outcome=Outcome.OK, exit_code=0, finished_at=T0)
    now = T0 + timedelta(minutes=5)
    assert history.mark_orphans(now) == 1
    row = history.get(running)
    assert (row.status, row.outcome, row.note, row.pid) == ("finished", Outcome.FAILED, ORPHAN_NOTE, None)
    assert row.note == "the cockpit stopped during this run"
    assert row.duration_seconds == 300.0
    assert history.get(done).outcome is Outcome.OK
    assert history.mark_orphans(now) == 0


def test_export_csv(history):
    import io

    a = start(history, recipe="imports/a", at=T0 + timedelta(hours=1))
    b = start(history, recipe="imports/b", at=T0)
    history.finish(a, outcome=Outcome.OK, exit_code=0, finished_at=T0 + timedelta(hours=1, seconds=5), note="n, 1")
    out = io.StringIO()
    assert history.export_csv(HistoryFilter(), out) == 2
    lines = out.getvalue().splitlines()
    assert lines[0] == (
        "id,started_at,finished_at,trigger,recipe,mode,outcome,exit_code,duration_seconds,commit,"
        "token_source,log_path,note,branch"
    )
    assert lines[1].startswith(f"{b},2026-10-20T06:00:00Z,,manual,imports/b,unattended,")
    assert lines[2] == (
        f'{a},2026-10-20T07:00:00Z,2026-10-20T07:00:05Z,manual,imports/a,unattended,ok,0,5.0,abc,default,,"n, 1",'
    )
    only = io.StringIO()
    assert history.export_csv(HistoryFilter(recipe="imports/b"), only) == 1


def test_set_log_path(history):
    run = start(history)
    history.set_log_path(run, "logs/x.log")
    assert history.get(run).log_path == "logs/x.log"


def test_prune_logs(history, tmp_path):
    import os

    from privacyfence_cockpit.paths import DataPaths

    paths = DataPaths(tmp_path / "data").ensure()
    now = datetime(2026, 10, 20, tzinfo=UTC)
    for day in ("2026-07-01", "2026-07-22", "2026-10-19"):
        (paths.logs / day).mkdir()
        (paths.logs / day / "x.log").write_text("x")
    (paths.logs / "notes").mkdir()
    (paths.logs / "stray.txt").write_text("x")
    venv_logs = paths.logs / "venv"
    venv_logs.mkdir()
    old, new = venv_logs / "old.log", venv_logs / "new.log"
    old.write_text("x")
    new.write_text("x")
    stamp = (now - timedelta(days=200)).timestamp()
    os.utime(old, (stamp, stamp))
    os.utime(new, ((now - timedelta(days=1)).timestamp(),) * 2)
    assert history.prune_logs(paths, 90, now) == 2  # 2026-07-01 and old.log; 2026-07-22 is exactly at the cutoff
    assert not (paths.logs / "2026-07-01").exists()
    assert (paths.logs / "2026-07-22").exists()
    assert (paths.logs / "2026-10-19").exists()
    assert (paths.logs / "notes").exists() and (paths.logs / "stray.txt").exists()
    assert new.exists() and not old.exists()
