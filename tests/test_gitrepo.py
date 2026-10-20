import shutil
from contextlib import contextmanager
from datetime import timedelta

import pytest

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.events import EventKind, ListSink
from privacyfence_cockpit.gitrepo import GitError, GitManager, no_credentials
from privacyfence_cockpit.models import utc_now
from privacyfence_cockpit.paths import DataPaths
from tests.gitutil import git, make_remote, push_commit, rewrite_history


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
    with pytest.raises(GitError, match="git log failed"):
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
