"""The Settings page: owner overrides for the effective settings, and the password."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.web.auth import (
    MIN_PASSWORD_LENGTH,
    WRONG_LOGIN,
    LoginThrottle,
    check_password,
    delete_all_except,
    password_problem,
    require_csrf,
    set_password,
)
from privacyfence_cockpit.web.render import redirect, render


@dataclass(frozen=True)
class NumberField:
    key: str
    label: str
    low: int
    high: int


NUMBER_FIELDS = (
    NumberField("log_retention_days", "Log retention (days)", 1, 3650),
    NumberField("git.fetch_interval_minutes", "Fetch interval (minutes)", 5, 1440),
)


def _form_name(key: str) -> str:
    return key.replace(".", "_")


def _values(db: Database) -> dict[str, str]:
    values = {
        "server_url": db.get_setting("server_url") or "",
        "timezone": db.get_setting("timezone") or "",
        "auto_update": "1" if db.get_setting("git.auto_update") == "1" else "",
    }
    for field in NUMBER_FIELDS:
        values[_form_name(field.key)] = db.get_setting(field.key) or ""
    return values


def _page(
    request: Request,
    *,
    values: Mapping[str, str] | None = None,
    error: str | None = None,
    password_error: str | None = None,
    status_code: int = 200,
) -> Response:
    cockpit = request.app.state.cockpit
    config, config_error = cockpit.config_or_default()
    effective = cockpit.effective()
    context: dict[str, Any] = {
        "values": values if values is not None else _values(cockpit.db),
        "number_fields": [(f, _form_name(f.key)) for f in NUMBER_FIELDS],
        "effective": effective,
        "yaml_server": config.server is not None,
        "yaml_timezone": config.timezone is not None,
        "config_error": config_error,
        "error": error,
        "password_error": password_error,
        "min_length": MIN_PASSWORD_LENGTH,
    }
    return render(request, "settings.html", context, status_code)


async def settings_page(request: Request) -> Response:
    return _page(request)


def _validate(form: Mapping[str, str]) -> tuple[dict[str, str], str | None]:
    """The cleaned form values and the first problem found, if any."""
    values = {
        "server_url": form.get("server_url", "").strip(),
        "timezone": form.get("timezone", "").strip(),
        "auto_update": "1" if form.get("auto_update") else "",
    }
    for field in NUMBER_FIELDS:
        values[_form_name(field.key)] = form.get(_form_name(field.key), "").strip()
    if values["server_url"] and not values["server_url"].startswith(("http://", "https://")):
        return values, "Server URL must start with http:// or https://."
    if values["timezone"]:
        try:
            ZoneInfo(values["timezone"])
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return values, f"Timezone {values['timezone']} is unknown."
    for field in NUMBER_FIELDS:
        text = values[_form_name(field.key)]
        if text and not (text.isascii() and text.isdigit() and field.low <= int(text) <= field.high):
            return values, f"{field.label} must be a whole number between {field.low} and {field.high}."
    return values, None


async def save_settings(request: Request) -> Response:
    form = await require_csrf(request)
    cockpit = request.app.state.cockpit
    text = {key: value for key, value in form.items() if isinstance(value, str)}
    values, error = _validate(text)
    if error is not None:
        return _page(request, values=values, error=error, status_code=400)
    db: Database = cockpit.db
    for key, value in (
        ("server_url", values["server_url"]),
        ("timezone", values["timezone"]),
        *((field.key, values[_form_name(field.key)]) for field in NUMBER_FIELDS),
    ):
        if value:
            db.set_setting(key, value)
        else:
            db.delete_setting(key)
    db.set_setting("git.auto_update", "1" if values["auto_update"] else "0")
    return redirect("/settings", "Saved.")


async def change_password(request: Request) -> Response:
    form = await require_csrf(request)
    cockpit = request.app.state.cockpit
    throttle: LoginThrottle = request.app.state.login_throttle
    refused = throttle.refused_message()
    if refused is not None:
        return _page(request, password_error=refused, status_code=429)
    current = str(form.get("current", ""))
    if not check_password(cockpit.db, current):
        throttle.failure()
        return _page(request, password_error=WRONG_LOGIN, status_code=400)
    problem = password_problem(str(form.get("password", "")), str(form.get("confirm", "")))
    if problem is not None:
        return _page(request, password_error=problem, status_code=400)
    throttle.reset()
    set_password(cockpit.db, str(form.get("password", "")))
    delete_all_except(cockpit.db, request.state.session_id)
    return redirect("/settings", "Password changed. Other sessions were logged out.")


ROUTES: list[BaseRoute] = [
    Route("/settings", settings_page),
    Route("/settings", save_settings, methods=["POST"]),
    Route("/settings/password", change_password, methods=["POST"]),
]
