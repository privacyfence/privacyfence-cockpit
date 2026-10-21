"""Overview, jobs, runs with live logs, and history."""

from __future__ import annotations

import asyncio
import io
import json
import re
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode

import yaml
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, Response, StreamingResponse
from starlette.routing import BaseRoute, Route

from privacyfence_cockpit.history import HistoryFilter, RunRow
from privacyfence_cockpit.jobs import Job, JobKind
from privacyfence_cockpit.models import OUTCOME_LABELS, Mode, Outcome, iso
from privacyfence_cockpit.recipes import Recipe, group_names
from privacyfence_cockpit.scheduler import next_fire, schedules_for
from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web.auth import require_csrf
from privacyfence_cockpit.web.render import redirect, render

LOG_POLL_SECONDS = 0.5
PING_SECONDS = 15.0
PAGE_SIZE = 50
JOB_ROWS_LIMIT = 500
LOG_DELETED_LINE = "log deleted after the retention period"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILTER_KEYS = ("recipe", "outcome", "trigger", "mode", "since", "until")


def _cockpit(request: Request) -> Cockpit:
    cockpit: Cockpit = request.app.state.cockpit
    return cockpit


def _describe(job: Job) -> str:
    if job.kind is not JobKind.RUN:
        return job.kind.value.replace("_", " ")
    return f"{', '.join(job.selectors) or 'all recipes'} ({job.mode.value}, {job.trigger})"


