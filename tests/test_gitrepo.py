import shutil
import subprocess
from contextlib import contextmanager
from datetime import timedelta

import pytest

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.events import EventKind, ListSink
from privacyfence_cockpit.gitrepo import GitError, GitManager, _first_line, no_credentials
from privacyfence_cockpit.models import utc_now
from privacyfence_cockpit.paths import DataPaths
from tests.gitutil import git, make_remote, push_branch, push_commit, rewrite_history


class Clock:
    def __init__(self):
        self.now = utc_now()

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path, request):
    paths = DataPaths(tmp_path / "data").ensure()
    db = Database(paths.db_file)
    db.migrate()
    request.addfinalizer(db.close)
    sink = ListSink()
    clock = Clock()
    remote = make_remote(tmp_path, {"recipe.yaml": "a: 1\n", "bin.dat": b"\x00\x01\x02", "notes.txt": "one\n"})
    manager = GitManager(paths, db, no_credentials, sink, clock)
    manager.configure(str(remote), "main", "none")
    return manager, paths, db, remote, sink, clock


@pytest.fixture
def cloned(env):
    env[0].clone()
    return env


def test_configure_validation(env):
    manager = env[0]
    for url in ("https://github.com/o/r.git", "ssh://git@host/o/r", "git@github.com:o/r.git", "file:///x/y", "/x/y"):
        manager.configure(url, "main", "ssh")
    for url in ("http://insecure/r", "relative/path", "-oProxy=x", ""):
        with pytest.raises(GitError):
            manager.configure(url, "main", "none")
    for branch in ("-evil", "a b", "", "x" * 101):
        with pytest.raises(GitError):
            manager.configure("/x/y", branch, "none")
    with pytest.raises(GitError):
        manager.configure("/x/y", "main", "token")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pw@host/r.git",
        "https://tokenonly@host/r.git",
        "https://user:p%40ss/w@rd!@host/r.git",
        "ssh://user:pw@host/r.git",
    ],
)
def test_configure_rejects_credentials_in_url(env, url):
    manager, _, db, *_ = env
    before = db.get_setting("git.repo_url")
    with pytest.raises(GitError, match="access token field") as info:
        manager.configure(url, "main", "https")
    assert "pw" not in str(info.value) and "tokenonly" not in str(info.value)
    assert db.get_setting("git.repo_url") == before


@pytest.mark.parametrize(
    "url", ["git@github.com:org/repo.git", "ssh://git@host/r.git", "https://host/r.git", "/some/local/path"]
)
def test_configure_accepts_credential_free_urls(env, url):
    env[0].configure(url, "main", "none")
    assert env[2].get_setting("git.repo_url") == url


def test_first_line_scrubs_credentials_before_truncating():
    assert _first_line("fatal: unable to access 'https://user:pw@host/x/': 404\nmore") == (
        "fatal: unable to access 'https://***@host/x/': 404"
    )
    prefix = "x" * 180
    line = _first_line(f"{prefix} https://user:supersecretpassword@host/x")
    assert "supersecret" not in line and "user:" not in line and len(line) <= 200
    assert _first_line("") == "unknown error"


def test_fetch_error_never_stores_url_credentials(cloned):
    manager, _, db, remote, *_ = cloned
    git(cloned[1].repo, "remote", "set-url", "origin", "file:///nonexistent/https://user:s3cretpw@host/r.git")
    with pytest.raises(GitError) as info:
        manager.fetch()
    assert "https://***@host/r.git" in str(info.value)
    assert "s3cretpw" not in str(info.value)
    assert "s3cretpw" not in (db.get_setting("git.last_fetch_error") or "")


