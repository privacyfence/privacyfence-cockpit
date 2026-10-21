#!/usr/bin/env python3
"""Pre-flight checks for the Release workflow (stdlib only).

``--version X.Y.Z`` checks a manual dispatch, ``--tag vX.Y.Z`` checks a pushed tag. Each problem is
printed as one ``::error::`` line and the exit code is 1; on success the last line is
``latest=true`` or ``latest=false`` (true when this version is the highest release tag).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
MAIN = "origin/main"


def parse(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)  # noqa: S603, S607 - fixed argv, no shell


def existing_versions() -> list[tuple[int, int, int]]:
    tags = git("tag", "--list", "v*").stdout.split()
    return [parse(tag[1:]) for tag in tags if TAG_RE.match(tag)]


def changelog_problems(version: str) -> list[str]:
    try:
        with open("CHANGELOG.md", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return ["CHANGELOG.md cannot be read"]
    unreleased = next((i for i, line in enumerate(lines) if line.strip() == "## [Unreleased]"), None)
    heading = re.compile(rf"^## \[{re.escape(version)}\]")
    released = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    problems = []
    if released is None:
        problems.append(f"CHANGELOG.md has no '## [{version}]' heading")
    if unreleased is None:
        problems.append("CHANGELOG.md has no '## [Unreleased]' heading")
    if unreleased is not None:
        end = next((i for i in range(unreleased + 1, len(lines)) if lines[i].startswith("## [")), len(lines))
        if released is not None and released < unreleased:
            problems.append(f"'## [{version}]' must come after '## [Unreleased]'")
        elif any(line.strip() for line in lines[unreleased + 1 : end]):
            problems.append("the '## [Unreleased]' section is not empty; move it under the release heading")
    return problems


def check_version(version: str) -> tuple[list[str], bool]:
    if not VERSION_RE.match(version):
        return [f"version '{version}' is not of the form X.Y.Z"], False
    problems = []
    head = git("rev-parse", "HEAD").stdout.strip()
    main = git("rev-parse", MAIN).stdout.strip()
    if not main or head != main:
        problems.append(f"HEAD is not {MAIN}; run the release from the tip of main")
    if git("rev-parse", "--verify", "--quiet", f"refs/tags/v{version}").returncode == 0:
        problems.append(f"tag v{version} already exists")
    highest = max(existing_versions(), default=None)
    if highest is not None and parse(version) <= highest:
        problems.append(f"version {version} does not sort after the existing tag v{'.'.join(map(str, highest))}")
    problems += changelog_problems(version)
    return problems, True


def check_tag(tag: str) -> tuple[list[str], bool]:
    if not TAG_RE.match(tag):
        return [f"tag '{tag}' is not of the form vX.Y.Z"], False
    problems = []
    if git("merge-base", "--is-ancestor", f"refs/tags/{tag}^{{commit}}", MAIN).returncode != 0:
        problems.append(f"tag {tag} is not on {MAIN}")
    latest = parse(tag[1:]) >= max(existing_versions(), default=(0, 0, 0))
    return problems, latest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--version", help="X.Y.Z of a manual release")
    group.add_argument("--tag", help="vX.Y.Z of a pushed tag")
    args = parser.parse_args(argv)
    problems, latest = check_version(args.version) if args.version is not None else check_tag(args.tag)
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print(f"latest={'true' if latest else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
