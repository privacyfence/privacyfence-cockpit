"""The Notifications page: webhooks (name, format, last status), add, delete and send test."""

from __future__ import annotations

from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from privacyfence_cockpit.events import EventKind
from privacyfence_cockpit.notify import WebhookFormat
from privacyfence_cockpit.web.auth import require_csrf
from privacyfence_cockpit.web.render import redirect, render

NOTIFYING_EVENTS = {
    EventKind.RUN_FAILED: "A scheduled run ends as recipe error, failed, denied, not signed in or timed out.",
    EventKind.SCHEDULE_MISSED: "Scheduled runs were missed while the cockpit was not running.",
    EventKind.TOKEN_EXPIRING: "A token expires within 14, 7 or 1 day(s).",
    EventKind.TOKEN_INVALID: "PrivacyFence refuses a token, or it has expired.",
    EventKind.VENV_FAILED: "The recipe environment could not be built.",
    EventKind.GIT_FETCH_FAILING: "Fetching the recipe repository has failed for over 24 hours.",
}


def _page(
    request: Request, *, error: str | None = None, name: str = "", fmt: str = "json", status_code: int = 200
) -> Response:
    cockpit = request.app.state.cockpit
    context: dict[str, Any] = {
        "webhooks": cockpit.notifier.list(),
        "formats": [f.value for f in WebhookFormat],
        "events": list(NOTIFYING_EVENTS.values()),
        "tz": cockpit.effective().timezone,
        "error": error,
        "name": name,
        "fmt": fmt,
    }
    return render(request, "notifications.html", context, status_code)


async def notifications_page(request: Request) -> Response:
    return _page(request)


async def add_webhook(request: Request) -> Response:
    form = await require_csrf(request)
    name = str(form.get("name", "")).strip()
    fmt = str(form.get("format", ""))
    url = str(form.get("url", ""))
    try:
        fmt_value = WebhookFormat(fmt)
    except ValueError:
        return _page(request, error="Pick a webhook format.", name=name, status_code=400)
    try:
        # The URL is a secret: it goes straight into the encrypted store and is never rendered back.
        request.app.state.cockpit.notifier.add(name, fmt_value, url)
    except ValueError as error:
        return _page(request, error=str(error), name=name, fmt=fmt, status_code=400)
    return redirect("/notifications", f"Added the webhook {name}.")


def _webhook_id(request: Request) -> int:
    try:
        return int(request.path_params["webhook_id"])
    except ValueError:
        raise HTTPException(404, "Unknown webhook.") from None


async def delete_webhook(request: Request) -> Response:
    await require_csrf(request)
    if not request.app.state.cockpit.notifier.delete(_webhook_id(request)):
        raise HTTPException(404, "Unknown webhook.")
    return redirect("/notifications", "Deleted the webhook.")


async def test_webhook(request: Request) -> Response:
    await require_csrf(request)
    try:
        status = await request.app.state.cockpit.notifier.send_test(_webhook_id(request))
    except ValueError:
        raise HTTPException(404, "Unknown webhook.") from None
    return redirect("/notifications", f"Test notification: {status}")


ROUTES: list[BaseRoute] = [
    Route("/notifications", notifications_page),
    Route("/notifications", add_webhook, methods=["POST"]),
    Route("/notifications/{webhook_id}/delete", delete_webhook, methods=["POST"]),
    Route("/notifications/{webhook_id}/test", test_webhook, methods=["POST"]),
]