def test_status_before_configure_and_clone(tmp_path, request):
    paths = DataPaths(tmp_path).ensure()
    db = Database(paths.db_file)
    db.migrate()
    request.addfinalizer(db.close)
    manager = GitManager(paths, db, no_credentials, ListSink())
    status = manager.status()
    assert (status.configured, status.cloned, status.head) == (False, False, None)
    assert (status.new_commits, status.dirty) == (0, False)
    assert manager.head() is None
    with pytest.raises(GitError, match="not configured"):
        manager.clone()
    with pytest.raises(GitError, match="not been cloned"):
        manager.log()


def test_clone_and_status(env):
    manager, paths, db, remote, _, _ = env
    assert manager.status().configured and not manager.status().cloned
    manager.clone()
    status = manager.status()
    assert status.cloned and status.branch == "main"
    assert status.head == status.remote_tip == git(remote, "rev-parse", "main")
    assert (status.new_commits, status.dirty, status.pinned_sha) == (0, False, None)
    assert manager.head() == status.head
    with pytest.raises(GitError, match="the checkout already exists"):
        manager.clone()


def test_clone_failure(env):
    manager, _, _, remote, _, _ = env
    shutil.rmtree(remote)
    with pytest.raises(GitError, match="git clone failed"):
        manager.clone()


def test_clone_into_empty_existing_folder(env):
    manager, paths = env[0], env[1]
    paths.repo.mkdir()
    manager.clone()
    assert manager.head()


def test_new_commits_after_fetch(cloned):
    manager, _, db, remote, _, _ = cloned
    tip = push_commit(remote, {"notes.txt": "two\n"}, "second")
    assert manager.status().new_commits == 0
    manager.fetch()
    status = manager.status()
    assert status.new_commits == 1 and status.remote_tip == tip and status.head != tip
    assert status.last_fetch_ok_at and db.get_setting("git.last_fetch_attempt_at")


def test_log_order_and_fields(cloned):
    manager, _, _, remote, _, _ = cloned
    second = push_commit(remote, {"notes.txt": "two\n"}, "second change")
    manager.fetch()
    commits = manager.log()
    assert [c.subject for c in commits] == ["second change", "initial"]
    assert commits[0].sha == second and commits[0].author == "test"
    assert commits[0].date.startswith("20") and "T" in commits[0].date
    assert len(manager.log(limit=1)) == 1


def test_show_and_binary(cloned):
    manager, _, _, remote, _, _ = cloned
    sha = push_commit(remote, {"notes.txt": "one\ntwo\n", "new.bin": b"\x00\xff\x00"}, "subject line\n\nbody text")
    manager.fetch()
    detail = manager.show(sha)
    assert detail.commit.sha == sha and detail.commit.subject == "subject line"
    assert detail.message == "subject line\n\nbody text"
    changes = {f.path: (f.added, f.deleted) for f in detail.files}
    assert changes == {"notes.txt": (1, 0), "new.bin": (None, None)}


def test_diff(cloned):
    manager, _, _, remote, _, _ = cloned
    sha = push_commit(remote, {"notes.txt": "one\ntwo\n"}, "more")
    manager.fetch()
    assert "+two" in manager.diff(sha, "notes.txt")
    with pytest.raises(GitError, match="unknown file"):
        manager.diff(sha, "recipe.yaml")
    with pytest.raises(GitError, match="unknown file"):
        manager.diff(sha, "../etc/passwd")


def test_diff_truncated(cloned):
    manager, _, _, remote, _, _ = cloned
    sha = push_commit(remote, {"big.txt": "x" * 100 + "\n" + "é" * 300_000}, "big")
    manager.fetch()
    out = manager.diff(sha, "big.txt")
    assert out.endswith("\n… diff truncated at 512 KiB")
    assert len(out.encode()) < 512 * 1024 + 100


