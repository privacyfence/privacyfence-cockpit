import pytest

from privacyfence_cockpit.permissions import (
    NO_SUGGESTIONS,
    PermissionsReport,
    collect,
    merge,
    render_merged,
    render_recipe,
    rule_text,
)
from tests.test_runner import Env


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


DATA = {
    "version": 1,
    "recipe": "a",
    "steps": [
        {"id": "s1", "tool": "drive_list_files", "status": "covered", "matched_rule": "r", "dropped": [],
         "suggested_rules": []},
        {"id": "s2", "tool": "gmail_send", "status": "needs_rule", "matched_rule": None, "dropped": [],
         "suggested_rules": [
             {"group": "Gmail", "label": "Send to", "value": ["a@x"], "value_known": True, "verb": "send",
              "operation": "gmail_send"},
             {"group": "Gmail", "label": "Send to", "value": None, "value_known": False, "verb": "send",
              "operation": "gmail_send"},
         ]},
        {"id": "s3", "tool": "jira_export_issues", "status": "unknown", "matched_rule": None, "dropped": [],
         "suggested_rules": None},
    ],
    "needed": [
        {"group": "Gmail", "label": "Send to", "value": ["a@x"], "verbs": ["send"]},
        {"group": "Drive", "label": "Folder", "value": None, "verbs": ["read", "list"]},
    ],
    "tools": ["gmail_send", "drive_list_files"],
}


async def test_collect_ok(env):
    env.venv(permissions_json=DATA)
    report = await collect(env.recipe, env.runner())
    assert report == PermissionsReport(env.recipe.path, DATA, None)
    argv = env.records()[0]["argv"]
    assert argv == ["permissions", str(env.recipe.yaml_path), "--json"]


async def test_collect_exit_4(env):
    env.venv(exit_code=4)
    report = await collect(env.recipe, env.runner())
    assert report.data is None
    assert report.error == "privacyfence-sync permissions exited with 4: "


async def test_collect_no_venv(env):
    report = await collect(env.recipe, env.runner())
    assert report.error == "the recipe environment is not ready"


async def test_collect_invalid_json(env):
    env.venv(permissions_json=None)
    report = await collect(env.recipe, env.runner())
    assert report.error == "privacyfence-sync permissions printed invalid JSON"


async def test_collect_old_sync_stderr_line(env):
    env.venv(help_permissions=False)
    report = await collect(env.recipe, env.runner())
    assert report.error.startswith("privacyfence-sync permissions exited with 2: privacyfence-sync: error")


async def test_collect_cannot_start(env):
    venv = env.venv()
    (venv / "bin" / "privacyfence-sync").unlink()
    report = await collect(env.recipe, env.runner())
    assert report.error.startswith("could not start privacyfence-sync")