def _recipe_server(recipe: Recipe) -> str | None:
    try:
        data = yaml.safe_load(recipe.yaml_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    server = data.get("server") if isinstance(data, dict) else None
    return server if isinstance(server, str) and server else None


@dataclass(frozen=True)
class OverviewRow:
    recipe: Recipe
    last: RunRow | None
    next_fire: str | None
    server: str | None


async def overview(request: Request) -> Response:
    cockpit = _cockpit(request)
    recipes = cockpit.recipes()
    config, _ = cockpit.config_or_default()
    eff = cockpit.effective()
    now = cockpit.clock()
    last = cockpit.history.last_by_recipe()
    groups: list[tuple[str, list[OverviewRow]]] = []
    for name in group_names(recipes):
        rows: list[OverviewRow] = []
        for recipe in (r for r in recipes if r.group == name):
            fires = [
                next_fire(s, eff.timezone, now)
                for s in schedules_for(recipe, config, recipes)
                if not cockpit.scheduler.is_paused(s.name)
            ]
            rows.append(
                OverviewRow(recipe, last.get(recipe.path), iso(min(fires)) if fires else None, _recipe_server(recipe))
            )
        groups.append((name, rows))
    running = cockpit.queue.running()
    context = {
        "groups": groups,
        "tz": eff.timezone,
        "running": running,
        "running_text": _describe(running) if running else None,
        "queued": len(cockpit.queue.queued()),
        "modes": [m.value for m in (Mode.UNATTENDED, Mode.ATTENDED, Mode.PREFLIGHT, Mode.CHECK, Mode.REPLAY)],
    }
    return render(request, "overview.html", context)


async def post_run(request: Request) -> Response:
    form = await require_csrf(request)
    cockpit = _cockpit(request)
    selected = tuple(v for v in form.getlist("recipe") if isinstance(v, str) and v)
    if not selected:
        return redirect("/", flash="Pick at least one recipe.")
    try:
        mode = Mode(str(form.get("mode") or Mode.UNATTENDED.value))
    except ValueError:
        return redirect("/", flash="Pick a valid mode.")
    job = Job(JobKind.RUN, "manual", selectors=selected, mode=mode)
    cockpit.queue.submit(job)
    return redirect(f"/jobs/{job.id}")


def _job_context(cockpit: Cockpit, job_id: str) -> dict[str, Any]:
    queued = next((j for j in cockpit.queue.queued() if j.id == job_id), None)
    running = cockpit.queue.running()
    running_here = running if running is not None and running.id == job_id else None
    rows = cockpit.history.query(HistoryFilter(job_id=job_id), limit=JOB_ROWS_LIMIT)
    if queued is None and running_here is None and not rows:
        raise HTTPException(404, "No such job.")
    rows.reverse()
    job = queued or running_here
    state = "queued" if queued else "running" if running_here else "finished"
    return {
        "job_id": job_id,
        "rows": rows,
        "state": state,
        "active": state != "finished",
        "title": _describe(job) if job else (rows[0].trigger if rows else job_id),
        "tz": cockpit.effective().timezone,
    }


async def job_page(request: Request) -> Response:
    return render(request, "job.html", _job_context(_cockpit(request), request.path_params["job_id"]))


async def job_rows(request: Request) -> Response:
    return render(request, "job_rows.html", _job_context(_cockpit(request), request.path_params["job_id"]))


async def job_cancel(request: Request) -> Response:
    await require_csrf(request)
    job_id: str = request.path_params["job_id"]
    cancelled = _cockpit(request).queue.cancel(job_id)
    return redirect(f"/jobs/{job_id}", flash=None if cancelled else "The job has already started.")


def _run_id(request: Request) -> int:
    try:
        return int(request.path_params["run_id"])
    except ValueError:
        raise HTTPException(404, "No such run.") from None


def _run_or_404(cockpit: Cockpit, run_id: int) -> RunRow:
    row = cockpit.history.get(run_id)
    if row is None:
        raise HTTPException(404, "No such run.")
    return row


async def run_page(request: Request) -> Response:
    cockpit = _cockpit(request)
    row = _run_or_404(cockpit, _run_id(request))
    return render(request, "run.html", {"run": row, "tz": cockpit.effective().timezone})


def _sse(event: str, data: Mapping[str, str]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _log_events(cockpit: Cockpit, run_id: int) -> AsyncIterator[str]:
    position = 0
    pending = b""
    last_sent = time.monotonic()
    while True:
        row = cockpit.history.get(run_id)
        if row is None:
            return
        # Read the row first: a finished row means the file below is complete.
        finished = row.status == "finished"
        path = cockpit.paths.root / row.log_path if row.log_path else None
        if path is not None and path.is_file():
            with path.open("rb") as handle:
                handle.seek(position)
                chunk = handle.read()
            position += len(chunk)
            pending += chunk
            *lines, pending = pending.split(b"\n")
            if finished and pending:
                lines.append(pending)
                pending = b""
            for raw in lines:
                yield _sse("line", {"line": raw.decode("utf-8", errors="replace").rstrip("\r")})
                last_sent = time.monotonic()
        elif finished:
            yield _sse("line", {"line": LOG_DELETED_LINE})
        if finished:
            yield _sse("end", {"outcome": OUTCOME_LABELS[row.outcome] if row.outcome else ""})
            return
        if time.monotonic() - last_sent >= PING_SECONDS:
            yield ": ping\n\n"
            last_sent = time.monotonic()
        await asyncio.sleep(LOG_POLL_SECONDS)


async def run_events(request: Request) -> Response:
    cockpit = _cockpit(request)
    run_id = _run_id(request)
    _run_or_404(cockpit, run_id)
    return StreamingResponse(
        _log_events(cockpit, run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


async def run_log(request: Request) -> Response:
    cockpit = _cockpit(request)
    run_id = _run_id(request)
    row = _run_or_404(cockpit, run_id)
    path = cockpit.paths.root / row.log_path if row.log_path else None
    if path is None or not path.is_file():
        raise HTTPException(404, LOG_DELETED_LINE.capitalize() + ".")
    return FileResponse(path, media_type="text/plain", filename=f"run-{run_id}.log")


async def run_stop(request: Request) -> Response:
    await require_csrf(request)
    run_id = _run_id(request)
    cockpit = _cockpit(request)
    _run_or_404(cockpit, run_id)
    stopped = cockpit.runner.stop(run_id)
    return redirect(f"/runs/{run_id}", flash="Stopping the run…" if stopped else "That run is not running.")


def _parse_filter(params: Mapping[str, str]) -> tuple[HistoryFilter, dict[str, str]]:
    """The filter and the query pairs that made it; invalid values are dropped."""
    used: dict[str, str] = {}
    values: dict[str, Any] = {}
    for key in ("recipe", "trigger"):
        if params.get(key):
            used[key] = values[key] = params[key]
    for key, kind in (("outcome", Outcome), ("mode", Mode)):
        try:
            values[key] = kind(params[key]) if params.get(key) else None
        except ValueError:
            values[key] = None
        if values[key] is not None:
            used[key] = params[key]
    for key in ("since", "until"):
        text = params.get(key, "")
        if _DATE.match(text):
            try:
                values[key] = date.fromisoformat(text)
            except ValueError:
                continue
            used[key] = text
    return HistoryFilter(**{k: v for k, v in values.items() if v is not None}), used


async def history_page(request: Request) -> Response:
    cockpit = _cockpit(request)
    f, used = _parse_filter(request.query_params)
    total = cockpit.history.count(f)
    pages = max(1, -(-total // PAGE_SIZE))
    try:
        page = int(request.query_params.get("page", "1"))
    except ValueError:
        page = 1
    page = min(max(page, 1), pages)
    context = {
        "rows": cockpit.history.query(f, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE),
        "total": total,
        "page": page,
        "pages": pages,
        "used": used,
        "query": urlencode(used),
        "recipes": [r.path for r in cockpit.recipes()],
        "outcomes": [(o.value, OUTCOME_LABELS[o]) for o in Outcome],
        "modes": [m.value for m in Mode],
        "tz": cockpit.effective().timezone,
    }
    return render(request, "history.html", context)


async def history_csv(request: Request) -> Response:
    f, _ = _parse_filter(request.query_params)
    out = io.StringIO()
    _cockpit(request).history.export_csv(f, out)
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="runs.csv"'},
    )


ROUTES: list[BaseRoute] = [
    Route("/", overview),
    Route("/run", post_run, methods=["POST"]),
    Route("/jobs/{job_id}", job_page),
    Route("/jobs/{job_id}/rows", job_rows),
    Route("/jobs/{job_id}/cancel", job_cancel, methods=["POST"]),
    Route("/runs/{run_id}", run_page),
    Route("/runs/{run_id}/events", run_events),
    Route("/runs/{run_id}/log", run_log),
    Route("/runs/{run_id}/stop", run_stop, methods=["POST"]),
    Route("/history", history_page),
    Route("/history/runs.csv", history_csv),
]