def test_unknown_and_non_ancestor_sha(cloned):
    manager, _, _, remote, _, _ = cloned
    for bad in ("nothex", "0" * 40, "HEAD", "abc"):
        with pytest.raises(GitError, match="unknown commit"):
            manager.show(bad)
    # A commit that exists in the object store but is not on origin/main.
    git(manager._paths.repo, "checkout", "-q", "-b", "side")
    (manager._paths.repo / "side.txt").write_text("s")
    git(manager._paths.repo, "add", "side.txt")
    git(manager._paths.repo, "commit", "-m", "side")
    side = git(manager._paths.repo, "rev-parse", "HEAD")
    git(manager._paths.repo, "checkout", "-q", "main")
    with pytest.raises(GitError, match="unknown commit"):
        manager.show(side)
    with pytest.raises(GitError, match="unknown commit"):
        manager.diff(side, "side.txt")
    with pytest.raises(GitError, match="unknown commit"):
        manager.pin(side)


def test_update_fast_forwards(cloned):
    manager, _, _, remote, _, _ = cloned
    tip = push_commit(remote, {"notes.txt": "two\n"}, "second")
    manager.update()
    assert manager.head() == tip and manager.status().new_commits == 0


def test_update_refuses_when_pinned(cloned):
    manager, _, _, remote, _, _ = cloned
    first = manager.head()
    push_commit(remote, {"notes.txt": "two\n"}, "second")
    manager.fetch()
    manager.pin(first)
    with pytest.raises(GitError, match=f"pinned to {first[:7]}; return to tracking first"):
        manager.update()


def test_update_dirty_and_untracked(cloned):
    manager, paths, _, remote, _, _ = cloned
    push_commit(remote, {"notes.txt": "two\n"}, "second")
    (paths.repo / "work").mkdir()
    (paths.repo / "work" / "out.txt").write_text("untracked")
    assert not manager.status().dirty
    (paths.repo / "notes.txt").write_text("local edit\n")
    assert manager.status().dirty
    with pytest.raises(GitError, match="never overwrites them.*git -C /data/repo status"):
        manager.update()
    (paths.repo / "notes.txt").write_text("one\n")
    manager.update()


def test_update_after_rewrite(cloned):
    manager, _, _, remote, _, _ = cloned
    rewrite_history(remote, {"other.txt": "x"})
    with pytest.raises(GitError, match=r"origin/main cannot be fast-forwarded.*newest commit to run it\."):
        manager.update()


def test_pin_and_track(cloned):
    manager, _, db, remote, _, _ = cloned
    first = manager.head()
    tip = push_commit(remote, {"notes.txt": "two\n"}, "second")
    manager.update()
    manager.pin(first)
    status = manager.status()
    assert status.head == first and status.pinned_sha == first
    manager.track()
    assert db.get_setting("git.pinned_sha") is None
    assert manager.head() == tip  # the branch was already fast-forwarded before pinning


def test_pin_and_track_refuse_dirty(cloned):
    manager, paths, _, _, _, _ = cloned
    first = manager.head()
    (paths.repo / "notes.txt").write_text("edit\n")
    with pytest.raises(GitError, match="never overwrites"):
        manager.pin(first)
    with pytest.raises(GitError, match="never overwrites"):
        manager.track()


def test_checkout_failures(cloned):
    manager, _, db, _, _, _ = cloned
    db.set_setting("git.branch", "nope")
    with pytest.raises(GitError, match="git checkout failed"):
        manager.track()
    with pytest.raises(GitError, match="The remote has no branch 'nope'"):
        manager.log()
    assert manager.status().remote_tip is None


def test_fetch_failure_state_and_notice(cloned):
    manager, _, db, remote, sink, clock = cloned
    shutil.rmtree(remote)
    with pytest.raises(GitError, match="^git fetch failed: ") as info:
        manager.fetch()
    error = db.get_setting("git.last_fetch_error")
    assert error == str(info.value) and len(error) <= len("git fetch failed: ") + 200
    assert db.get_setting("git.failing_since")
    assert sink.events == []

    clock.now += timedelta(hours=23)
    with pytest.raises(GitError):
        manager.fetch()
    assert sink.events == []

    clock.now += timedelta(hours=2)
    with pytest.raises(GitError):
        manager.fetch()
    with pytest.raises(GitError):
        manager.fetch()
    assert [e.kind for e in sink.events] == [EventKind.GIT_FETCH_FAILING]
    assert manager.status().last_fetch_error == error


