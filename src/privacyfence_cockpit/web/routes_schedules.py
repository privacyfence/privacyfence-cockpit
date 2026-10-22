"""The Schedules page: schedules of cockpit.yaml and of the dashboard, create, edit, delete, pause and Run now."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from privacyfence_cockpit.cockpit_yaml import CockpitConfig, Schedule, ScheduleInputError, parse_ui_schedule
from privacyfence_cockpit.history import HistoryFilter, RunRow
from privacyfence_cockpit.jobs import SCHEDULE_PREFIX, Job, JobKind
from privacyfence_cockpit.models import Outcome, iso
from privacyfence_cockpit.recipes import SelectionError, select
from privacyfence_cockpit.scheduler import next_fire
from privacyfence_cockpit.web.auth import require_csrf
from privacyfence_cockpit.web.render import redirect, render

MISSED_LIMIT = 20


@dataclass(frozen=True)
class ScheduleView:
    schedule: Schedule
    paused: bool
    next_fire: str | None
    last_run: RunRow | None


def _find(request: Request, name: str) -> Schedule:
    config: CockpitConfig = request.app.state.cockpit.config_or_default()[0]
    for schedule in config.schedules:
        if schedule.name == name:
            return schedule
    raise HTTPException(404, f"No schedule named {name}.")


async def schedules_page(request: Request) -> Response:
    cockpit = request.app.state.cockpit
    config, error = cockpit.config_or_default()
    tz = cockpit.effective().timezone
    now = cockpit.clock()
    views: list[ScheduleView] = []
    if error is None:
        for schedule in config.schedules:
            paused = cockpit.scheduler.is_paused(schedule.name)
            upcoming = None if paused else iso(next_fire(schedule, tz, now))
            last = cockpit.history.query(HistoryFilter(trigger=f"{SCHEDULE_PREFIX}{schedule.name}"), limit=1)
            views.append(ScheduleView(schedule, paused, upcoming, last[0] if last else None))
    missed = cockpit.history.query(HistoryFilter(outcome=Outcome.MISSED), limit=MISSED_LIMIT)
    from_repo = {s.name for s in config.schedules if s.source == "repo"}
    shadowed = [s.name for s in cockpit.schedule_store.all() if s.name in from_repo]
    context = {"error": error, "views": views, "missed": missed, "tz": tz, "shadowed": shadowed}
    return render(request, "schedules.html", context)


def _own(request: Request, name: str) -> Schedule:
    """A schedule made in the dashboard; those of cockpit.yaml are changed in the recipe repository."""
    schedule: Schedule | None = request.app.state.cockpit.schedule_store.get(name)
    if schedule is None:
        raise HTTPException(404, f"No dashboard schedule named {name}; schedules of cockpit.yaml are edited there.")
    return schedule


def _form_page(
    request: Request,
    *,
    values: Mapping[str, str],
    editing: bool,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    context: dict[str, Any] = {
        "values": values,
        "editing": editing,
        "error": error,
        "tz": request.app.state.cockpit.effective().timezone,
    }
    return render(request, "schedule_form.html", context, status_code)


def _values_of(schedule: Schedule) -> dict[str, str]:
    return {
        "name": schedule.name,
        "cron": schedule.cron,
        "run": "\n".join(schedule.run),
        "repeat": str(schedule.repeat),
        "stop_on_error": "1" if schedule.stop_on_error else "",
    }


async def new_page(request: Request) -> Response:
    values = {"name": "", "cron": "", "run": "", "repeat": "1", "stop_on_error": ""}
    return _form_page(request, values=values, editing=False)


async def edit_page(request: Request) -> Response:
    return _form_page(request, values=_values_of(_own(request, request.path_params["name"])), editing=True)


def _read_form(form: Mapping[str, Any], name: str | None) -> dict[str, str]:
    text = {key: value for key, value in form.items() if isinstance(value, str)}
    return {
        "name": (name if name is not None else text.get("name", "")).strip(),
        "cron": " ".join(text.get("cron", "").split()),
        "run": text.get("run", "").strip(),
        "repeat": text.get("repeat", "").strip(),
        "stop_on_error": "1" if text.get("stop_on_error") else "",
    }


def _build(request: Request, values: Mapping[str, str], *, creating: bool) -> tuple[Schedule | None, str | None]:
    """The schedule for the form values, or the first problem found."""
    cockpit = request.app.state.cockpit
    selectors = [line.strip() for line in values["run"].replace(",", "\n").splitlines() if line.strip()]
    repeat: object = int(values["repeat"]) if values["repeat"].isascii() and values["repeat"].isdigit() else None
    raw = {
        "name": values["name"],
        "cron": values["cron"],
        "run": selectors,
        "repeat": repeat,
        "stop_on_error": bool(values["stop_on_error"]),
    }
    try:
        schedule = parse_ui_schedule(raw)
    except ScheduleInputError as exc:
        return None, str(exc)
    if creating and any(s.name == schedule.name for s in cockpit.config_or_default()[0].schedules):
        return None, f"A schedule named {schedule.name} already exists."
    recipes = cockpit.recipes()
    for selector in schedule.run:
        try:
            select(recipes, [selector])
        except SelectionError:
            return None, f"No recipe or group matches {selector!r}."
    return schedule, None


async def create(request: Request) -> Response:
    form = await require_csrf(request)
    values = _read_form(form, None)
    schedule, error = _build(request, values, creating=True)
    if schedule is None:
        return _form_page(request, values=values, editing=False, error=error, status_code=400)
    request.app.state.cockpit.schedule_store.save(schedule)
    return redirect("/schedules", f"Created {schedule.name}.")


async def update(request: Request) -> Response:
    form = await require_csrf(request)
    name = _own(request, request.path_params["name"]).name
    values = _read_form(form, name)
    schedule, error = _build(request, values, creating=False)
    if schedule is None:
        return _form_page(request, values=values, editing=True, error=error, status_code=400)
    request.app.state.cockpit.schedule_store.save(schedule)
    return redirect("/schedules", f"Saved {name}.")


async def delete(request: Request) -> Response:
    await require_csrf(request)
    name = _own(request, request.path_params["name"]).name
    request.app.state.cockpit.schedule_store.delete(name)
    return redirect("/schedules", f"Deleted {name}.")


async def pause(request: Request) -> Response:
    await require_csrf(request)
    schedule = _find(request, request.path_params["name"])
    request.app.state.cockpit.scheduler.set_paused(schedule.name, True)
    return redirect("/schedules", f"Paused {schedule.name}.")


async def resume(request: Request) -> Response:
    await require_csrf(request)
    schedule = _find(request, request.path_params["name"])
    request.app.state.cockpit.scheduler.set_paused(schedule.name, False)
    return redirect("/schedules", f"Resumed {schedule.name}.")


async def run_now(request: Request) -> Response:
    await require_csrf(request)
    schedule = _find(request, request.path_params["name"])
    job = Job(
        JobKind.RUN,
        "manual",
        selectors=schedule.run,
        repeat=schedule.repeat,
        stop_on_error=schedule.stop_on_error,
    )
    request.app.state.cockpit.queue.submit(job)
    return redirect(f"/jobs/{job.id}")


ROUTES: list[BaseRoute] = [
    Route("/schedules", schedules_page),
    Route("/schedules/new", new_page),
    Route("/schedules/new", create, methods=["POST"]),
    Route("/schedules/{name}/edit", edit_page),
    Route("/schedules/{name}/edit", update, methods=["POST"]),
    Route("/schedules/{name}/delete", delete, methods=["POST"]),
    Route("/schedules/{name}/pause", pause, methods=["POST"]),
    Route("/schedules/{name}/resume", resume, methods=["POST"]),
    Route("/schedules/{name}/run", run_now, methods=["POST"]),
]
