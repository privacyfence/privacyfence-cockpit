"""``cockpit.yaml`` in the recipe repository: server, timezone, timeout and schedules."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from croniter import croniter

from privacyfence_cockpit.recipes import Recipe, SelectionError, select

_TOP_KEYS = {"version", "server", "timezone", "timeout_minutes", "schedules"}
_SCHEDULE_KEYS = {"name", "cron", "run", "repeat", "stop_on_error"}
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


@dataclass(frozen=True)
class Schedule:
    name: str
    cron: str
    run: tuple[str, ...]
    repeat: int = 1
    stop_on_error: bool = False
    source: str = "repo"  # "repo" (cockpit.yaml) or "ui" (the dashboard's database)


@dataclass(frozen=True)
class CockpitConfig:
    server: str | None = None
    timezone: str | None = None
    timeout_minutes: int = 60
    schedules: tuple[Schedule, ...] = ()


class CockpitConfigError(ValueError):
    """Message always starts with ``cockpit.yaml: ``."""


class ScheduleInputError(ValueError):
    """A schedule entered in the dashboard is invalid; the message is shown to the owner."""


def _fail(message: str) -> CockpitConfigError:
    return CockpitConfigError(f"cockpit.yaml: {message}")


def _is_int(value: object, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _check_keys(mapping: dict[Any, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(str(k) for k in mapping if k not in allowed)
    if unknown:
        raise _fail(f"unknown key {unknown[0]!r} in {where}")


def _parse_schedule(raw: Any, index: int, seen: set[str]) -> Schedule:
    where = f"schedules[{index}]"
    if not isinstance(raw, dict):
        raise _fail(f"{where} must be a mapping")
    _check_keys(raw, _SCHEDULE_KEYS, where)
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise _fail(f"{where}: name must match ^[a-z0-9][a-z0-9-]{{0,40}}$")
    if name in seen:
        raise _fail(f"schedule name {name!r} is used twice")
    seen.add(name)
    cron = raw.get("cron")
    if not isinstance(cron, str) or not croniter.is_valid(cron):
        raise _fail(f"schedule {name}: cron is not a valid cron expression")
    run = raw.get("run")
    if not isinstance(run, list) or not run or not all(isinstance(r, str) for r in run):
        raise _fail(f"schedule {name}: run must be a non-empty list of strings")
    repeat = raw.get("repeat", 1)
    if not _is_int(repeat, 1, 100):
        raise _fail(f"schedule {name}: repeat must be a number between 1 and 100")
    stop_on_error = raw.get("stop_on_error", False)
    if not isinstance(stop_on_error, bool):
        raise _fail(f"schedule {name}: stop_on_error must be true or false")
    return Schedule(name, cron, tuple(run), repeat, stop_on_error)


def parse_ui_schedule(raw: dict[str, Any]) -> Schedule:
    """Validate a schedule entered in the dashboard with the rules of cockpit.yaml; the message has no file prefix."""
    try:
        parsed = _parse_schedule(raw, 0, set())
    except CockpitConfigError as exc:
        message = str(exc).removeprefix("cockpit.yaml: ")
        raise ScheduleInputError(message.removeprefix("schedules[0]: ")) from exc
    return replace(parsed, source="ui")


def load_cockpit_yaml(repo: Path) -> CockpitConfig:
    file = repo / "cockpit.yaml"
    if not file.is_file():
        return CockpitConfig()
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise _fail(f"cannot parse the file ({type(exc).__name__})") from exc
    if not isinstance(data, dict):
        raise _fail("the file must be a mapping")
    _check_keys(data, _TOP_KEYS, "the top level")
    version = data.get("version")
    if not _is_int(version, 1, 1):
        raise _fail("version must be 1")
    server = data.get("server")
    if server is not None and not (
        isinstance(server, str) and server.startswith(("http://", "https://"))
    ):
        raise _fail("server must start with http:// or https://")
    timezone = data.get("timezone")
    if timezone is not None:
        try:
            if not isinstance(timezone, str):
                raise ValueError(timezone)
            ZoneInfo(timezone)
        except (ValueError, OSError, LookupError) as exc:
            raise _fail("timezone is not a valid time zone name") from exc
    timeout = data.get("timeout_minutes", 60)
    if not _is_int(timeout, 1, 1440):
        raise _fail("timeout_minutes must be a number between 1 and 1440")
    raw_schedules = data.get("schedules", [])
    if not isinstance(raw_schedules, list):
        raise _fail("schedules must be a list")
    seen: set[str] = set()
    schedules = tuple(_parse_schedule(s, i, seen) for i, s in enumerate(raw_schedules))
    return CockpitConfig(server, timezone, timeout, schedules)


def selector_problems(config: CockpitConfig, recipes: Sequence[Recipe]) -> list[str]:
    problems: list[str] = []
    for schedule in config.schedules:
        for selector in schedule.run:
            try:
                select(recipes, [selector])
            except SelectionError:
                problems.append(f"schedule {schedule.name}: no recipe or group matches {selector!r}")
    return problems
