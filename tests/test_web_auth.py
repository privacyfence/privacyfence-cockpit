import base64
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from cockpit_factory import make_cockpit
from httpx import Response
from starlette.testclient import TestClient
from webclient import PASSWORD, client, form_token, logged_in

from privacyfence_cockpit.cli import main
from privacyfence_cockpit.config import EnvConfig
from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web.auth import (
    CSRF_FAILED,
    SECURITY_HEADERS,
    LoginThrottle,
    check_setup_code,
    create_session,
    delete_all_except,
    hash_password,
    lookup_session,
    new_setup_code,
    password_is_set,
    password_problem,
    set_password,
    verify_password,
)

NEW_PASSWORD = "a long enough password"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def cockpit(tmp_path: Path, clock: Clock) -> Cockpit:
    return make_cockpit(tmp_path, clock=clock)


def cookies(response: Response) -> dict[str, str]:
    """Set-Cookie headers by cookie name."""
    return {header.split("=", 1)[0]: header for header in response.headers.get_list("set-cookie")}


def submit_setup(c: TestClient, code: str, password: str = NEW_PASSWORD, confirm: str | None = None) -> Response:
    page = c.get("/setup")
    data = {"code": code, "password": password, "confirm": password if confirm is None else confirm}
    return c.post("/setup", data={**data, "csrf_token": form_token(page.text)}, follow_redirects=False)


def submit_login(c: TestClient, password: str) -> Response:
    page = c.get("/login")
    return c.post("/login", data={"password": password, "csrf_token": form_token(page.text)}, follow_redirects=False)


# Passwords


def test_hash_format_and_verify() -> None:
    stored = hash_password(PASSWORD)
    scheme, n, r, p, salt, digest = stored.split("$")
    assert (scheme, n, r, p) == ("scrypt", "32768", "8", "1")
    assert len(base64.b64decode(salt)) == 16
    assert len(base64.b64decode(digest)) == 32
    assert verify_password(PASSWORD, stored)
    assert not verify_password(PASSWORD + "!", stored)
    assert hash_password(PASSWORD) != stored  # a fresh salt each time


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "bcrypt$1$2$3$4$5",
        "scrypt$32768$8$1$c2FsdA==",
        "scrypt$nope$8$1$c2FsdA==$c2FsdA==",
        "scrypt$32768$8$1$%%%$c2FsdA==",
        "scrypt$1048576$8$1$c2FsdA==$" + base64.b64encode(b"x" * 32).decode(),
        "scrypt$32768$8$1$c2FsdA==$c2FsdA==",
    ],
)
def test_verify_rejects_malformed_or_foreign_hashes(stored: str) -> None:
    assert not verify_password(PASSWORD, stored)


def test_password_problem() -> None:
    assert password_problem("short", "short") == "Use at least 12 characters."
    assert password_problem(NEW_PASSWORD, NEW_PASSWORD + "x") == "The passwords do not match."
    assert password_problem(NEW_PASSWORD, NEW_PASSWORD) is None


# Setup code


def test_setup_code_format_and_check(cockpit: Cockpit) -> None:
    code = new_setup_code(cockpit.db)
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}", code)
    raw = code.replace("-", "")
    import hashlib  # noqa: PLC0415

    assert cockpit.db.get_setting("auth.setup_code_hash") == hashlib.sha256(raw.encode()).hexdigest()
    assert check_setup_code(cockpit.db, code)
    assert check_setup_code(cockpit.db, f" {raw.lower()} ")
    assert not check_setup_code(cockpit.db, "AAAA-AAAA-AAAA")
    cockpit.db.delete_setting("auth.setup_code_hash")
    assert not check_setup_code(cockpit.db, code)


# Redirects and public paths


