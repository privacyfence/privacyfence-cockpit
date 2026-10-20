"""First-run password setup, login and logout."""

from __future__ import annotations

import secrets

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    WRONG_LOGIN,
    WRONG_SETUP_CODE,
    LoginThrottle,
    check_password,
    check_setup_code,
    consume_setup_code,
    create_session,
    delete_all_except,
    delete_session,
    password_is_set,
    password_problem,
    require_csrf,
    set_csrf_cookie,
    set_password,
    set_session_cookie,
)
from privacyfence_cockpit.web.render import redirect, render


def _cockpit(request: Request) -> Cockpit:
    cockpit: Cockpit = request.app.state.cockpit
    return cockpit


def _throttle(request: Request) -> LoginThrottle:
    throttle: LoginThrottle = request.app.state.login_throttle
    return throttle


def _form_page(request: Request, template: str, error: str | None = None, status_code: int = 200) -> Response:
    """Renders /login or /setup with the double-submit CSRF token, reusing the cookie on a re-render."""
    token = request.cookies.get(CSRF_COOKIE) if error is not None else None
    token = token or secrets.token_urlsafe(32)
    response = render(request, template, {"csrf_token": token, "error": error}, status_code)
    set_csrf_cookie(response, token, secure=_cockpit(request).env.secure_cookies)
    return response


def _logged_in(request: Request) -> Response:
    """Starts a new session after a successful login or setup and sends the owner to the overview."""
    cockpit = _cockpit(request)
    _throttle(request).reset()
    session = create_session(cockpit.db, cockpit.clock())
    response = redirect("/")
    set_session_cookie(response, session, secure=cockpit.env.secure_cookies)
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


async def setup_page(request: Request) -> Response:
    if password_is_set(_cockpit(request).db):
        return redirect("/login")
    return _form_page(request, "setup.html")


async def setup_submit(request: Request) -> Response:
    form = await require_csrf(request)
    cockpit = _cockpit(request)
    if password_is_set(cockpit.db):
        return redirect("/login")
    throttle = _throttle(request)
    refused = throttle.refused_message()
    if refused is not None:
        return _form_page(request, "setup.html", refused, 429)
    if not check_setup_code(cockpit.db, _text(form.get("code"))):
        throttle.failure()
        return _form_page(request, "setup.html", WRONG_SETUP_CODE, 400)
    password = _text(form.get("password"))
    problem = password_problem(password, _text(form.get("confirm")))
    if problem is not None:
        return _form_page(request, "setup.html", problem, 400)
    # Hashing runs on the event loop on purpose: no await between the throttle check and recording the
    # outcome, so concurrent requests cannot slip past the throttle.
    set_password(cockpit.db, password)
    consume_setup_code(cockpit.db)
    delete_all_except(cockpit.db, None)
    return _logged_in(request)


async def login_page(request: Request) -> Response:
    if not password_is_set(_cockpit(request).db):
        return redirect("/setup")
    if request.state.authenticated:
        return redirect("/")
    return _form_page(request, "login.html")


async def login_submit(request: Request) -> Response:
    form = await require_csrf(request)
    cockpit = _cockpit(request)
    if not password_is_set(cockpit.db):
        return redirect("/setup")
    throttle = _throttle(request)
    refused = throttle.refused_message()
    if refused is not None:
        return _form_page(request, "login.html", refused, 429)
    # Verified on the event loop on purpose, as in setup_submit.
    if not check_password(cockpit.db, _text(form.get("password"))):
        throttle.failure()
        return _form_page(request, "login.html", WRONG_LOGIN, 400)
    return _logged_in(request)


async def logout(request: Request) -> Response:
    await require_csrf(request)
    delete_session(_cockpit(request).db, request.state.session_id)
    response = redirect("/login")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


ROUTES: list[BaseRoute] = [
    Route("/setup", setup_page, methods=["GET"]),
    Route("/setup", setup_submit, methods=["POST"]),
    Route("/login", login_page, methods=["GET"]),
    Route("/login", login_submit, methods=["POST"]),
    Route("/logout", logout, methods=["POST"]),
]
