"""The layout of the /data volume."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def branch_folder(branch: str) -> str:
    """The folder name of a branch's worktree; `%` cannot occur in a branch name, so this is reversible."""
    return branch.replace("/", "%2F")


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def venvs(self) -> Path:
        return self.root / "venvs"

    @property
    def branches(self) -> Path:
        return self.root / "branches"

    @property
    def branch_venvs(self) -> Path:
        return self.root / "branch-venvs"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def db_file(self) -> Path:
        return self.root / "cockpit.db"

    @property
    def secrets(self) -> Path:
        return self.root / "secrets"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def run_lock(self) -> Path:
        return self.root / "run.lock"

    @property
    def git_lock(self) -> Path:
        return self.root / "git.lock"

    def ensure(self) -> DataPaths:
        # The repo folder is left to `git clone`, which refuses a non-empty target.
        for folder in (self.root, self.venvs, self.logs, self.secrets, self.home, self.home / ".ssh"):
            folder.mkdir(parents=True, exist_ok=True)
            folder.chmod(0o700)
        return self
