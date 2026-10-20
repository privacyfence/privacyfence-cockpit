"""The owner's password, the setup code, sessions, CSRF, the login throttle and the two middlewares."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import math
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from starlette.datastructures import FormData, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection, Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.models import iso, parse_iso
from privacyfence_cockpit.services import Cockpit

OWNER_HASH_SETTING = "auth.password_hash"
SETUP_HASH_SETTING = "auth.setup_code_hash"

MIN_PASSWORD_LENGTH = 12
TOO_SHORT = "Use at least 12 characters."
NO_MATCH = "The passwords do not match."
WRONG_LOGIN = "Wrong password."
WRONG_SETUP_CODE = "That setup code is not valid."
CSRF_FAILED = "Invalid or missing CSRF token. Reload the page and try again."
NOT_LOGGED_IN = "Log in first."
THROTTLED = "Too many failed attempts. Try again in {n} seconds."

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
SCRYPT_DKLEN = 32

SETUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SETUP_CODE_LENGTH = 12

SESSION_COOKIE = "cockpit_session"
CSRF_COOKIE = "cockpit_csrf"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SESSION_LIFETIME = timedelta(days=7)

THROTTLE_FREE_FAILURES = 5
THROTTLE_BASE_SECONDS = 30
THROTTLE_MAX_SECONDS = 900

PUBLIC_PATHS = frozenset({"/healthz", "/login", "/setup"})

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=2**15, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=SCRYPT_DKLEN
    )
    return "$".join(["scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P), _b64(salt), _b64(digest)])


def verify_password(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
    except (ValueError, binascii.Error):
        return False
    # Only the parameters this module writes are accepted, so a tampered row cannot make a login
    # burn unbounded memory or CPU.
    if (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P) or len(expected) != SCRYPT_DKLEN:
        return False
    actual = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=SCRYPT_DKLEN)
    return hmac.compare_digest(actual, expected)


def password_problem(password: str, confirm: str) -> str | None:
    """The message for a new password that is not acceptable, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return TOO_SHORT
    if not hmac.compare_digest(password.encode(), confirm.encode()):
        return NO_MATCH
    return None


def password_is_set(db: Database) -> bool:
    return db.get_setting(OWNER_HASH_SETTING) is not None


def set_password(db: Database, password: str) -> None:
    db.set_setting(OWNER_HASH_SETTING, hash_password(password))


def check_password(db: Database, password: str) -> bool:
    stored = db.get_setting(OWNER_HASH_SETTING)
    return stored is not None and verify_password(password, stored)


def new_setup_code(db: Database) -> str:
    raw = "".join(secrets.choice(SETUP_CODE_ALPHABET) for _ in range(SETUP_CODE_LENGTH))
    db.set_setting(SETUP_HASH_SETTING, _sha256(raw))
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


def check_setup_code(db: Database, typed: str) -> bool:
    stored = db.get_setting(SETUP_HASH_SETTING)
    if stored is None:
        return False
    raw = "".join(typed.split()).replace("-", "").upper()
    return hmac.compare_digest(_sha256(raw), stored)


def consume_setup_code(db: Database) -> None:
    db.delete_setting(SETUP_HASH_SETTING)


@dataclass(frozen=True)
class Session:
    session_id: str
    csrf_token: str
    created_at: datetime
    expires_at: datetime


def create_session(db: Database, now: datetime) -> Session:
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso(now),))
    session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32), now, now + SESSION_LIFETIME)
    db.execute(
        "INSERT INTO sessions (id_hash, csrf_token, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_sha256(session.session_id), session.csrf_token, iso(session.created_at), iso(session.expires_at)),
    )
    return session


def lookup_session(db: Database, session_id: str, now: datetime) -> Session | None:
    id_hash = _sha256(session_id)
    row = db.query_one("SELECT csrf_token, created_at, expires_at FROM sessions WHERE id_hash = ?", (id_hash,))
    if row is None:
        return None
    expires_at = parse_iso(row["expires_at"])
    if expires_at <= now:
        db.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
        return None
    return Session(session_id, row["csrf_token"], parse_iso(row["created_at"]), expires_at)


def delete_session(db: Database, session_id: str) -> None:
    db.execute("DELETE FROM sessions WHERE id_hash = ?", (_sha256(session_id),))


def delete_all_except(db: Database, session_id: str | None) -> None:
    """Deletes every session but `session_id`'s; None deletes them all."""
    if session_id is None:
        db.execute("DELETE FROM sessions")
    else:
        db.execute("DELETE FROM sessions WHERE id_hash != ?", (_sha256(session_id),))


def set_session_cookie(response: Response, session: Session, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session.session_id,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path="/",
        secure=secure,
        httponly=True,
        samesite="strict",
    )


def set_csrf_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(CSRF_COOKIE, token, path="/", secure=secure, httponly=True, samesite="strict")


class LoginThrottle:
    """Counts consecutive failed logins (and setup attempts) and refuses attempts while locked out."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._failures = 0
        self._last_failure: datetime | None = None

    def wait_seconds(self) -> int:
        """Seconds until the next attempt is allowed; 0 when it is allowed now."""
        if self._failures < THROTTLE_FREE_FAILURES or self._last_failure is None:
            return 0
        lockout = min(THROTTLE_BASE_SECONDS * 2 ** (self._failures - THROTTLE_FREE_FAILURES), THROTTLE_MAX_SECONDS)
        elapsed = (self._clock() - self._last_failure).total_seconds()
        return max(0, int(math.ceil(lockout - elapsed)))

    def refused_message(self) -> str | None:
        wait = self.wait_seconds()
        return THROTTLED.format(n=wait) if wait > 0 else None

    def failure(self) -> None:
        self._failures += 1
        self._last_failure = self._clock()

    def reset(self) -> None:
        self._failures = 0
        self._last_failure = None


async def require_csrf(request: Request) -> FormData:
    """Reads the form and checks its CSRF token; every POST handler calls this first.

    With a session the token is the session's; without one (only /login and /setup get that far) it
    is the double-submit cookie set when the form was shown.
    """
    form = await request.form()
    if getattr(request.state, "session_id", None):
        expected = getattr(request.state, "csrf_token", "")
    else:
        expected = request.cookies.get(CSRF_COOKIE, "")
    sent = request.headers.get(CSRF_HEADER)
    if sent is None:
        field = form.get(CSRF_FIELD)
        sent = field if isinstance(field, str) else ""
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        raise HTTPException(403, CSRF_FAILED)
    return form


class AuthMiddleware:
    """Finds the session for each request and keeps everything but the public paths behind it."""

    def __init__(self, app: ASGIApp, cockpit: Cockpit) -> None:
        self.app = app
        self.cockpit = cockpit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        session = None
        cookie = connection.cookies.get(SESSION_COOKIE)
        if cookie:
            session = lookup_session(self.cockpit.db, cookie, self.cockpit.clock())
        state = scope.setdefault("state", {})
        state["authenticated"] = session is not None
        state["session_id"] = session.session_id if session else None
        state["csrf_token"] = session.csrf_token if session else ""
        path: str = scope["path"]
        if session is not None or path in PUBLIC_PATHS or path == "/static" or path.startswith("/static/"):
            await self.app(scope, receive, send)
            return
        response: Response
        if scope["method"] in ("GET", "HEAD"):
            target = "/login" if password_is_set(self.cockpit.db) else "/setup"
            response = RedirectResponse(target, status_code=303)
        else:
            response = PlainTextResponse(NOT_LOGGED_IN, status_code=403)
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """Adds the security headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
