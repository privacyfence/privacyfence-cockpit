"""Helpers that build bare git repositories for tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

_IDENTITY = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *_IDENTITY, *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)


def make_remote(tmp_path: Path, files: dict[str, str | bytes], branch: str = "main") -> Path:
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-b", branch)
    _write(work, files)
    git(work, "add", "-A")
    git(work, "commit", "-m", "initial")
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "--bare", str(work), str(remote))
    return remote


def push_commit(remote: Path, files: dict[str, str | bytes], message: str, branch: str = "main") -> str:
    scratch = remote.parent / f"push-{len(list(remote.parent.glob('push-*')))}"
    git(remote.parent, "clone", "--branch", branch, str(remote), str(scratch))
    _write(scratch, files)
    git(scratch, "add", "-A")
    git(scratch, "commit", "-m", message)
    git(scratch, "push", "origin", branch)
    return git(scratch, "rev-parse", "HEAD")


def push_branch(remote: Path, files: dict[str, str | bytes], branch: str, base: str = "main") -> str:
    """Create `branch` from `base` with one commit and push it; return the new sha."""
    scratch = remote.parent / f"push-{len(list(remote.parent.glob('push-*')))}"
    git(remote.parent, "clone", "--branch", base, str(remote), str(scratch))
    git(scratch, "checkout", "-b", branch)
    _write(scratch, files)
    git(scratch, "add", "-A")
    git(scratch, "commit", "-m", "branch commit")
    git(scratch, "push", "origin", branch)
    return git(scratch, "rev-parse", "HEAD")


def rewrite_history(remote: Path, files: dict[str, str | bytes], branch: str = "main") -> str:
    """Replace the branch with an unrelated root commit and force-push it."""
    scratch = remote.parent / "rewrite"
    scratch.mkdir()
    git(scratch, "init", "-b", branch)
    _write(scratch, files)
    git(scratch, "add", "-A")
    git(scratch, "commit", "-m", "rewritten")
    git(scratch, "push", "--force", str(remote), branch)
    return git(scratch, "rev-parse", "HEAD")
