"""The Starlette application: routes, templates, static files and the background-task lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jinja2 import Environment, PackageLoader, select_autoescape
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from privacyfence_cockpit.models import OUTCOME_LABELS, Outcome, parse_iso
from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web import (
    routes_auth,
    routes_code,
    routes_notifications,
    routes_permissions,
    routes_runs,
    routes_schedules,
    routes_settings,
    routes_tokens,
)
from privacyfence_cockpit.web.auth import AuthMiddleware, LoginThrottle, SecurityHeadersMiddleware
from privacyfence_cockpit.web.render import render


def _environment() -> Environment:
    environment = Environment(
        loader=PackageLoader("privacyfence_cockpit.web", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    environment.filters["localtime"] = localtime
    environment.filters["outcome_label"] = outcome_label
    environment.filters["sha7"] = sha7
    return environment


def localtime(iso_text: str | None, tz: str) -> str:
    if not iso_text:
        return ""
    try:
        local = parse_iso(iso_text).astimezone(ZoneInfo(tz))
    except (ValueError, ZoneInfoNotFoundError):
        return iso_text
    return f"{local:%Y-%m-%d %H:%M} {tz}"


def outcome_label(value: Any) -> str:
    if value is None:
        return ""
    try:
        return OUTCOME_LABELS[Outcome(value)]
    except ValueError:
        return str(value)


def sha7(value: str | None) -> str:
    return (value or "")[:7]


async def healthz(request: Request) -> Response:
    try:
        request.app.state.cockpit.db.query_one("SELECT 1")
    except Exception:
        return JSONResponse({"status": "error"}, status_code=503)
    return JSONResponse({"status": "ok"})


async def home(request: Request) -> Response:
    # Temporary: the Overview page (p15) replaces this route and template.
    return render(request, "home_placeholder.html", {})


def create_app(cockpit: Cockpit, *, start_background: bool = True) -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        if start_background:
            await cockpit.start()
        try:
            yield
        finally:
            if start_background:
                await cockpit.stop()

    routes: list[BaseRoute] = [
        *routes_auth.ROUTES,
        *routes_runs.ROUTES,
        *routes_schedules.ROUTES,
        *routes_code.ROUTES,
        *routes_tokens.ROUTES,
        *routes_permissions.ROUTES,
        *routes_notifications.ROUTES,
        *routes_settings.ROUTES,
        Route("/healthz", healthz),
        Route("/", home),
        Mount("/static", StaticFiles(packages=[("privacyfence_cockpit.web", "static")]), name="static"),
    ]
    # The first entry is the outermost, so even the auth middleware's redirects carry the headers.
    middleware = [Middleware(SecurityHeadersMiddleware), Middleware(AuthMiddleware, cockpit=cockpit)]
    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
    app.state.cockpit = cockpit
    app.state.login_throttle = LoginThrottle(cockpit.clock)
    app.state.templates = Jinja2Templates(env=_environment())
    return app
