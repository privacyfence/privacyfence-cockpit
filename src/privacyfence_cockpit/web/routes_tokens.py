"""The Tokens page: stored PrivacyFence scoped tokens, their last check, and set, delete and check-all."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from privacyfence_cockpit.crypto import SecretDecryptError
from privacyfence_cockpit.token_check import TokenState
from privacyfence_cockpit.tokens import (
    TokenNameError,
    TokenValueError,
    check_token_name,
    clean_token_value,
    valid_token_names,
)
from privacyfence_cockpit.web.auth import require_csrf
from privacyfence_cockpit.web.render import redirect, render

UNDECRYPTABLE = "A stored token cannot be decrypted with this master key. Delete it and set it again."

# A token state has no chip of its own; it borrows the colour of the outcome that reads the same.
STATE_CHIPS = {
    TokenState.OK: "ok",
    TokenState.EXPIRING: "not_signed_in",
    TokenState.EXPIRED: "failed",
    TokenState.REFUSED: "failed",
    TokenState.NOT_SCOPED: "failed",
    TokenState.UNREACHABLE: "timed_out",
    TokenState.NO_SERVER: "stopped",
}
STATE_LABELS = {
    TokenState.OK: "ok",
    TokenState.EXPIRING: "expiring",
    TokenState.EXPIRED: "expired",
    TokenState.REFUSED: "refused",
    TokenState.NOT_SCOPED: "not scoped",
    TokenState.UNREACHABLE: "unreachable",
    TokenState.NO_SERVER: "no server",
}


@dataclass(frozen=True)
class TokenRow:
    name: str
    state: str | None
    chip: str | None
    label: str | None
    expires_at: str | None
    days_left: int | None
    checked_at: str | None
    error: str | None


def settings_link(server_url: str | None) -> str | None:
    if not server_url:
        return None
    parts = urlsplit(server_url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}/settings"


def _page(request: Request, *, error: str | None = None, name: str = "", status_code: int = 200) -> Response:
    cockpit = request.app.state.cockpit
    stored = cockpit.store.names("token")
    status = {row["name"]: row for row in cockpit.db.query("SELECT * FROM token_status")}
    rows: list[TokenRow] = []
    for token_name in stored:
        row = status.get(token_name)
        state = TokenState(row["state"]) if row is not None else None
        rows.append(
            TokenRow(
                token_name,
                STATE_LABELS[state] if state else None,
                STATE_CHIPS[state] if state else None,
                row["label"] if row else None,
                row["expires_at"] if row else None,
                row["days_left"] if row else None,
                row["checked_at"] if row else None,
                row["error"] if row else None,
            )
        )
    valid = valid_token_names(cockpit.recipes())
    effective = cockpit.effective()
    context: dict[str, Any] = {
        "rows": rows,
        "missing": [n for n in valid if n not in stored],
        "valid_names": valid,
        "settings_link": settings_link(effective.server_url),
        "tz": effective.timezone,
        "error": error,
        "name": name,
        "env_token": cockpit.env.env_token is not None,
    }
    return render(request, "tokens.html", context, status_code)


async def tokens_page(request: Request) -> Response:
    return _page(request)


async def set_token(request: Request) -> Response:
    form = await require_csrf(request)
    cockpit = request.app.state.cockpit
    name = str(form.get("name", "")).strip()
    try:
        check_token_name(name, cockpit.recipes())
        value = clean_token_value(str(form.get("value", "")))
    except (TokenNameError, TokenValueError) as error:
        return _page(request, error=str(error), name=name, status_code=400)
    cockpit.store.set("token", name, value)
    cockpit.token_checker.forget(name)
    try:
        result = await cockpit.token_checker.check_one(name)
    except SecretDecryptError:
        return redirect("/tokens", UNDECRYPTABLE)
    return redirect("/tokens", f"Saved the token for {name}: {STATE_LABELS[result.state]}.")


async def delete_token(request: Request) -> Response:
    form = await require_csrf(request)
    cockpit = request.app.state.cockpit
    name = str(form.get("name", ""))
    cockpit.store.delete("token", name)
    cockpit.token_checker.forget(name)
    return redirect("/tokens", f"Deleted the token for {name}.")


async def check_tokens(request: Request) -> Response:
    await require_csrf(request)
    cockpit = request.app.state.cockpit
    try:
        results = await cockpit.token_checker.check_all()
    except SecretDecryptError:
        return redirect("/tokens", UNDECRYPTABLE)
    return redirect("/tokens", f"Checked {len(results)} token(s).")


ROUTES: list[BaseRoute] = [
    Route("/tokens", tokens_page),
    Route("/tokens", set_token, methods=["POST"]),
    Route("/tokens/delete", delete_token, methods=["POST"]),
    Route("/tokens/check", check_tokens, methods=["POST"]),
]
