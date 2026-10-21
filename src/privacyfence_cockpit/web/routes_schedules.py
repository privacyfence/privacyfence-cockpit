"""The Schedules page: the schedules of cockpit.yaml, pause and resume, and Run now."""

from __future__ import annotations

from dataclasses import dataclass

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from privacyfence_cockpit.cockpit_yaml import CockpitConfig, Schedule
from privacyfence_cockpit.history import HistoryFilter, RunRow
from privacyfence_cockpit.jobs import SCHEDULE_PREFIX, Job, JobKind
from privacyfence_cockpit.models import Outcome, iso
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
    context = {"error": error, "views": views, "missed": missed, "tz": tz}
    return render(request, "schedules.html", context)


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
    Route("/schedules/{name}/pause", pause, methods=["POST"]),
    Route("/schedules/{name}/resume", resume, methods=["POST"]),
    Route("/schedules/{name}/run", run_now, methods=["POST"]),
]
