import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fakesync import install_fake_venv
from gitutil import make_remote, push_commit

from privacyfence_cockpit.cli import main
from privacyfence_cockpit.config import EnvConfig
from privacyfence_cockpit.crypto import SecretDecryptError
from privacyfence_cockpit.events import Event, EventKind
from privacyfence_cockpit.models import Mode, Outcome, utc_now
from privacyfence_cockpit.permissions import NO_SUGGESTIONS
from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.token_check import TokenCheck, TokenChecker, TokenState
from privacyfence_cockpit.venvs import VenvManager, VenvResult

TOKEN = "pfst_" + "s3cr3t" * 5
RECIPE = "steps: []\n"
FILES = {
    "workflows/10-imports/10-sf/recipe.yaml": RECIPE,
    "workflows/10-imports/20-jira/recipe.yaml": RECIPE,
    "workflows/20-exports/10-out/recipe.yaml": RECIPE,
    "cockpit.yaml": (
        "version: 1\ntimezone: UTC\nschedules:\n"
        "  - {name: nightly, cron: '0 3 * * *', run: [imports]}\n"
        "  - {name: weekly, cron: '0 4 * * 1', run: [exports, imports/10-sf]}\n"
    ),
}
REPORT = {
    "version": 1,
    "recipe": "r",
    "steps": [
        {"id": "s1", "tool": "drive_list_files", "status": "needs_rule", "suggested_rules": None},
        {
            "id": "s2",
            "tool": "drive_download_file",
            "status": "needs_rule",
            "suggested_rules": [
                {"group": "drive", "label": "Drive folder", "value": ["abc"], "value_known": True,
                 "verb": "read", "operation": "download"}
            ],
        },
    ],
    "needed": [{"group": "drive", "label": "Drive folder", "value": ["abc"], "verbs": ["read"]}],
    "tools": ["drive_list_files"],
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    key = tmp_path / "key"
    key.write_text(base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COCKPIT_MASTER_KEY_FILE", str(key))
    monkeypatch.delenv("PRIVACYFENCE_TOKEN", raising=False)
    monkeypatch.delenv("COCKPIT_PUBLIC_URL", raising=False)
    return tmp_path


def open_cockpit() -> Cockpit:
    return Cockpit.open(EnvConfig.from_environ(dict(os.environ)))


@pytest.fixture
def cloned(env: Path) -> Path:
    """A cockpit data dir whose checkout is cloned from a local remote; returns the remote."""
    remote = make_remote(env, FILES)
    cockpit = open_cockpit()
    cockpit.git.configure(str(remote), "main", "none")
    cockpit.git.clone()
    cockpit.db.close()
    return remote


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


# list


def test_list_without_runs(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["list"], capsys)
    assert code == 0
    assert out.splitlines() == [
        "10-imports",
        f"  {'10-sf':<32} {'':<14} never",
        f"  {'20-jira':<32} {'':<14} never",
        "20-exports",
        f"  {'10-out':<32} {'':<14} never",
    ]


def test_list_with_runs(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cockpit = open_cockpit()
    now = utc_now()
    run_id = cockpit.history.start(
        job_id=None, trigger="manual", recipe_path="imports/10-sf", mode=Mode.UNATTENDED,
        started_at=now, log_path=None, commit_sha=None, token_source="none",
    )
    cockpit.history.finish(run_id, outcome=Outcome.DENIED, exit_code=3, finished_at=now, note=None)
    cockpit.db.close()
    code, out, _ = run(["list"], capsys)
    assert code == 0
    first = out.splitlines()[1]
    assert first.startswith(f"  {'10-sf':<32} {'denied':<14} {now:%Y-%m-%d %H:%M} UTC")
    assert "never" in out.splitlines()[2]


def test_list_without_a_checkout(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["list"], capsys) == (0, "No recipes found.\n", "")


def test_commands_without_a_master_key_exit_2(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COCKPIT_MASTER_KEY_FILE", str(env / "missing"))
    code, out, err = run(["list"], capsys)
    assert (code, out) == (2, "")
    assert "No master key" in err


# permissions


def with_report(env: Path, **kwargs: object) -> None:
    cockpit = open_cockpit()
    install_fake_venv(cockpit.paths, cockpit.db, permissions_json=REPORT, **kwargs)
    cockpit.db.close()


def test_permissions_text(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with_report(cloned)
    code, out, _ = run(["permissions", "imports/10-sf"], capsys)
    assert code == 0
    assert out.splitlines() == [
        "imports/10-sf",
        "  [s1] drive_list_files: needs_rule",
        f"      {NO_SUGGESTIONS}",
        "  [s2] drive_download_file: needs_rule",
        "      Drive folder = abc (read)",
        "  Rules:",
        "    Drive folder = abc (read)",
        "  Tools: drive_list_files",
    ]


def test_permissions_union_and_by_group(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with_report(cloned)
    code, out, _ = run(["permissions", "--union"], capsys)
    assert code == 0
    assert out.startswith("All recipes\n  Rules:\n    Drive folder = abc (read)   [")
    assert "imports/10-sf" in out and "exports/10-out" in out
    code, out, _ = run(["permissions", "--by-group", "imports"], capsys)
    assert code == 0
    assert out.startswith("Group imports\n")
    assert "Group exports" not in out
    assert f"  Steps without suggestions ({NO_SUGGESTIONS}):" in out


def test_permissions_json(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with_report(cloned)
    code, out, _ = run(["permissions", "--json", "exports"], capsys)
    assert code == 0
    assert json.loads(out) == {"recipes": [{"recipe": "exports/10-out", "report": REPORT, "error": None}]}


def test_permissions_without_a_venv_reports_the_error(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["permissions", "--json", "exports"], capsys)
    assert code == 0
    assert json.loads(out)["recipes"][0]["error"] == "the recipe environment is not ready"
    code, out, _ = run(["permissions", "exports"], capsys)
    assert out == "exports/10-out\n  error: the recipe environment is not ready\n"


def test_permissions_unknown_selector_exits_1(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(["permissions", "nope"], capsys)
    assert (code, out) == (1, "")
    assert "no recipe or group matches 'nope'" in err


def test_permissions_union_and_by_group_conflict(cloned: Path) -> None:
    assert main(["permissions", "--union", "--by-group"]) == 1


# token


def test_token_set_from_stdin_then_show(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin(monkeypatch, TOKEN + "\n")
    code, out, err = run(["token", "set", "imports"], capsys)
    assert (code, out, err) == (0, "imports: no server\n", "")
    code, out, err = run(["token", "show"], capsys)
    assert code == 0
    lines = {line.split()[0]: line for line in out.splitlines()}
    assert list(lines) == ["imports/10-sf", "imports/20-jira", "exports/10-out", "imports", "exports", "default"]
    assert lines["imports"] == f"{'imports':<40} present  no server"
    assert lines["default"] == f"{'default':<40} missing"
    assert TOKEN not in out + err
    cockpit = open_cockpit()
    assert cockpit.store.get("token", "imports") == TOKEN
    cockpit.db.close()


def test_token_set_prints_the_check_result(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_check(self: TokenChecker, name: str) -> TokenCheck:
        result = TokenCheck(TokenState.EXPIRING, "ci", "2026-11-01T00:00:00Z", 12, "careful")
        self._record(name, result)
        return result

    monkeypatch.setattr(TokenChecker, "check_one", fake_check)
    stdin(monkeypatch, f"  {TOKEN}  \n")
    code, out, _ = run(["token", "set", "default"], capsys)
    assert code == 0
    assert out == "default: expiring ci expires 2026-11-01T00:00:00Z (12 day(s) left)\ncareful\n"
    code, out, _ = run(["token", "show"], capsys)
    assert out.splitlines()[-1] == f"{'default':<40} present  expiring ci 12"
    assert TOKEN not in out


def test_token_set_on_a_tty_uses_getpass(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import getpass

    class Tty:
        def isatty(self) -> bool:
            return True

    prompts: list[str] = []
    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr(getpass, "getpass", lambda prompt: prompts.append(prompt) or TOKEN)
    assert run(["token", "set", "default"], capsys)[0] == 0
    assert prompts == ["Token: "]


def test_token_set_on_a_tty_without_input_is_refused(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import getpass

    class Tty:
        def isatty(self) -> bool:
            return True

    def eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr(getpass, "getpass", eof)
    code, _, err = run(["token", "set", "default"], capsys)
    assert code == 1
    assert "pfst_" in err


def test_token_set_unknown_name_suggests_one(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin(monkeypatch, TOKEN)
    code, out, err = run(["token", "set", "imprts"], capsys)
    assert (code, out) == (1, "")
    assert "unknown token name 'imprts'; did you mean 'imports'?" in err
    assert TOKEN not in err


def test_token_set_refuses_a_non_scoped_value(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin(monkeypatch, "ghp_" + "a" * 30 + "\n")
    code, out, err = run(["token", "set", "default"], capsys)
    assert (code, out) == (1, "")
    assert err == "Only PrivacyFence scoped tokens (pfst_…) are supported.\n"
    cockpit = open_cockpit()
    assert cockpit.store.names("token") == []
    cockpit.db.close()


def test_token_delete_and_its_missing_case(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin(monkeypatch, TOKEN)
    run(["token", "set", "default"], capsys)
    assert run(["token", "delete", "default"], capsys) == (0, "deleted default\n", "")
    assert run(["token", "delete", "default"], capsys) == (1, "", "no token named default\n")
    cockpit = open_cockpit()
    assert cockpit.db.query("SELECT * FROM token_status") == []
    cockpit.db.close()


def test_a_token_that_cannot_be_decrypted_is_one_clean_line(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(self: object, name: str) -> TokenCheck:
        raise SecretDecryptError("bad")

    monkeypatch.setattr(TokenChecker, "check_one", broken)
    stdin(monkeypatch, TOKEN)
    code, out, err = run(["token", "set", "default"], capsys)
    assert (code, out) == (2, "")
    assert err == "A stored secret cannot be decrypted with this master key. Delete it and set it again.\n"
    assert "Traceback" not in err


def test_commands_deliver_pending_notifications(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    delivered: list[int] = []

    async def deliver(self: object) -> int:
        delivered.append(1)
        return 0

    from privacyfence_cockpit.notify import Notifier

    monkeypatch.setattr(Notifier, "deliver_pending", deliver)
    run(["list"], capsys)
    run(["token", "delete", "default"], capsys)
    assert delivered == [1, 1]


# schedule


def test_schedule_list(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cockpit = open_cockpit()
    cockpit.scheduler.set_paused("weekly", True)
    cockpit.db.close()
    code, out, _ = run(["schedule", "list"], capsys)
    assert code == 0
    first, second = out.splitlines()
    assert first.startswith("nightly  0 3 * * *  UTC  active  next 20")
    assert first.endswith(" 03:00 UTC  run: imports")
    assert second == "weekly  0 4 * * 1  UTC  paused  next -  run: exports, imports/10-sf"


def test_schedule_list_without_schedules(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["schedule", "list"], capsys) == (0, "No schedules.\n", "")


def test_schedule_list_with_a_broken_cockpit_yaml(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cockpit = open_cockpit()
    (cockpit.paths.repo).mkdir()
    (cockpit.paths.repo / "cockpit.yaml").write_text("version: 2\n")
    cockpit.db.close()
    code, out, err = run(["schedule", "list"], capsys)
    assert (code, out) == (1, "")
    assert err.startswith("cockpit.yaml: ")


# git


def test_git_status_not_configured(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["git", "status"], capsys)
    assert code == 0
    assert out == "No recipe repository is configured. Set one on the Code page.\n"


def test_git_status_tracking_and_new_commits(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cockpit = open_cockpit()
    head = cockpit.git.head()
    push_commit(cloned, {"x.txt": "1"}, "more")
    cockpit.git.fetch()
    cockpit.db.close()
    assert head is not None
    code, out, _ = run(["git", "status"], capsys)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == f"Repository:  {cloned}"
    assert lines[1] == "Branch:      main"
    assert lines[2] == f"Checkout:    tracking main at {head[:7]}"
    assert lines[3].startswith("Remote tip:  ") and lines[3] != f"Remote tip:  {head[:7]}"
    assert lines[4] == "New commits: 1"
    assert lines[5].startswith("Last fetch:  20")
    assert len(lines) == 6


def test_git_status_scrubs_credentials_and_shows_pinned_dirty_and_error(
    cloned: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cockpit = open_cockpit()
    head = cockpit.git.head()
    assert head is not None
    cockpit.db.set_setting("git.repo_url", "https://user:hunter2@example.com/org/repo.git")
    cockpit.db.set_setting("git.pinned_sha", head)
    cockpit.db.set_setting("git.last_fetch_error", "git fetch failed: boom")
    (cockpit.paths.repo / "cockpit.yaml").write_text("changed\n")
    cockpit.db.close()
    code, out, _ = run(["git", "status"], capsys)
    assert code == 0
    assert "hunter2" not in out and "user" not in out.split("Repository:")[1].splitlines()[0]
    assert "https://***@example.com/org/repo.git" in out
    assert f"Checkout:    pinned to {head[:7]} (has changes to tracked files)" in out
    assert "Last fetch:  never" in out
    assert "Error:       git fetch failed: boom" in out


def test_git_status_before_the_first_clone(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cockpit = open_cockpit()
    cockpit.git.configure("https://example.com/org/repo.git", "main", "https")
    cockpit.db.close()
    code, out, _ = run(["git", "status"], capsys)
    assert code == 0
    assert "Checkout:    not cloned yet" in out
    assert "Remote tip" not in out


def fake_ensure(monkeypatch: pytest.MonkeyPatch, result: VenvResult) -> list[int]:
    calls: list[int] = []

    def ensure(self: VenvManager) -> VenvResult:
        calls.append(1)
        return result

    monkeypatch.setattr(VenvManager, "ensure", ensure)
    return calls


def test_git_update_fast_forwards_and_ensures_the_venv(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = fake_ensure(monkeypatch, VenvResult(Path("v"), True, None))
    new = push_commit(cloned, {"x.txt": "1"}, "more")
    code, out, err = run(["git", "update"], capsys)
    assert (code, err) == (0, "")
    assert out == f"updated to {new[:7]}\nrecipe environment built\n"
    assert calls == [1]
    cockpit = open_cockpit()
    assert cockpit.git.head() == new
    cockpit.db.close()


def test_git_update_when_current(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_ensure(monkeypatch, VenvResult(Path("v"), False, None))
    assert run(["git", "update"], capsys) == (0, "already up to date\nrecipe environment ready\n", "")


def test_git_update_reports_a_venv_failure(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_ensure(monkeypatch, VenvResult(None, False, "requirements.txt must pin privacyfence-sync"))
    code, out, err = run(["git", "update"], capsys)
    assert code == 2
    assert err == "requirements.txt must pin privacyfence-sync\n"


def test_git_update_error_is_one_line_and_releases_the_lock(
    cloned: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cockpit = open_cockpit()
    cockpit.db.set_setting("git.pinned_sha", "a" * 40)
    cockpit.db.close()
    code, out, err = run(["git", "update"], capsys)
    assert (code, out) == (2, "")
    assert err == "The checkout is pinned to aaaaaaa; return to tracking first.\n"
    cockpit = open_cockpit()
    assert cockpit.lock.try_acquire()
    cockpit.lock.release()
    cockpit.db.close()


def test_git_update_delivers_events_it_raised(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from privacyfence_cockpit.notify import Notifier

    seen: list[Event] = []
    monkeypatch.setattr(Notifier, "emit", lambda self, event: seen.append(event))

    def failing(self: VenvManager) -> VenvResult:
        self.events.emit(Event(EventKind.VENV_FAILED, utc_now()))
        return VenvResult(None, False, "x")

    monkeypatch.setattr(VenvManager, "ensure", failing)
    assert run(["git", "update"], capsys)[0] == 2
    assert [e.kind for e in seen] == [EventKind.VENV_FAILED]


HOLDER = """
import fcntl, sys, time
handle = open(sys.argv[1], "a+")
fcntl.flock(handle, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(0.6)
"""


def test_git_update_waits_for_the_run_lock(
    cloned: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_ensure(monkeypatch, VenvResult(Path("v"), False, None))
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(cloned.parent / "data" / "run.lock")], stdout=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    try:
        code, out, err = run(["git", "update"], capsys)
    finally:
        proc.wait(timeout=5)
        proc.stdout.close()
    assert code == 0
    assert err == "Waiting for the cockpit's current run to finish…\n"
    assert out == "already up to date\nrecipe environment ready\n"
