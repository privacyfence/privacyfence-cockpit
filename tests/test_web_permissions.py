import json
import threading
import time
from pathlib import Path

from cockpit_factory import make_cockpit
from fakesync import install_fake_venv
from webclient import logged_in

from privacyfence_cockpit.permissions import NO_SUGGESTIONS, PermissionsReport
from privacyfence_cockpit.web import routes_permissions

REPORT = {
    "version": 1,
    "recipe": "r",
    "steps": [
        {"id": "s1", "tool": "drive_list_files", "status": "covered", "suggested_rules": []},
        {"id": "s2", "tool": "gmail_get_message", "status": "needs_rule", "suggested_rules": None},
        {
            "id": "s3",
            "tool": "drive_download_file",
            "status": "needs_rule",
            "suggested_rules": [
                {"group": "drive", "label": "Drive folder", "value": ["abc"], "value_known": True,
                 "verb": "read", "operation": "download"}
            ],
        },
    ],
    "needed": [{"group": "drive", "label": "Drive folder", "value": ["abc"], "verbs": ["read"]}],
    "tools": ["drive_list_files", "gmail_get_message"],
}


def setup(tmp_path: Path):
    cockpit = make_cockpit(tmp_path)
    repo = tmp_path / "data" / "repo" / "workflows"
    for group, folder in (("10-imports", "10-sf"), ("20-exports", "10-out")):
        (repo / group / folder).mkdir(parents=True)
        (repo / group / folder / "recipe.yaml").write_text("steps: []\n")
    c, token = logged_in(cockpit)
    return cockpit, c, token


def store(cockpit, path: str, data: dict | None, error: str | None = None) -> None:
    cockpit.db.execute(
        "INSERT INTO permissions_reports (recipe_path, commit_sha, created_at, report_json, error) "
        "VALUES (?, ?, ?, ?, ?)",
        (path, "a" * 40, "2026-10-20T10:00:00Z", None if data is None else json.dumps(data), error),
    )


def wait_idle(c) -> None:
    for _ in range(200):
        if "Refreshing…" not in c.get("/permissions").text:
            return
        time.sleep(0.02)
    raise AssertionError("refresh did not finish")


def test_empty_page_invites_a_refresh(tmp_path: Path) -> None:
    _, c, _ = setup(tmp_path)
    assert "No reports yet" in c.get("/permissions").text


def test_recipe_view_shows_rule_text_and_no_suggestions(tmp_path: Path) -> None:
    cockpit, c, _ = setup(tmp_path)
    store(cockpit, "imports/10-sf", REPORT)
    store(cockpit, "exports/10-out", None, "the recipe environment is not ready")
    page = c.get("/permissions").text
    assert "imports/10-sf" in page and "Drive folder = abc (read)" in page
    assert NO_SUGGESTIONS in page
    assert "error: the recipe environment is not ready" in page
    assert "2026-10-20 10:00 UTC" in page and "aaaaaaa" in page


def test_union_and_group_views(tmp_path: Path) -> None:
    cockpit, c, _ = setup(tmp_path)
    store(cockpit, "imports/10-sf", REPORT)
    store(cockpit, "exports/10-out", None, "boom")
    union = c.get("/permissions?view=union").text
    assert "All recipes" in union and "Drive folder = abc (read)   [imports/10-sf]" in union
    assert "Steps without suggestions" in union and "imports/10-sf [s2] gmail_get_message" in union
    assert "exports/10-out: boom" in union
    group = c.get("/permissions?view=group").text
    assert "Group imports" in group and "Group exports" in group
    assert c.get("/permissions?view=nonsense").status_code == 200


def test_uncollected_recipes_are_named(tmp_path: Path) -> None:
    cockpit, c, _ = setup(tmp_path)
    store(cockpit, "imports/10-sf", REPORT)
    assert "Not collected yet" in c.get("/permissions").text


def test_refresh_collects_every_recipe_and_stores_reports(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    install_fake_venv(cockpit.paths, cockpit.db, permissions_json=REPORT)
    with c:
        response = c.post("/permissions/refresh?view=union", data={"csrf_token": token}, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/permissions?view=union"
        wait_idle(c)
    rows = cockpit.db.query("SELECT * FROM permissions_reports ORDER BY recipe_path")
    assert [r["recipe_path"] for r in rows] == ["exports/10-out", "imports/10-sf"]
    assert json.loads(rows[0]["report_json"])["tools"] == REPORT["tools"]
    assert rows[0]["error"] is None
    assert "Drive folder = abc (read)" in c.get("/permissions").text


def test_refresh_without_a_venv_stores_errors(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    with c:
        c.post("/permissions/refresh", data={"csrf_token": token})
        wait_idle(c)
    rows = cockpit.db.query("SELECT * FROM permissions_reports")
    assert len(rows) == 2 and all(r["report_json"] is None for r in rows)
    assert "the recipe environment is not ready" in c.get("/permissions").text


def test_a_second_refresh_while_running_starts_no_task(tmp_path: Path, monkeypatch) -> None:
    cockpit, c, token = setup(tmp_path)
    release = threading.Event()
    calls: list[str] = []

    async def slow_collect(recipe, runner, **kwargs):
        calls.append(recipe.path)
        while not release.is_set():
            await routes_permissions.asyncio.sleep(0.01)
        return PermissionsReport(recipe.path, REPORT, None)

    monkeypatch.setattr(routes_permissions, "collect", slow_collect)
    with c:
        c.post("/permissions/refresh", data={"csrf_token": token})
        page = c.get("/permissions").text
        assert "Refreshing…" in page and 'hx-trigger="every 3s"' in page
        second = c.post("/permissions/refresh", data={"csrf_token": token}, follow_redirects=True)
        assert "Already refreshing." in second.text
        release.set()
        wait_idle(c)
    assert sorted(calls) == ["exports/10-out", "imports/10-sf"]
