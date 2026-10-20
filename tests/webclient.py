"""A Starlette test client over the app, without the background loops."""

from __future__ import annotations

from starlette.testclient import TestClient

from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web.app import create_app


def client(cockpit: Cockpit) -> TestClient:
    return TestClient(create_app(cockpit, start_background=False))
