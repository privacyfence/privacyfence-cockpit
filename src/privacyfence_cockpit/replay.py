"""Live replay: the leading read-only part of a recipe, run against real data without writing."""

from __future__ import annotations

from pathlib import Path

import yaml

REPLAY_ALLOWLIST = frozenset(
    {
        "drive_list_files",
        "drive_download_file",
        "drive_sheets_get_values",
        "salesforce_export_report",
        "salesforce_run_report",
        "jira_export_issues",
        "assets_export_objects",
        "grist_export_records",
        "grist_export_sql",
        "grist_get_records",
    }
)
REPLAY_FILE = ".replay.yaml"


def cut_replay(recipe_yaml: Path) -> Path | None:
    """Write the steps before the first non-allowlisted tool next to the recipe; None when none are kept."""
    document = yaml.safe_load(recipe_yaml.read_text(encoding="utf-8"))
    steps = document.get("steps") or []
    kept = []
    for step in steps:
        if isinstance(step, dict) and "tool" in step and step["tool"] not in REPLAY_ALLOWLIST:
            break
        kept.append(step)
    if not kept:
        return None
    target = recipe_yaml.parent / REPLAY_FILE
    target.write_text(yaml.safe_dump({**document, "steps": kept}, sort_keys=False), encoding="utf-8")
    return target