def test_get_redirects_to_setup_then_login(cockpit: Cockpit) -> None:
    c = client(cockpit)
    response = c.get("/", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/setup")
    assert c.head("/history", follow_redirects=False).headers["location"] == "/setup"
    set_password(cockpit.db, PASSWORD)
    response = c.get("/", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/login")


def test_other_methods_without_a_session_get_403(cockpit: Cockpit) -> None:
    response = client(cockpit).post("/logout", follow_redirects=False)
    assert response.status_code == 403
    assert response.text == "Log in first."


def test_healthz_and_static_need_no_login(cockpit: Cockpit) -> None:
    c = client(cockpit)
    assert c.get("/healthz").status_code == 200
    assert c.get("/static/cockpit.css").status_code == 200


def test_login_and_setup_pages_show_no_nav(cockpit: Cockpit) -> None:
    c = client(cockpit)
    page = c.get("/setup")
    assert page.status_code == 200
    assert 'href="/history"' not in page.text
    assert "No recipe repository yet." not in page.text
    set_password(cockpit.db, PASSWORD)
    page = c.get("/login")
    assert page.status_code == 200
    assert 'href="/history"' not in page.text


# Setup


def test_setup_wrong_code(cockpit: Cockpit) -> None:
    new_setup_code(cockpit.db)
    response = submit_setup(client(cockpit), "AAAA-AAAA-AAAA")
    assert response.status_code == 400
    assert "That setup code is not valid." in response.text
    assert not password_is_set(cockpit.db)


def test_setup_mismatch_and_short_password(cockpit: Cockpit) -> None:
    code = new_setup_code(cockpit.db)
    c = client(cockpit)
    response = submit_setup(c, code, confirm=NEW_PASSWORD + "x")
    assert response.status_code == 400
    assert "The passwords do not match." in response.text
    response = submit_setup(c, code, password="too short")
    assert response.status_code == 400
    assert "Use at least 12 characters." in response.text
    assert not password_is_set(cockpit.db)
    assert cockpit.db.get_setting("auth.setup_code_hash") is not None


def test_setup_success(cockpit: Cockpit) -> None:
    code = new_setup_code(cockpit.db)
    c = client(cockpit)
    response = submit_setup(c, code)
    assert (response.status_code, response.headers["location"]) == (303, "/")
    assert verify_password(NEW_PASSWORD, cockpit.db.get_setting("auth.password_hash") or "")
    assert cockpit.db.get_setting("auth.setup_code_hash") is None
    session = cookies(response)["cockpit_session"]
    assert "HttpOnly" in session and "SameSite=strict" in session and "Path=/" in session
    assert "Max-Age=604800" in session
    assert "Secure" not in session
    assert c.get("/", follow_redirects=False).status_code == 200
    # The code is consumed: a second setup is not possible, with it or at all.
    assert c.get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_setup_success_with_https_public_url_sets_secure_cookies(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path, environ={"COCKPIT_PUBLIC_URL": "https://cockpit.example.invalid"})
    code = new_setup_code(cockpit.db)
    c = TestClient(client(cockpit).app, base_url="https://testserver")
    page = c.get("/setup")
    assert "Secure" in cookies(page)["cockpit_csrf"]
    response = submit_setup(c, code)
    assert response.status_code == 303
    assert "Secure" in cookies(response)["cockpit_session"]


def test_setup_after_a_password_is_set_redirects_to_login(cockpit: Cockpit) -> None:
    c = client(cockpit)
    page = c.get("/setup")
    set_password(cockpit.db, PASSWORD)
    assert c.get("/setup", follow_redirects=False).headers["location"] == "/login"
    response = c.post(
        "/setup",
        data={"code": "x", "password": NEW_PASSWORD, "confirm": NEW_PASSWORD, "csrf_token": form_token(page.text)},
        follow_redirects=False,
    )
    assert (response.status_code, response.headers["location"]) == (303, "/login")
    assert verify_password(PASSWORD, cockpit.db.get_setting("auth.password_hash") or "")


# Login and logout


def test_login_wrong_password_then_right(cockpit: Cockpit) -> None:
    set_password(cockpit.db, PASSWORD)
    c = client(cockpit)
    response = submit_login(c, "not the password")
    assert response.status_code == 400
    assert "Wrong password." in response.text
    assert "cockpit_session" not in cookies(response)
    response = submit_login(c, PASSWORD)
    assert (response.status_code, response.headers["location"]) == (303, "/")
    assert "cockpit_session" in cookies(response)


def test_login_page_redirects(cockpit: Cockpit) -> None:
    c = client(cockpit)
    page = c.get("/setup")
    assert c.get("/login", follow_redirects=False).headers["location"] == "/setup"
    response = c.post(
        "/login", data={"password": PASSWORD, "csrf_token": form_token(page.text)}, follow_redirects=False
    )
    assert response.headers["location"] == "/setup"
    c, _ = logged_in(cockpit)
    assert c.get("/login", follow_redirects=False).headers["location"] == "/"


def test_logout_deletes_the_session(cockpit: Cockpit) -> None:
    c, token = logged_in(cockpit)
    response = c.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/login")
    assert cockpit.db.query("SELECT * FROM sessions") == []
    assert 'cockpit_session=""' in cookies(response)["cockpit_session"]
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"


def test_csrf_header_is_accepted(cockpit: Cockpit) -> None:
    c, token = logged_in(cockpit)
    response = c.post("/logout", headers={"X-CSRF-Token": token}, follow_redirects=False)
    assert response.status_code == 303


def test_post_without_or_with_a_wrong_csrf_token(cockpit: Cockpit) -> None:
    set_password(cockpit.db, PASSWORD)
    c = client(cockpit)
    response = c.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert (response.status_code, response.text) == (403, CSRF_FAILED)
    token = form_token(c.get("/login").text)
    response = c.post("/login", data={"password": PASSWORD, "csrf_token": token[:-1]}, follow_redirects=False)
    assert (response.status_code, response.text) == (403, CSRF_FAILED)
    # A token from the form without the matching cookie does not work either.
    c.cookies.clear()
    response = c.post("/login", data={"password": PASSWORD, "csrf_token": token}, follow_redirects=False)
    assert (response.status_code, response.text) == (403, CSRF_FAILED)
    c, session_token = logged_in(cockpit)
    response = c.post("/logout", headers={"X-CSRF-Token": session_token + "x"}, follow_redirects=False)
    assert (response.status_code, response.text) == (403, CSRF_FAILED)


# Throttle


def test_throttle_lockouts_double_up_to_the_cap(clock: Clock) -> None:
    throttle = LoginThrottle(clock)
    for _ in range(4):
        throttle.failure()
        assert throttle.wait_seconds() == 0
    waits = []
    for _ in range(7):
        throttle.failure()
        waits.append(throttle.wait_seconds())
        clock.advance(waits[-1])
        assert throttle.wait_seconds() == 0
    assert waits == [30, 60, 120, 240, 480, 900, 900]
    throttle.failure()
    clock.advance(0.5)
    assert throttle.refused_message() == "Too many failed attempts. Try again in 900 seconds."
    throttle.reset()
    assert throttle.refused_message() is None


def test_login_throttle_through_the_routes(cockpit: Cockpit, clock: Clock) -> None:
    set_password(cockpit.db, PASSWORD)
    c = client(cockpit)
    for _ in range(5):
        assert submit_login(c, "wrong").status_code == 400
    response = submit_login(c, PASSWORD)
    assert response.status_code == 429
    assert "Too many failed attempts. Try again in 30 seconds." in response.text
    clock.advance(10)
    assert "Try again in 20 seconds." in submit_login(c, PASSWORD).text
    clock.advance(20)
    assert submit_login(c, "wrong").status_code == 400
    assert "Try again in 60 seconds." in submit_login(c, PASSWORD).text
    clock.advance(60)
    assert submit_login(c, PASSWORD).status_code == 303
    # Success reset the count: five more failures are allowed before the next lockout.
    for _ in range(5):
        assert submit_login(c, "wrong").status_code == 400
    assert submit_login(c, PASSWORD).status_code == 429


def test_setup_shares_the_throttle(cockpit: Cockpit) -> None:
    code = new_setup_code(cockpit.db)
    c = client(cockpit)
    for _ in range(5):
        assert submit_setup(c, "AAAA-AAAA-AAAA").status_code == 400
    response = submit_setup(c, code)
    assert response.status_code == 429
    assert "Too many failed attempts. Try again in 30 seconds." in response.text
    assert not password_is_set(cockpit.db)


# Sessions


def test_session_expires_after_seven_days(cockpit: Cockpit, clock: Clock) -> None:
    c, _ = logged_in(cockpit)
    clock.advance(timedelta(days=7).total_seconds() - 1)
    assert c.get("/", follow_redirects=False).status_code == 200
    clock.advance(1)
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    assert cockpit.db.query("SELECT * FROM sessions") == []


def test_unknown_session_cookie_is_ignored(cockpit: Cockpit) -> None:
    set_password(cockpit.db, PASSWORD)
    c = client(cockpit)
    c.cookies.set("cockpit_session", "made-up")
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"


def test_sessions_are_stored_hashed_and_old_ones_pruned(cockpit: Cockpit, clock: Clock) -> None:
    first = create_session(cockpit.db, clock())
    rows = cockpit.db.query("SELECT id_hash FROM sessions")
    assert [r["id_hash"] for r in rows] != [first.session_id]
    assert lookup_session(cockpit.db, first.session_id, clock()) == first
    clock.advance(timedelta(days=8).total_seconds())
    second = create_session(cockpit.db, clock())
    third = create_session(cockpit.db, clock())
    assert len(cockpit.db.query("SELECT * FROM sessions")) == 2
    delete_all_except(cockpit.db, second.session_id)
    assert lookup_session(cockpit.db, second.session_id, clock()) == second
    assert lookup_session(cockpit.db, third.session_id, clock()) is None


# Security headers


def test_security_headers_on_pages_redirects_and_healthz(cockpit: Cockpit) -> None:
    c = client(cockpit)
    for response in (c.get("/setup"), c.get("/", follow_redirects=False), c.get("/healthz")):
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value
    assert SECURITY_HEADERS["Content-Security-Policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )


# The setup code in the log


@pytest.fixture
def served_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    key = tmp_path / "key"
    key.write_text(base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COCKPIT_MASTER_KEY_FILE", str(key))
    monkeypatch.delenv("PRIVACYFENCE_TOKEN", raising=False)
    monkeypatch.delenv("COCKPIT_PUBLIC_URL", raising=False)
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: None)
    # serve() reconfigures the root logger; keep pytest's capture handler in place.
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: None)
    return tmp_path


def test_setup_code_is_logged_once_and_never_echoed(served_env: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    assert main(["serve"]) == 0
    warnings = [r for r in caplog.records if "Setup code" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    found = re.fullmatch(
        r"No password is set\. Setup code: (\S+)\. Open the cockpit/setup to set one\.", warnings[0].getMessage()
    )
    assert found is not None
    code = found.group(1)

    cockpit = Cockpit.open(EnvConfig.from_environ(dict(os.environ)))
    c = client(cockpit)
    texts = [c.get("/setup").text]
    texts.append(submit_setup(c, "AAAA-AAAA-AAAA").text)
    texts.append(submit_setup(c, code, confirm="something else entirely").text)
    texts.append(submit_setup(c, code, password="short").text)
    response = submit_setup(c, code)
    assert response.status_code == 303
    texts.append(response.text)
    texts.append(c.get("/").text)
    for text in texts:
        assert code not in text
        assert code.replace("-", "") not in text
    assert caplog.text.count(code) == 1

    # With a password set, a restart issues no code.
    caplog.clear()
    assert main(["serve"]) == 0
    assert "Setup code" not in caplog.text


def test_announce_names_the_public_url(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from privacyfence_cockpit.web.server import announce_setup_code  # noqa: PLC0415

    cockpit = make_cockpit(tmp_path, environ={"COCKPIT_PUBLIC_URL": "https://cockpit.example.invalid/"})
    with caplog.at_level(logging.WARNING):
        announce_setup_code(cockpit)
    assert "Open https://cockpit.example.invalid/setup to set one." in caplog.text
