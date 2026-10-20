"""Rendering helpers shared by every page: the template context, redirects and the flash cookie."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from privacyfence_cockpit import __version__
from privacyfence_cockpit.web.context import banners

FLASH_COOKIE = "cockpit_flash"
FLASH_MAX_CHARS = 300


def _read_flash(request: Request) -> str | None:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    try:
        return base64.urlsafe_b64decode(raw.encode()).decode()
    except (binascii.Error, UnicodeError):
        return None


def render(request: Request, template: str, context: dict[str, Any], status_code: int = 200) -> HTMLResponse:
    cockpit = request.app.state.cockpit
    flash = _read_flash(request)
    nav = request.url.path.strip("/").split("/", 1)[0]
    full = {
        "cockpit": cockpit,
        "banners": banners(cockpit),
        "flash": flash,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "nav": nav,
        "version": __version__,
        "authenticated": getattr(request.state, "authenticated", True),
        **context,
    }
    response: HTMLResponse = request.app.state.templates.TemplateResponse(request, template, full, status_code)
    if request.cookies.get(FLASH_COOKIE) is not None:
        response.delete_cookie(FLASH_COOKIE, path="/")
    return response


def redirect(url: str, flash: str | None = None) -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    if flash is not None:
        value = base64.urlsafe_b64encode(flash[:FLASH_MAX_CHARS].encode()).decode()
        response.set_cookie(FLASH_COOKIE, value, max_age=60, path="/", httponly=True, samesite="strict")
    return response
