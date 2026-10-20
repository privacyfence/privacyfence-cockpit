import csv
import io
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cockpit_factory import make_cockpit
from webclient import logged_in

from privacyfence_cockpit.history import CSV_COLUMNS
from privacyfence_cockpit.jobs import Job, JobKind
from privacyfence_cockpit.models import Mode, Outcome, iso
from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web import routes_runs

NOW = datetime(2026, 10, 20, 6, 15, 7, tzinfo=UTC)
SECRET = "pfst_" + "s" * 30


def add_recipe(cockpit: Cockpit, group: str, folder: str, extra: str = "") -> None:
    folder_path = cockpit.paths.repo / "workflows" / group / folder
    folder_path.mkdir(parents=True)
    (folder_path / "recipe.yaml").write_text(f"version: 1\nname: {folder}\nsteps: []\n{extra}")


@pytest.fixture
def cockpit(tmp_path: Path) -> Cockpit:
    c = make_cockpit(tmp_path, clock=lambda: NOW)
    add_recipe(c, "20-exports", "10-b")
    add_recipe(c, "10-imports", "20-second")
    add_recipe(c, "10-imports", "10-first", "server: https://other.example/mcp\n")
    return c


def start_run(
    cockpit: Cockpit,
    *,
    recipe: str | None = "imports/10-first",
    mode: Mode = Mode.UNATTENDED,
    trigger: str = "manual",
    job_id: str | None = None,
    started: datetime = NOW,
    outcome: Outcome | None = Outcome.OK,
    log: str | None = "line one\nline two\n",
    source: str | None = "default",
) -> int:
    run_id = cockpit.history.start(
        job_id=job_id,
        trigger=trigger,
        recipe_path=recipe,
        mode=mode,
        started_at=started,
        log_path=None,
        commit_sha="a" * 40,
        token_source=source,
    )
    if log is not None:
        rel = f"logs/{started:%Y-%m-%d}/run_{run_id}.log"
        (cockpit.paths.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (cockpit.paths.root / rel).write_text(log)
        cockpit.history.set_log_path(run_id, rel)
    if outcome is not None:
        cockpit.history.finish(run_id, outcome=outcome, exit_code=0, finished_at=started + timedelta(seconds=5))
    return run_id


def events(body: str) -> list[tuple[str, str]]:
    found = []
    for block in body.strip().split("\n\n"):
        if block.startswith(":"):
            found.append(("ping", ""))
            continue
        name, data = block.split("\n")
        found.append((name.removeprefix("event: "), data.removeprefix("data: ")))
    return found


def test_overview_lists_groups_in_run_order_with_last_run_and_next_fire(cockpit: Cockpit) -> None:
    (cockpit.paths.repo / "cockpit.yaml").write_text(
        "version: 1\ntimezone: UTC\nschedules:\n"
        "  - {name: nightly, cron: '0 3 * * *', run: [imports]}\n"
        "  - {name: paused-one, cron: '0 4 * * *', run: [imports]}\n"
    )
    cockpit.scheduler.set_paused("paused-one", True)
    run_id = start_run(cockpit, outcome=Outcome.DENIED)
    c, _ = logged_in(cockpit)
    page = c.get("/").text
    assert page.index("imports") < page.index("exports")
    assert page.index("10-first") < page.index("20-second") < page.index("10-b")
    assert f'href="/runs/{run_id}"' in page
    assert "chip-denied" in page
    assert "2026-10-21 03:00 UTC" in page
    assert "04:00" not in page
    assert "server: https://other.example/mcp" in page
    assert page.count('name="recipe" value="imports/10-first"') == 2  # checkbox and Run button
    assert 'value="preflight"' in page
    assert "Nothing is running." in page


def test_overview_shows_queue(cockpit: Cockpit) -> None:
    cockpit.queue.submit(Job(JobKind.RUN, "manual", selectors=("imports",)))
    cockpit.queue.submit(Job(JobKind.GIT_UPDATE, "manual"))
    cockpit.queue.submit(Job(JobKind.RUN, "manual"))
    c, _ = logged_in(cockpit)
    assert "3 queued" in c.get("/").text


def test_overview_names_the_running_job(cockpit: Cockpit) -> None:
    import asyncio

    cockpit.queue.submit(Job(JobKind.RUN, "manual", selectors=("imports",)))
    cockpit.queue.submit(Job(JobKind.GIT_UPDATE, "manual"))
    running = asyncio.run(cockpit.queue.next())
    c, _ = logged_in(cockpit)
    page = c.get("/").text
    assert f'href="/jobs/{running.id}"' in page
    assert "imports (unattended, manual)" in page
    assert "1 queued" in page
    # A non-run job is described by its kind.
    asyncio.run(cockpit.queue.next())
    assert "git update" in c.get("/").text


def test_overview_without_recipes(tmp_path: Path) -> None:
    c, _ = logged_in(make_cockpit(tmp_path))
    assert "No recipes found." in c.get("/").text


def test_overview_survives_an_unreadable_recipe_server(cockpit: Cockpit) -> None:
    add_recipe(cockpit, "30-more", "10-c", "- broken: [\n")
    add_recipe(cockpit, "30-more", "20-d")
    (cockpit.paths.repo / "workflows/30-more/20-d/recipe.yaml").write_text("- a list\n")
    c, _ = logged_in(cockpit)
    page = c.get("/").text
    assert "10-c" in page and "20-d" in page


def test_post_run_without_a_recipe_flashes(cockpit: Cockpit) -> None:
    c, token = logged_in(cockpit)
    response = c.post("/run", data={"csrf_token": token, "mode": "check"}, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/")
    assert "Pick at least one recipe." in c.get("/").text
    assert cockpit.queue.queued() == []


def test_post_run_with_a_bad_mode_flashes(cockpit: Cockpit) -> None:
    c, token = logged_in(cockpit)
    c.post("/run", data={"csrf_token": token, "recipe": "imports", "mode": "nope"}, follow_redirects=False)
    assert "Pick a valid mode." in c.get("/").text
    assert cockpit.queue.queued() == []


def test_post_run_submits_one_job(cockpit: Cockpit) -> None:
    c, token = logged_in(cockpit)
    response = c.post(
        "/run",
        data={"csrf_token": token, "recipe": ["imports/10-first", "exports"], "mode": "preflight"},
        follow_redirects=False,
    )
    (job,) = cockpit.queue.queued()
    assert response.headers["location"] == f"/jobs/{job.id}"
    assert (job.kind, job.trigger, job.selectors, job.mode) == (
        JobKind.RUN,
        "manual",
        ("imports/10-first", "exports"),
        Mode.PREFLIGHT,
    )
    page = c.get(f"/jobs/{job.id}")
    assert "Queued: it starts after the current run." in page.text
    assert f'hx-get="/jobs/{job.id}/rows"' in page.text


def test_post_run_defaults_to_unattended(cockpit: Cockpit) -> None:
    c, token = logged_in(cockpit)
    c.post("/run", data={"csrf_token": token, "recipe": "imports"}, follow_redirects=False)
    assert cockpit.queue.queued()[0].mode is Mode.UNATTENDED


def test_job_rows_fragment_and_polling_stops_when_finished(cockpit: Cockpit) -> None:
    job = Job(JobKind.RUN, "manual", selectors=("imports",))
    cockpit.queue.submit(job)
    c, _ = logged_in(cockpit)
    running_id = start_run(cockpit, job_id=job.id, outcome=None)
    fragment = c.get(f"/jobs/{job.id}/rows").text
    assert "<html" not in fragment
    assert f'href="/runs/{running_id}"' in fragment
    assert 'hx-trigger="every 2s"' in fragment
    assert "running" in fragment

    cockpit.queue.cancel(job.id)
    cockpit.history.finish(running_id, outcome=Outcome.OK, exit_code=0, finished_at=NOW + timedelta(seconds=3))
    fragment = c.get(f"/jobs/{job.id}/rows").text
    assert "hx-trigger" not in fragment
    assert "chip-ok" in fragment
    assert "3.0 s" in fragment


def test_job_page_for_a_job_that_has_not_started_its_run(cockpit: Cockpit) -> None:
    import asyncio

    job = Job(JobKind.RUN, "manual", selectors=("imports",))
    cockpit.queue.submit(job)
    asyncio.run(cockpit.queue.next())
    c, _ = logged_in(cockpit)
    assert "Starting…" in c.get(f"/jobs/{job.id}").text


def test_unknown_job_is_404(cockpit: Cockpit) -> None:
    c, _ = logged_in(cockpit)
    assert c.get("/jobs/nope").status_code == 404
    assert c.get("/jobs/nope/rows").status_code == 404


def test_cancel_a_queued_job(cockpit: Cockpit) -> None:
    job = Job(JobKind.RUN, "manual", selectors=("imports",))
    cockpit.queue.submit(job)
    c, token = logged_in(cockpit)
    response = c.post(f"/jobs/{job.id}/cancel", data={"csrf_token": token}, follow_redirects=False)
    assert response.headers["location"] == f"/jobs/{job.id}"
    assert cockpit.queue.queued() == []
    # Cancelling again: it is gone from the queue, and says so.
    c.post(f"/jobs/{job.id}/cancel", data={"csrf_token": token}, follow_redirects=False)
    assert "The job has already started." in c.get("/").text


def test_run_page_shows_facts_and_never_a_token(cockpit: Cockpit) -> None:
    cockpit.store.set("token", "default", SECRET)
    run_id = start_run(cockpit, source="default")
    c, _ = logged_in(cockpit)
    page = c.get(f"/runs/{run_id}")
    assert page.status_code == 200
    expected_texts = ("imports/10-first", "unattended", "manual", "chip-ok", "2026-10-20 06:15 UTC", "aaaaaaa")
    for expected in (*expected_texts, "default"):
        assert expected in page.text
    assert "Token source" in page.text
    assert SECRET not in page.text
    assert f'data-events="/runs/{run_id}/events"' in page.text
    assert f'href="/runs/{run_id}/log"' in page.text
    assert "Stop</button>" not in page.text


def test_run_page_has_stop_while_running(cockpit: Cockpit) -> None:
    run_id = start_run(cockpit, outcome=None)
    c, _ = logged_in(cockpit)
    page = c.get(f"/runs/{run_id}").text
    assert f'action="/runs/{run_id}/stop"' in page
    assert "running" in page


def test_unknown_run_is_404(cockpit: Cockpit) -> None:
    c, _ = logged_in(cockpit)
    for path in ("/runs/99", "/runs/99/events", "/runs/99/log"):
        assert c.get(path).status_code == 404, path
    assert c.get("/runs/abc").status_code == 404


def test_events_stream_of_a_finished_run(cockpit: Cockpit) -> None:
    run_id = start_run(cockpit, log="one\ntwo\n[s] waiting for approval: https://pf.example/a\nlast no newline")
    c, _ = logged_in(cockpit)
    response = c.get(f"/runs/{run_id}/events")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert events(response.text) == [
        ("line", '{"line": "one"}'),
        ("line", '{"line": "two"}'),
        ("line", '{"line": "[s] waiting for approval: https://pf.example/a"}'),
        ("line", '{"line": "last no newline"}'),
        ("end", '{"outcome": "ok"}'),
    ]


def test_events_stream_needs_a_login(cockpit: Cockpit) -> None:
    run_id = start_run(cockpit)
    from webclient import client

    response = client(cockpit).get(f"/runs/{run_id}/events", follow_redirects=False)
    assert response.status_code == 303


def test_events_stream_picks_up_appended_lines_and_pings(cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes_runs, "LOG_POLL_SECONDS", 0.01)
    monkeypatch.setattr(routes_runs, "PING_SECONDS", 0.0)
    run_id = start_run(cockpit, outcome=None, log="first\npartial")
    log_file = next((cockpit.paths.logs).rglob("*.log"))

    def writer() -> None:
        time.sleep(0.1)
        with log_file.open("a") as handle:
            handle.write(" line\nsecond\n")
        time.sleep(0.1)
        cockpit.history.finish(run_id, outcome=Outcome.FAILED, exit_code=2, finished_at=NOW + timedelta(seconds=1))

    c, _ = logged_in(cockpit)  # slow (scrypt): log in before the writer starts
    thread = threading.Thread(target=writer)
    thread.start()
    found = events(c.get(f"/runs/{run_id}/events").text)
    thread.join()
    assert [e for e in found if e[0] != "ping"] == [
        ("line", '{"line": "first"}'),
        ("line", '{"line": "partial line"}'),
        ("line", '{"line": "second"}'),
        ("end", '{"outcome": "failed"}'),
    ]
    assert ("ping", "") in found


def test_events_stream_waits_for_a_log_that_does_not_exist_yet(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes_runs, "LOG_POLL_SECONDS", 0.01)
    run_id = start_run(cockpit, outcome=None, log=None)

    def finish() -> None:
        time.sleep(0.1)
        cockpit.history.finish(run_id, outcome=Outcome.OK, exit_code=0, finished_at=NOW)

    c, _ = logged_in(cockpit)
    thread = threading.Thread(target=finish)
    thread.start()
    found = events(c.get(f"/runs/{run_id}/events").text)
    thread.join()
    assert found[-1] == ("end", '{"outcome": "ok"}')


def test_events_stream_for_a_deleted_log(cockpit: Cockpit) -> None:
    run_id = start_run(cockpit)
    for log in cockpit.paths.logs.rglob("*.log"):
        log.unlink()
    c, _ = logged_in(cockpit)
    assert events(c.get(f"/runs/{run_id}/events").text) == [
        ("line", '{"line": "log deleted after the retention period"}'),
        ("end", '{"outcome": "ok"}'),
    ]


def test_events_stream_stops_if_the_row_disappears(cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes_runs, "LOG_POLL_SECONDS", 0.01)
    run_id = start_run(cockpit, outcome=None)

    def delete() -> None:
        time.sleep(0.1)
        cockpit.db.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    c, _ = logged_in(cockpit)
    thread = threading.Thread(target=delete)
    thread.start()
    found = events(c.get(f"/runs/{run_id}/events").text)
    thread.join()
    assert ("end", '{"outcome": "ok"}') not in found


def test_events_end_event_for_an_undecided_outcome_is_empty(cockpit: Cockpit) -> None:
    run_id = start_run(cockpit, outcome=None)
    cockpit.db.execute("UPDATE runs SET status = 'finished' WHERE id = ?", (run_id,))
    c, _ = logged_in(cockpit)
    assert events(c.get(f"/runs/{run_id}/events").text)[-1] == ("end", '{"outcome": ""}')


def test_log_download(cockpit: Cockpit) -> None:
    run_id = start_run(cockpit)
    c, _ = logged_in(cockpit)
    response = c.get(f"/runs/{run_id}/log")
    assert response.text == "line one\nline two\n"
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"] == f'attachment; filename="run-{run_id}.log"'
    for log in cockpit.paths.logs.rglob("*.log"):
        log.unlink()
    assert c.get(f"/runs/{run_id}/log").status_code == 404


def test_stop_calls_the_runner(cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = start_run(cockpit, outcome=None)
    calls: list[int] = []
    monkeypatch.setattr(cockpit.runner, "stop", lambda i: calls.append(i) or True)
    c, token = logged_in(cockpit)
    response = c.post(f"/runs/{run_id}/stop", data={"csrf_token": token}, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, f"/runs/{run_id}")
    assert calls == [run_id]
    assert "Stopping the run…" in c.get(f"/runs/{run_id}").text


def test_stop_of_a_finished_run_says_so(cockpit: Cockpit) -> None:
    run_id = start_run(cockpit)
    c, token = logged_in(cockpit)
    c.post(f"/runs/{run_id}/stop", data={"csrf_token": token}, follow_redirects=False)
    assert "That run is not running." in c.get(f"/runs/{run_id}").text
    assert c.post("/runs/99/stop", data={"csrf_token": token}).status_code == 404


def history_ids(c, query: str = "") -> list[str]:
    import re

    return re.findall(r'href="/runs/(\d+)"', c.get(f"/history{query}").text)


@pytest.fixture
def populated(cockpit: Cockpit) -> dict[str, int]:
    ids = {
        "a": start_run(cockpit, recipe="imports/10-first", started=NOW - timedelta(days=3), outcome=Outcome.OK),
        "b": start_run(
            cockpit,
            recipe="imports/20-second",
            started=NOW - timedelta(days=2),
            outcome=Outcome.FAILED,
            trigger="schedule:nightly",
        ),
        "c": start_run(
            cockpit, recipe="exports/10-b", started=NOW - timedelta(days=1), outcome=Outcome.OK, mode=Mode.CHECK
        ),
    }
    cockpit.history.record_missed("nightly", NOW - timedelta(hours=1), "2 run(s) were missed")
    return ids


def test_history_lists_newest_first_with_missed_rows(cockpit: Cockpit, populated: dict[str, int]) -> None:
    c, _ = logged_in(cockpit)
    page = c.get("/history").text
    assert history_ids(c) == [str(populated[k]) for k in "cba"]
    assert "2 run(s) were missed" in page
    assert "—" in page
    assert "chip-missed" in page
    assert "4 run(s)" in page


@pytest.mark.parametrize(
    ("query", "keys"),
    [
        ("?recipe=imports/20-second", "b"),
        ("?outcome=failed", "b"),
        ("?outcome=ok", "ac"),
        ("?trigger=schedule:nightly", "b"),
        ("?mode=check", "c"),
        (f"?since={(NOW - timedelta(days=2)).date()}", "cb"),
        (f"?until={(NOW - timedelta(days=2)).date()}", "ba"),
        (f"?since={(NOW - timedelta(days=2)).date()}&until={(NOW - timedelta(days=2)).date()}", "b"),
    ],
)
def test_history_filters_narrow_the_rows(
    cockpit: Cockpit, populated: dict[str, int], query: str, keys: str
) -> None:
    c, _ = logged_in(cockpit)
    assert history_ids(c, query) == [str(populated[k]) for k in sorted(keys, reverse=True)]


def test_history_ignores_invalid_values(cockpit: Cockpit, populated: dict[str, int]) -> None:
    c, _ = logged_in(cockpit)
    query = "?outcome=bogus&mode=nope&since=yesterday&until=2026-13-45&page=x&recipe=&trigger="
    assert len(history_ids(c, query)) == 3
    assert len(history_ids(c, "?since=2026-02-30")) == 3


def test_history_pagination(cockpit: Cockpit) -> None:
    ids = [start_run(cockpit, started=NOW + timedelta(minutes=i), log=None) for i in range(120)]
    c, _ = logged_in(cockpit)
    first = c.get("/history?outcome=ok")
    assert history_ids(c, "?outcome=ok") == [str(i) for i in reversed(ids[70:])]
    assert "Page 1 of 3" in first.text
    assert 'href="/history?outcome=ok&amp;page=2"' in first.text or "outcome=ok&page=2" in first.text
    assert history_ids(c, "?outcome=ok&page=2") == [str(i) for i in reversed(ids[20:70])]
    assert history_ids(c, "?outcome=ok&page=3") == [str(i) for i in reversed(ids[:20])]
    assert history_ids(c, "?outcome=ok&page=99") == history_ids(c, "?outcome=ok&page=3")
    assert history_ids(c, "?page=0") == history_ids(c, "?page=1")
    assert "Previous" in c.get("/history?page=2").text


def test_history_without_runs(cockpit: Cockpit) -> None:
    c, _ = logged_in(cockpit)
    assert "No runs match." in c.get("/history").text


def test_csv_export_uses_the_same_filters(cockpit: Cockpit, populated: dict[str, int]) -> None:
    c, _ = logged_in(cockpit)
    page = c.get("/history?outcome=ok").text
    assert "/history/runs.csv?outcome=ok" in page
    response = c.get("/history/runs.csv?outcome=ok")
    assert response.headers["content-disposition"] == 'attachment; filename="runs.csv"'
    assert response.headers["content-type"].startswith("text/csv")
    reader = csv.reader(io.StringIO(response.text))
    assert tuple(next(reader)) == CSV_COLUMNS
    assert [row[0] for row in reader] == [str(populated["a"]), str(populated["c"])]
    everything = list(csv.reader(io.StringIO(c.get("/history/runs.csv").text)))
    assert len(everything) == 5


def test_iso_is_what_the_overview_expects() -> None:
    assert iso(NOW) == "2026-10-20T06:15:07Z"
