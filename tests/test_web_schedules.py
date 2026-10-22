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


def form(**overrides: str) -> dict[str, str]:
    data = {
        "name": "nightly",
        "cron": " 0   4 * * * ",
        "run": "imports\nimports/10-sf",
        "repeat": "3",
        "stop_on_error": "1",
    }
    return {**data, **overrides}


def test_create_a_schedule_in_the_dashboard(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    assert "New schedule" in c.get("/schedules/new").text
    response = c.post("/schedules/new", data={"csrf_token": token, **form()}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/schedules"
    stored = cockpit.schedule_store.get("nightly")
    assert stored is not None
    assert (stored.cron, stored.run, stored.repeat, stored.stop_on_error) == (
        "0 4 * * *",
        ("imports", "imports/10-sf"),
        3,
        True,
    )
    page = c.get("/schedules").text
    assert "nightly" in page and "dashboard" in page and "2026-10-21 04:00 Europe/Budapest" in page
    assert "/schedules/nightly/edit" in page and "/schedules/nightly/delete" in page
    assert "/schedules/daily/edit" not in page  # cockpit.yaml schedules are not editable here
    assert [s.name for s in cockpit.config().schedules] == ["daily", "weekly", "nightly"]


def test_a_dashboard_schedule_fires_like_any_other(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    c.post("/schedules/new", data={"csrf_token": token, **form(cron="0 14 * * *")})  # 12:00Z in Budapest (UTC+2)
    cockpit.db.set_setting("scheduler.last_tick_at", "2026-10-20T10:00:00Z")
    jobs = cockpit.scheduler.tick()
    assert jobs == []  # 10:00Z -> 10:00Z, nothing due yet
    later = NOW + timedelta(hours=2, seconds=30)
    cockpit.scheduler._clock = lambda: later  # noqa: SLF001
    (job,) = cockpit.scheduler.tick()
    assert job.trigger == "schedule:nightly" and job.repeat == 3 and job.stop_on_error


def test_create_refuses_bad_input_and_keeps_the_form(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    cases = {
        "cron is not a valid cron expression": form(cron="nope"),
        "name must match": form(name="Bad Name"),
        "run must be a non-empty list": form(run="  \n "),
        "repeat must be a number between 1 and 100": form(repeat="abc"),
        "already exists": form(name="daily"),
        "No recipe or group matches": form(run="typo"),
    }
    for message, data in cases.items():
        response = c.post("/schedules/new", data={"csrf_token": token, **data})
        assert response.status_code == 400, message
        assert message in response.text, message
    assert "nope" in c.post("/schedules/new", data={"csrf_token": token, **form(cron="nope")}).text
    assert cockpit.schedule_store.all() == ()


def test_edit_and_delete_a_dashboard_schedule(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    c.post("/schedules/new", data={"csrf_token": token, **form()})
    assert "readonly" in c.get("/schedules/nightly/edit").text
    # the name field cannot be used to rename or to overwrite another schedule
    response = c.post(
        "/schedules/nightly/edit",
        data={"csrf_token": token, **form(name="daily", cron="5 5 * * *", repeat="1", stop_on_error="")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    stored = cockpit.schedule_store.get("nightly")
    assert stored is not None and stored.cron == "5 5 * * *" and stored.repeat == 1 and not stored.stop_on_error
    assert cockpit.schedule_store.get("daily") is None
    bad = c.post("/schedules/nightly/edit", data={"csrf_token": token, **form(cron="x")})
    assert bad.status_code == 400 and "readonly" in bad.text
    cockpit.scheduler.set_paused("nightly", True)
    assert c.post("/schedules/nightly/delete", data={"csrf_token": token}, follow_redirects=False).status_code == 303
    assert cockpit.schedule_store.all() == ()
    assert not cockpit.scheduler.is_paused("nightly")


def test_schedules_of_cockpit_yaml_and_unknown_ones_cannot_be_edited_or_deleted(tmp_path: Path) -> None:
    _, c, token = setup(tmp_path)
    for name in ("daily", "nope"):
        assert c.get(f"/schedules/{name}/edit").status_code == 404
        assert c.post(f"/schedules/{name}/edit", data={"csrf_token": token, **form()}).status_code == 404
        assert c.post(f"/schedules/{name}/delete", data={"csrf_token": token}).status_code == 404


def test_the_schedule_forms_need_the_csrf_token(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    c.post("/schedules/new", data={"csrf_token": token, **form()})
    for path in ("/schedules/new", "/schedules/nightly/edit", "/schedules/nightly/delete"):
        assert c.post(path, data=form()).status_code == 403, path
    assert cockpit.schedule_store.get("nightly") is not None


def test_a_dashboard_schedule_with_the_name_of_a_yaml_schedule_is_ignored_and_reported(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path, None)
    c.post("/schedules/new", data={"csrf_token": token, **form(name="smoke")})
    (tmp_path / "data" / "repo" / "cockpit.yaml").write_text(
        'version: 1\nschedules:\n  - name: smoke\n    cron: "1 1 * * *"\n    run: [imports]\n'
    )
    (only,) = cockpit.config().schedules
    assert only.source == "repo" and only.cron == "1 1 * * *"
    page = c.get("/schedules").text
    assert "smoke are not used" in page
