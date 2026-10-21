"""A Starlette test client over the app, without the background loops."""

from __future__ import annotations

import re

from starlette.testclient import TestClient

from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web.app import create_app
from privacyfence_cockpit.web.auth import set_password

PASSWORD = "correct horse battery staple"


def client(cockpit: Cockpit) -> TestClient:
    return TestClient(create_app(cockpit, start_background=False))


def form_token(html: str) -> str:
    """The value of the first hidden csrf_token field on a page."""
    found = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert found is not None, "no csrf_token field on the page"
    return found.group(1)


def logged_in(cockpit: Cockpit) -> tuple[TestClient, str]:
    """Sets a password, logs in, and returns the client and the session's CSRF token."""
    c = client(cockpit)
    set_password(cockpit.db, PASSWORD)
    page = c.get("/login")
    response = c.post(
        "/login", data={"password": PASSWORD, "csrf_token": form_token(page.text)}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return c, form_token(c.get("/").text)
