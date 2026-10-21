from datetime import UTC, datetime, timedelta
from pathlib import Path

from cockpit_factory import make_cockpit
from webclient import logged_in

from privacyfence_cockpit.jobs import JobKind
from privacyfence_cockpit.models import Mode, Outcome

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)

YAML = """\
version: 1
schedules:
  - name: daily
    cron: "0 3 * * *"
    run: [imports]
    repeat: 2
    stop_on_error: true
  - name: weekly
    cron: "30 6 * * 1"
    run: [imports/10-sf]
"""


def write_repo(root: Path, text: str | None = YAML) -> None:
    repo = root / "data" / "repo"
    (repo / "workflows" / "10-imports" / "10-sf").mkdir(parents=True)
    (repo / "workflows" / "10-imports" / "10-sf" / "recipe.yaml").write_text("steps: []\n")
    if text is not None:
        (repo / "cockpit.yaml").write_text(text)


def setup(tmp_path: Path, text: str | None = YAML):
    cockpit = make_cockpit(tmp_path, clock=lambda: NOW)
    write_repo(tmp_path, text)
    cockpit.db.set_setting("timezone", "Europe/Budapest")
    c, token = logged_in(cockpit)
    return cockpit, c, token


def test_lists_schedules_with_next_fire_in_the_effective_timezone(tmp_path: Path) -> None:
    _, c, _ = setup(tmp_path)
    page = c.get("/schedules").text
    assert "daily" in page and "weekly" in page
    assert "0 3 * * *" in page and "Europe/Budapest" in page
    assert "imports/10-sf" in page
    assert "repeat 2×" in page and "stops on error" in page
    # 03:00 Budapest on 21 October (UTC+2) after 2026-10-20 10:00Z
    assert "2026-10-21 03:00 Europe/Budapest" in page
    assert "2026-10-26 06:30 Europe/Budapest" in page
    assert "active" in page and "paused" not in page
    assert "never" in page


def test_pause_and_resume_persist_in_schedule_state(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    response = c.post("/schedules/daily/pause", data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/schedules"
    assert cockpit.scheduler.is_paused("daily")
    assert not cockpit.scheduler.is_paused("weekly")
    row = cockpit.db.query_one("SELECT paused FROM schedule_state WHERE name = 'daily'")
    assert row is not None and row["paused"] == 1
    page = c.get("/schedules").text
    assert "paused" in page and "Resume" in page
    assert "2026-10-21 03:00" not in page  # a paused schedule has no next run
    c.post("/schedules/daily/resume", data={"csrf_token": token}, follow_redirects=False)
    assert not cockpit.scheduler.is_paused("daily")
    assert "2026-10-21 03:00" in c.get("/schedules").text


def test_run_now_submits_a_manual_job_with_the_schedules_settings(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    response = c.post("/schedules/daily/run", data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    (job,) = cockpit.queue.queued()
    assert response.headers["location"] == f"/jobs/{job.id}"
    assert job.kind is JobKind.RUN
    assert job.trigger == "manual"
    assert job.selectors == ("imports",)
    assert job.repeat == 2
    assert job.stop_on_error is True
    assert job.mode is Mode.UNATTENDED


def test_unknown_schedule_is_404(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    for action in ("pause", "resume", "run"):
        response = c.post(f"/schedules/nope/{action}", data={"csrf_token": token}, follow_redirects=False)
        assert response.status_code == 404, action
    assert cockpit.queue.queued() == []


def test_a_cockpit_yaml_error_is_shown_instead_of_the_table(tmp_path: Path) -> None:
    _, c, token = setup(tmp_path, "version: 2\n")
    page = c.get("/schedules").text
    assert "cockpit.yaml: " in page
    assert '<table class="data">' not in page.split("Missed runs")[0]
    assert "Run now" not in page
    assert c.post("/schedules/daily/run", data={"csrf_token": token}).status_code == 404


def test_no_schedules(tmp_path: Path) -> None:
    _, c, _ = setup(tmp_path, None)
    assert "No schedules" in c.get("/schedules").text


def test_shows_the_last_run_of_a_schedule(tmp_path: Path) -> None:
    cockpit, c, _ = setup(tmp_path)
    run_id = cockpit.history.start(
        job_id="j",
        trigger="schedule:daily",
        recipe_path="imports/10-sf",
        mode=Mode.UNATTENDED,
        started_at=NOW,
        log_path=None,
        commit_sha=None,
        token_source=None,
    )
    cockpit.history.finish(run_id, outcome=Outcome.FAILED, exit_code=2, finished_at=NOW)
    page = c.get("/schedules").text
    assert "chip-failed" in page
    assert f'href="/runs/{run_id}"' in page


def test_lists_only_the_twenty_newest_missed_rows(tmp_path: Path) -> None:
    cockpit, c, _ = setup(tmp_path)
    for i in range(22):
        cockpit.history.record_missed("daily", NOW - timedelta(days=30 - i), f"note-{i:02d}")
    page = c.get("/schedules").text
    assert "note-21" in page and "note-02" in page
    assert "note-01" not in page and "note-00" not in page