def test_fetch_success_clears_state(cloned, tmp_path):
    manager, _, db, remote, sink, clock = cloned
    moved = tmp_path / "moved.git"
    remote.rename(moved)
    with pytest.raises(GitError):
        manager.fetch()
    clock.now += timedelta(hours=25)
    with pytest.raises(GitError):
        manager.fetch()
    assert len(sink.events) == 1
    moved.rename(remote)
    manager.fetch()
    for key in ("git.last_fetch_error", "git.failing_since"):
        assert db.get_setting(key) is None
    assert db.query("SELECT * FROM notices_sent") == []
    assert manager.status().last_fetch_ok_at
    # A fresh failure streak starts over and can notify again later.
    moved = tmp_path / "moved2.git"
    remote.rename(moved)
    with pytest.raises(GitError):
        manager.fetch()
    clock.now += timedelta(hours=25)
    with pytest.raises(GitError):
        manager.fetch()
    assert len(sink.events) == 2


def test_credentials_reach_network_commands_only(cloned):
    manager, paths, db, remote, _, _ = cloned
    seen = []

    @contextmanager
    def creds():
        seen.append(1)
        yield {"GIT_TRACE_TEST": "1"}

    manager = GitManager(paths, db, creds, ListSink())
    manager.fetch()
    manager.status()
    manager.log()
    assert seen == [1]


def test_timeout_and_missing_git(cloned, monkeypatch):
    import subprocess

    manager = cloned[0]

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(GitError, match="timed out"):
        manager.status()

    def missing(*a, **k):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(GitError, match="could not be run"):
        manager.status()


def test_fetch_timeout_counts_as_failure(cloned, monkeypatch):
    import subprocess

    manager, _, db, _, _, _ = cloned

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(GitError, match="timed out"):
        manager.fetch()
    assert "timed out" in db.get_setting("git.last_fetch_error")


def test_fetch_before_the_clone_is_refused_without_recording_a_failure(env):
    manager, _, db, _, sink, _ = env
    with pytest.raises(GitError, match="has not been cloned"):
        manager.fetch()
    assert db.get_setting("git.last_fetch_error") is None
    assert db.get_setting("git.failing_since") is None
    assert db.get_setting("git.last_fetch_attempt_at") is None
    assert sink.events == []


def test_fetch_downloads_every_branch(cloned):
    manager, _, _, remote, _, _ = cloned
    push_branch(remote, {"x.txt": "x\n"}, "feature/x")
    manager.fetch()
    assert manager.remote_branches() == ["feature/x", "main"]


def test_remote_branches_is_empty_before_the_clone(env):
    assert env[0].remote_branches() == []


def test_remote_branches_is_empty_when_git_fails(cloned, monkeypatch):
    manager = cloned[0]

    def boom(argv, extra_env):
        raise GitError("nope")

    monkeypatch.setattr(manager, "_run", boom)
    assert manager.remote_branches() == []
    monkeypatch.setattr(
        manager, "_run", lambda argv, extra_env: subprocess.CompletedProcess(argv, 1, "", "bad")
    )
    assert manager.remote_branches() == []


def test_remote_branches_drops_unsafe_names(cloned, monkeypatch):
    manager = cloned[0]
    monkeypatch.setattr(
        manager,
        "_run",
        lambda argv, extra_env: subprocess.CompletedProcess(argv, 0, "HEAD\nmain\n-x\na/.hidden\nok/name\n", ""),
    )
    assert manager.remote_branches() == ["main", "ok/name"]


def test_configured_branch(env):
    assert env[0].configured_branch() == "main"


