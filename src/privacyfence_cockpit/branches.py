"""Prepares a branch worktree and its recipe environment for a manual run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from privacyfence_cockpit.gitrepo import GitManager
from privacyfence_cockpit.venvs import VenvManager


@dataclass(frozen=True)
class BranchCheckout:
    branch: str
    sha: str
    root: Path
    venv: Path | None
    venv_error: str | None


def prepare_branch(git: GitManager, venvs: VenvManager, branch: str) -> BranchCheckout:
    """Fetch, move the branch's worktree to its newest commit and make sure it has an environment."""
    git.fetch()
    root, sha = git.branch_worktree(branch)
    result = venvs.ensure_for(root)
    return BranchCheckout(branch, sha, root, result.path, result.error)
