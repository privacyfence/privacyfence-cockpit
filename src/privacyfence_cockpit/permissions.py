"""Collects `privacyfence-sync permissions --json` per recipe and merges the needed rules."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from privacyfence_cockpit.recipes import Recipe

if TYPE_CHECKING:
    from privacyfence_cockpit.runner import Runner

NO_SUGGESTIONS = "update PrivacyFence to see suggestions"
_NEEDS_RULE = ("needs_rule", "unknown")


@dataclass(frozen=True)
class PermissionsReport:
    recipe: str
    data: dict[str, Any] | None
    error: str | None


@dataclass(frozen=True)
class NeededRule:
    group: str
    label: str
    value: tuple[str, ...] | None
    verbs: tuple[str, ...]
    recipes: tuple[str, ...]


@dataclass(frozen=True)
class MergedSet:
    title: str
    rules: tuple[NeededRule, ...]
    tools: tuple[str, ...]
    unsuggested: tuple[str, ...]
    errors: tuple[str, ...]


async def collect(recipe: Recipe, runner: Runner, *, timeout: float = 120.0) -> PermissionsReport:
    prepared = runner.command_env(recipe)
    if prepared is None:
        return PermissionsReport(recipe.path, None, "the recipe environment is not ready")
    venv, env = prepared
    sync = venv / "bin" / "privacyfence-sync"
    try:
        proc = await asyncio.create_subprocess_exec(
            str(sync),
            "permissions",
            str(recipe.yaml_path),
            "--json",
            cwd=recipe.dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        return PermissionsReport(recipe.path, None, f"could not start privacyfence-sync: {error.strerror}")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        return PermissionsReport(recipe.path, None, f"privacyfence-sync permissions timed out after {timeout:g} s")
    if proc.returncode != 0:
        lines = err.decode("utf-8", errors="replace").splitlines()
        first = lines[0] if lines else ""
        return PermissionsReport(
            recipe.path,
            None,
            f"privacyfence-sync permissions exited with {proc.returncode}: {first[:200]}",
        )
    try:
        data = json.loads(out.decode("utf-8", errors="replace"))
    except ValueError:
        return PermissionsReport(recipe.path, None, "privacyfence-sync permissions printed invalid JSON")
    if not isinstance(data, dict):
        return PermissionsReport(recipe.path, None, "privacyfence-sync permissions printed invalid JSON")
    return PermissionsReport(recipe.path, data, None)


def _value(raw: Any) -> tuple[str, ...] | None:
    return None if raw is None else tuple(str(v) for v in raw)


def _merge_one(title: str, reports: Sequence[PermissionsReport]) -> MergedSet:
    labels: dict[tuple[str, tuple[str, ...] | None], str] = {}
    verbs: dict[tuple[str, tuple[str, ...] | None], set[str]] = {}
    recipes: dict[tuple[str, tuple[str, ...] | None], list[str]] = {}
    tools: set[str] = set()
    unsuggested: list[str] = []
    errors: list[str] = []
    for report in reports:
        if report.data is None:
            errors.append(f"{report.recipe}: {report.error}")
            continue
        for need in report.data.get("needed", []):
            key = (need["group"], _value(need.get("value")))
            labels.setdefault(key, need["label"])
            verbs.setdefault(key, set()).update(need.get("verbs", []))
            if report.recipe not in recipes.setdefault(key, []):
                recipes[key].append(report.recipe)
        tools.update(report.data.get("tools", []))
        for step in report.data.get("steps", []):
            if step.get("status") in _NEEDS_RULE and step.get("suggested_rules") is None:
                unsuggested.append(f"{report.recipe} [{step['id']}] {step['tool']}")
    rules = tuple(
        NeededRule(key[0], labels[key], key[1], tuple(sorted(verbs[key])), tuple(recipes[key]))
        for key in verbs
    )
    return MergedSet(title, rules, tuple(sorted(tools)), tuple(unsuggested), tuple(errors))


def merge(
    reports: Sequence[PermissionsReport], by: Literal["union", "group"], groups: Mapping[str, str]
) -> list[MergedSet]:
    if by == "union":
        return [_merge_one("All recipes", reports)]
    order: list[str] = []
    for report in reports:
        name = groups.get(report.recipe, report.recipe.split("/", 1)[0])
        if name not in order:
            order.append(name)
    return [
        _merge_one(
            f"Group {name}",
            [r for r in reports if groups.get(r.recipe, r.recipe.split("/", 1)[0]) == name],
        )
        for name in order
    ]


def rule_text(label: str, value: Sequence[str] | None, verbs: Sequence[str]) -> str:
    if value is None:
        return f"{label}, naming what it reaches at run time ({', '.join(verbs)})"
    return f"{label} = {', '.join(value)} ({', '.join(verbs)})"


def _suggestion_text(s: Mapping[str, Any]) -> str:
    return rule_text(s["label"], _value(s.get("value")), [s["verb"]])


def render_recipe(report: PermissionsReport) -> list[str]:
    lines = [report.recipe]
    if report.data is None:
        return [*lines, f"  error: {report.error}"]
    for step in report.data.get("steps", []):
        lines.append(f"  [{step['id']}] {step['tool']}: {step['status']}")
        if step["status"] in _NEEDS_RULE:
            suggestions = step.get("suggested_rules")
            if suggestions is None:
                lines.append(f"      {NO_SUGGESTIONS}")
            else:
                lines.extend(f"      {_suggestion_text(s)}" for s in suggestions)
    lines.append("  Rules:")
    needed = report.data.get("needed", [])
    if needed:
        lines.extend(
            f"    {rule_text(n['label'], _value(n.get('value')), n.get('verbs', []))}" for n in needed
        )
    else:
        lines.append("    none")
    lines.append(f"  Tools: {', '.join(report.data.get('tools', []))}")
    return lines


def render_merged(sets: Sequence[MergedSet]) -> list[str]:
    lines: list[str] = []
    for merged in sets:
        lines.append(merged.title)
        lines.append("  Rules:")
        lines.extend(
            f"    {rule_text(r.label, r.value, r.verbs)}   [{', '.join(r.recipes)}]" for r in merged.rules
        )
        lines.append(f"  Tools: {', '.join(merged.tools)}")
        if merged.unsuggested:
            lines.append(f"  Steps without suggestions ({NO_SUGGESTIONS}):")
            lines.extend(f"    {entry}" for entry in merged.unsuggested)
        if merged.errors:
            lines.append("  Errors:")
            lines.extend(f"    {entry}" for entry in merged.errors)
    return lines