def _with_branch(cloned, files=None):
    manager, paths, _, remote, _, _ = cloned
    sha = push_branch(remote, files or {"x.txt": "x\n"}, "feature/x")
    manager.fetch()
    return manager, paths, remote, sha


def test_branch_worktree_checks_out_the_branch_tip(cloned):
    manager, paths, _, sha = _with_branch(cloned)
    head = manager.head()
    path, got = manager.branch_worktree("feature/x")
    assert path == paths.branches / "feature%2Fx"
    assert got == sha
    assert git(path, "rev-parse", "HEAD") == sha
    assert (path / "x.txt").read_text() == "x\n"
    assert manager.head() == head


def test_branch_worktree_moves_to_a_newer_commit_and_keeps_ignored_files(cloned):
    manager, _, remote, _ = _with_branch(cloned, {".gitignore": "work/\n", "x.txt": "x\n"})
    path, _ = manager.branch_worktree("feature/x")
    (path / "work").mkdir()
    (path / "work" / "state").write_text("kept")
    newer = push_commit(remote, {"y.txt": "y\n"}, "more", branch="feature/x")
    manager.fetch()
    path, sha = manager.branch_worktree("feature/x")
    assert sha == newer
    assert git(path, "rev-parse", "HEAD") == newer
    assert (path / "work" / "state").read_text() == "kept"


def test_branch_worktree_replaces_a_leftover_folder(cloned):
    manager, paths, _, sha = _with_branch(cloned)
    stray = paths.branches / "feature%2Fx"
    stray.mkdir(parents=True)
    (stray / "stray").write_text("junk")
    path, got = manager.branch_worktree("feature/x")
    assert got == sha
    assert not (path / "stray").exists()


def test_branch_worktree_refuses_the_configured_branch(cloned):
    with pytest.raises(GitError, match="main is the configured branch; run it without choosing a branch."):
        cloned[0].branch_worktree("main")


def test_branch_worktree_refuses_an_unknown_branch(cloned):
    with pytest.raises(GitError, match="There is no branch nope on the remote."):
        cloned[0].branch_worktree("nope")


def test_branch_worktree_refuses_tracked_changes(cloned):
    manager, _, _, _ = _with_branch(cloned)
    path, _ = manager.branch_worktree("feature/x")
    (path / "x.txt").write_text("changed\n")
    with pytest.raises(GitError, match="/data/branches/feature%2Fx"):
        manager.branch_worktree("feature/x")


def test_branch_worktree_refuses_a_name_that_escapes_the_folder(cloned, monkeypatch):
    manager = cloned[0]
    monkeypatch.setattr(manager, "remote_branches", lambda: ["a/b"])
    monkeypatch.setattr(manager, "_checked", lambda *a: subprocess.CompletedProcess(a, 0, "0" * 40, ""))
    monkeypatch.setattr("privacyfence_cockpit.gitrepo.branch_folder", lambda b: "../evil")
    with pytest.raises(GitError, match="There is no branch"):
        manager.branch_worktree("a/b")


def test_worktrees_of_deleted_branches_are_removed(cloned):
    manager, paths, remote, _ = _with_branch(cloned)
    path, _ = manager.branch_worktree("feature/x")
    git(remote, "branch", "-D", "feature/x")
    push_branch(remote, {"z.txt": "z\n"}, "feature/z")
    manager.fetch()
    manager.branch_worktree("feature/z")
    assert not path.exists()
    assert (paths.branches / "feature%2Fz").exists()


def test_branch_worktree_reports_a_refused_checkout(cloned):
    manager, _, remote, _ = _with_branch(cloned)
    path, _ = manager.branch_worktree("feature/x")
    newer = push_commit(remote, {"y.txt": "y\n"}, "more", branch="feature/x")
    manager.fetch()
    (path / "y.txt").write_text("untracked\n")
    with pytest.raises(GitError, match="git checkout failed"):
        manager.branch_worktree("feature/x")
    assert newer