async def test_collect_timeout(env):
    import stat
    venv = env.venv()
    script = venv / "bin" / "privacyfence-sync"
    script.write_text("#!/bin/sh\nexec sleep 30\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    report = await collect(env.recipe, env.runner(), timeout=0.2)
    assert report.error == "privacyfence-sync permissions timed out after 0.2 s"


def other():
    return {
        "version": 1,
        "recipe": "b",
        "steps": [{"id": "t1", "tool": "slack_post", "status": "needs_rule", "suggested_rules": None}],
        "needed": [
            {"group": "Gmail", "label": "Send to", "value": ["a@x"], "verbs": ["draft"]},
            {"group": "Gmail", "label": "Send to", "value": ["b@x"], "verbs": ["send"]},
            {"group": "Drive", "label": "Folder", "value": None, "verbs": ["write"]},
        ],
        "tools": ["slack_post", "gmail_send"],
    }


REPORTS = [
    PermissionsReport("g1/a", DATA, None),
    PermissionsReport("g2/b", other(), None),
    PermissionsReport("g2/c", None, "boom"),
]
GROUPS = {"g1/a": "g1", "g2/b": "g2", "g2/c": "g2"}


def test_merge_union():
    (merged,) = merge(REPORTS, "union", GROUPS)
    assert merged.title == "All recipes"
    by_key = {(r.group, r.value): r for r in merged.rules}
    shared = by_key[("Gmail", ("a@x",))]
    assert shared.verbs == ("draft", "send")
    assert shared.recipes == ("g1/a", "g2/b")
    drive = by_key[("Drive", None)]
    assert drive.verbs == ("list", "read", "write")
    assert len(merged.rules) == 3
    assert merged.tools == ("drive_list_files", "gmail_send", "slack_post")
    assert merged.unsuggested == ("g1/a [s3] jira_export_issues", "g2/b [t1] slack_post")
    assert merged.errors == ("g2/c: boom",)


def test_merge_by_group_in_run_order():
    sets = merge(REPORTS, "group", GROUPS)
    assert [s.title for s in sets] == ["Group g1", "Group g2"]
    assert [len(s.rules) for s in sets] == [2, 3 - 1 + 1]
    assert sets[1].errors == ("g2/c: boom",)
    assert sets[0].errors == ()


def test_merge_duplicate_rules_in_one_recipe_list_it_once():
    twice = {"needed": [{"group": "G", "label": "L", "value": ["v"], "verbs": ["a"]}] * 2}
    (merged,) = merge([PermissionsReport("g/x", twice, None)], "union", {})
    assert merged.rules[0].recipes == ("g/x",)


def test_rule_text():
    assert rule_text("Send to", ["a", "b"], ["send", "draft"]) == "Send to = a, b (send, draft)"
    assert rule_text("Folder", None, ["read"]) == "Folder, naming what it reaches at run time (read)"


def test_render_recipe():
    assert render_recipe(PermissionsReport("g1/a", DATA, None)) == [
        "g1/a",
        "  [s1] drive_list_files: covered",
        "  [s2] gmail_send: needs_rule",
        "      Send to = a@x (send)",
        "      Send to, naming what it reaches at run time (send)",
        "  [s3] jira_export_issues: unknown",
        f"      {NO_SUGGESTIONS}",
        "  Rules:",
        "    Send to = a@x (send)",
        "    Folder, naming what it reaches at run time (read, list)",
        "  Tools: gmail_send, drive_list_files",
    ]


def test_render_recipe_none_and_error():
    empty = {"steps": [], "needed": [], "tools": []}
    assert render_recipe(PermissionsReport("g/x", empty, None)) == ["g/x", "  Rules:", "    none", "  Tools: "]
    assert render_recipe(PermissionsReport("g/x", None, "bad")) == ["g/x", "  error: bad"]


def test_render_merged():
    lines = render_merged(merge(REPORTS, "union", GROUPS))
    assert lines == [
        "All recipes",
        "  Rules:",
        "    Send to = a@x (draft, send)   [g1/a, g2/b]",
        "    Folder, naming what it reaches at run time (list, read, write)   [g1/a, g2/b]",
        "    Send to = b@x (send)   [g2/b]",
        "  Tools: drive_list_files, gmail_send, slack_post",
        f"  Steps without suggestions ({NO_SUGGESTIONS}):",
        "    g1/a [s3] jira_export_issues",
        "    g2/b [t1] slack_post",
        "  Errors:",
        "    g2/c: boom",
    ]


async def test_collect_non_object_json(env):
    env.venv(permissions_json=[1])
    report = await collect(env.recipe, env.runner())
    assert report.error == "privacyfence-sync permissions printed invalid JSON"


def _state(pid):
    # A process that exits while its stat file is open makes the read fail with ProcessLookupError (or return
    # nothing), not FileNotFoundError; for this helper all of those mean "gone".
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return None


def test_state_treats_a_process_that_vanishes_mid_read_as_gone(monkeypatch):
    def vanishing(*args, **kwargs):
        raise ProcessLookupError(3, "No such process")

    # A module-level name shadows the builtin for _state only, so pytest's own file handling is untouched.
    monkeypatch.setitem(globals(), "open", vanishing)
    assert _state(1) is None


async def test_collect_timeout_kills_the_children_too(env, tmp_path):
    import asyncio
    import stat

    pidfile = tmp_path / "child.pid"
    venv = env.venv()
    script = venv / "bin" / "privacyfence-sync"
    script.write_text(f"#!/bin/sh\nsleep 30 &\necho $! > {pidfile}\nwait\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    report = await collect(env.recipe, env.runner(), timeout=0.5)
    assert report.error == "privacyfence-sync permissions timed out after 0.5 s"
    child = int(pidfile.read_text())
    for _ in range(100):
        if _state(child) in (None, "Z"):
            break
        await asyncio.sleep(0.02)
    assert _state(child) in (None, "Z")
