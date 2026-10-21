from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.gitutil import git

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_check.py"
CLEAN = "# Changelog\n\n## [Unreleased]\n\n## [1.2.0] - 2026-01-01\n\n- x\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", str(origin), str(work))
    git(work, "checkout", "-b", "main")
    (work / "CHANGELOG.md").write_text(CLEAN)
    git(work, "add", "-A")
    git(work, "commit", "-m", "initial")
    git(work, "push", "-u", "origin", "main")
    return work


def run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=repo, capture_output=True, text=True, check=False)


def tag(repo: Path, name: str) -> None:
    git(repo, "tag", name)


def test_version_ok(repo: Path) -> None:
    result = run(repo, "--version", "1.2.0")
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == "latest=true"


def test_version_bad_form(repo: Path) -> None:
    result = run(repo, "--version", "1.2")
    assert result.returncode == 1
    assert result.stdout.startswith("::error::")


def test_version_head_not_main(repo: Path) -> None:
    (repo / "x").write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "ahead")
    result = run(repo, "--version", "1.2.0")
    assert result.returncode == 1
    assert "HEAD is not origin/main" in result.stdout


def test_version_tag_exists(repo: Path) -> None:
    tag(repo, "v1.2.0")
    result = run(repo, "--version", "1.2.0")
    assert result.returncode == 1
    assert "already exists" in result.stdout


def test_version_must_sort_after_existing(repo: Path) -> None:
    tag(repo, "v1.3.0")
    result = run(repo, "--version", "1.2.0")
    assert result.returncode == 1
    assert "does not sort after" in result.stdout


def test_version_ten_sorts_after_nine(repo: Path) -> None:
    tag(repo, "v1.9.0")
    (repo / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [1.10.0] - 2026-01-01\n")
    git(repo, "commit", "-am", "release")
    git(repo, "push", "origin", "main")
    result = run(repo, "--version", "1.10.0")
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == "latest=true"


def test_version_ignores_non_matching_tags(repo: Path) -> None:
    for name in ("v9.9.9-rc1", "v10", "vnext"):
        tag(repo, name)
    assert run(repo, "--version", "1.2.0").returncode == 0


def test_version_changelog_heading_missing(repo: Path) -> None:
    result = run(repo, "--version", "1.2.1")
    assert result.returncode == 1
    assert "no '## [1.2.1]' heading" in result.stdout


def test_version_unreleased_not_empty(repo: Path) -> None:
    (repo / "CHANGELOG.md").write_text("## [Unreleased]\n\n- pending\n\n## [1.2.0] - 2026-01-01\n")
    git(repo, "commit", "-am", "dirty")
    git(repo, "push", "origin", "main")
    result = run(repo, "--version", "1.2.0")
    assert result.returncode == 1
    assert "not empty" in result.stdout


def test_version_changelog_order_and_missing_unreleased(repo: Path) -> None:
    (repo / "CHANGELOG.md").write_text("## [1.2.0] - 2026-01-01\n\n## [Unreleased]\n")
    git(repo, "commit", "-am", "swap")
    git(repo, "push", "origin", "main")
    assert "must come after" in run(repo, "--version", "1.2.0").stdout
    (repo / "CHANGELOG.md").write_text("## [1.2.0] - 2026-01-01\n")
    git(repo, "commit", "-am", "no unreleased")
    git(repo, "push", "origin", "main")
    assert "no '## [Unreleased]' heading" in run(repo, "--version", "1.2.0").stdout


def test_version_changelog_unreadable(repo: Path) -> None:
    (repo / "CHANGELOG.md").unlink()
    git(repo, "commit", "-am", "drop")
    git(repo, "push", "origin", "main")
    assert "cannot be read" in run(repo, "--version", "1.2.0").stdout


def test_several_problems_one_line_each(repo: Path) -> None:
    tag(repo, "v1.2.0")
    result = run(repo, "--version", "1.2.0")
    errors = [line for line in result.stdout.splitlines() if line.startswith("::error::")]
    assert len(errors) == 2


def test_tag_ok_and_latest(repo: Path) -> None:
    tag(repo, "v1.2.0")
    result = run(repo, "--tag", "v1.2.0")
    assert result.returncode == 0
    assert result.stdout.strip() == "latest=true"


def test_tag_older_patch_is_not_latest(repo: Path) -> None:
    tag(repo, "v1.1.0")
    tag(repo, "v1.0.1")
    result = run(repo, "--tag", "v1.0.1")
    assert result.returncode == 0
    assert result.stdout.strip() == "latest=false"


def test_tag_bad_form(repo: Path) -> None:
    tag(repo, "v1.0")
    result = run(repo, "--tag", "v1.0")
    assert result.returncode == 1
    assert "::error::" in result.stdout


def test_tag_not_on_main(repo: Path) -> None:
    git(repo, "checkout", "-b", "side")
    (repo / "x").write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "side")
    tag(repo, "v2.0.0")
    result = run(repo, "--tag", "v2.0.0")
    assert result.returncode == 1
    assert "not on origin/main" in result.stdout


def test_requires_a_mode(repo: Path) -> None:
    assert run(repo).returncode == 2
