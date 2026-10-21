"""The Permissions page: the rules each recipe needs, per recipe, as a union, or per group."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from privacyfence_cockpit.models import iso
from privacyfence_cockpit.permissions import (
    PermissionsReport,
    collect,
    merge,
    render_merged,
    render_recipe,
)
from privacyfence_cockpit.web.auth import require_csrf
from privacyfence_cockpit.web.render import redirect, render

VIEWS = ("recipe", "union", "group")


@dataclass(frozen=True)
class Line:
    indent: int
    text: str


@dataclass(frozen=True)
class Block:
    title: str
    lines: tuple[Line, ...]
    created_at: str | None = None
    commit_sha: str | None = None


def _lines(rendered: list[str]) -> tuple[str, tuple[Line, ...]]:
    title, *rest = rendered
    return title, tuple(Line(len(line) - len(line.lstrip(" ")), line.strip()) for line in rest)


def _running(request: Request) -> bool:
    task: asyncio.Task[None] | None = getattr(request.app.state, "permissions_task", None)
    return task is not None and not task.done()


def _stored(request: Request) -> dict[str, Any]:
    rows = request.app.state.cockpit.db.query("SELECT * FROM permissions_reports")
    return {row["recipe_path"]: row for row in rows}


def _report(row: Any) -> PermissionsReport:
    if row["report_json"] is None:
        return PermissionsReport(row["recipe_path"], None, row["error"] or "no report")
    return PermissionsReport(row["recipe_path"], json.loads(row["report_json"]), None)


async def permissions_page(request: Request) -> Response:
    cockpit = request.app.state.cockpit
    view = request.query_params.get("view", "recipe")
    if view not in VIEWS:
        view = "recipe"
    recipes = cockpit.recipes()
    stored = _stored(request)
    present = [r for r in recipes if r.path in stored]
    reports = [_report(stored[r.path]) for r in present]
    blocks: list[Block] = []
    if view == "recipe":
        for recipe, report in zip(present, reports, strict=True):
            row = stored[recipe.path]
            title, lines = _lines(render_recipe(report))
            blocks.append(Block(title, lines, row["created_at"], row["commit_sha"]))
    else:
        groups = {r.path: r.group for r in recipes}
        for merged in merge(reports, "union" if view == "union" else "group", groups):
            title, lines = _lines(render_merged([merged]))
            blocks.append(Block(title, lines))
    context = {
        "view": view,
        "views": VIEWS,
        "blocks": blocks,
        "uncollected": [r.path for r in recipes if r.path not in stored],
        "running": _running(request),
        "tz": cockpit.effective().timezone,
    }
    return render(request, "permissions.html", context)


async def _refresh(request: Request) -> None:
    cockpit = request.app.state.cockpit
    for recipe in cockpit.recipes():
        report = await collect(recipe, cockpit.runner)
        head = await asyncio.to_thread(cockpit.git.head)
        cockpit.db.execute(
            "INSERT INTO permissions_reports (recipe_path, commit_sha, created_at, report_json, error) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(recipe_path) DO UPDATE SET commit_sha = excluded.commit_sha, "
            "created_at = excluded.created_at, report_json = excluded.report_json, error = excluded.error",
            (
                recipe.path,
                head,
                iso(cockpit.clock()),
                None if report.data is None else json.dumps(report.data),
                report.error,
            ),
        )


async def refresh(request: Request) -> Response:
    await require_csrf(request)
    view = request.query_params.get("view", "recipe")
    target = f"/permissions?view={view}" if view in VIEWS else "/permissions"
    if _running(request):
        return redirect(target, "Already refreshing.")
    request.app.state.permissions_task = asyncio.create_task(_refresh(request))
    return redirect(target, "Refreshing…")


ROUTES: list[BaseRoute] = [
    Route("/permissions", permissions_page),
    Route("/permissions/refresh", refresh, methods=["POST"]),
]
