import pytest

from privacyfence_cockpit.branches import BranchCheckout, prepare_branch
from privacyfence_cockpit.db import Database
from privacyfence_cockpit.events import NullSink
from privacyfence_cockpit.gitrepo import GitError, GitManager, no_credentials
from privacyfence_cockpit.paths import DataPaths
from privacyfence_cockpit.venvs import VenvManager, VenvResult
from tests.gitutil import make_remote, push_branch


class FixedVenvs(VenvManager):
    def __init__(self, *args, result, **kwargs):
        super().__init__(*args, **kwargs)
        self.result = result
        self.seen = []

    def ensure_for(self, repo):
        self.seen.append(repo)
        return self.result


@pytest.fixture
def setup(tmp_path):
    paths = DataPaths(tmp_path / "data").ensure()
    db = Database(paths.db_file)
    db.migrate()
    remote = make_remote(tmp_path, {"README.md": "one\n"})
    git = GitManager(paths, db, no_credentials, NullSink())
    git.configure(str(remote), "main", "none")
    git.clone()
    yield paths, db, remote, git
    db.close()


def test_prepare_branch_returns_the_worktree_sha_and_environment(setup):
    paths, db, remote, git = setup
    sha = push_branch(remote, {"new.txt": "x"}, "feature/x")
    venv = paths.venvs / "abc"
    venvs = FixedVenvs(paths, db, NullSink(), result=VenvResult(venv, True, None))
    checkout = prepare_branch(git, venvs, "feature/x")
    root = paths.branches / "feature%2Fx"
    assert checkout == BranchCheckout("feature/x", sha, root, venv, None)
    assert (root / "new.txt").exists()
    assert venvs.seen == [root]


def test_prepare_branch_propagates_a_fetch_error(setup, monkeypatch):
    paths, db, _remote, git = setup

    def fail():
        raise GitError("offline")

    monkeypatch.setattr(git, "fetch", fail)
    venvs = FixedVenvs(paths, db, NullSink(), result=VenvResult(None, False, "boom"))
    with pytest.raises(GitError, match="offline"):
        prepare_branch(git, venvs, "feature/x")
    assert venvs.seen == []
