"""Checks the cockpit makes itself, around the recipe's own `privacyfence-sync check`."""

from __future__ import annotations

import os
import subprocess  # nosec B404  # fixed git argv, no shell
from pathlib import Path

import yaml

from privacyfence_cockpit.recipes import Recipe


def cockpit_problems(recipe: Recipe, repo: Path) -> list[str]:
    try:
        document = yaml.safe_load(recipe.yaml_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        document = None
    if not isinstance(document, dict):
        return ["recipe.yaml is not a YAML mapping"]
    work = str(document.get("work_dir") or "work")
    work_dir = (recipe.dir / work).resolve()
    if not work_dir.is_relative_to(recipe.dir.resolve()):
        return [f"work_dir {work} is outside the recipe folder"]
    rel = os.path.relpath(work_dir, repo.resolve())
    done = subprocess.run(  # noqa: S603  # nosec B603 B607  # fixed argv, no shell
        ["git", "-C", str(repo), "check-ignore", "-q", f"{rel}/"],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if done.returncode == 0:
        return []
    if done.returncode == 1:
        return [f"work_dir {rel} is not git-ignored; add it to .gitignore so source data never reaches git"]
    return ["the recipe repository is not a git checkout"]
