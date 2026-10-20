import re
from pathlib import Path

from cockpit_factory import make_cockpit
from starlette.applications import Starlette
from starlette.routing import Route
from webclient import logged_in

from privacyfence_cockpit.web.auth import CSRF_FAILED


def post_paths(app: Starlette) -> list[str]:
    return [
        re.sub(r"\{[^}]+\}", "x", route.path)
        for route in app.routes
        if isinstance(route, Route) and route.methods is not None and "POST" in route.methods
    ]


def test_every_post_route_needs_a_csrf_token(tmp_path: Path) -> None:
    c, token = logged_in(make_cockpit(tmp_path))
    assert isinstance(c.app, Starlette)
    paths = post_paths(c.app)
    assert {"/setup", "/login", "/logout"} <= set(paths)
    for path in paths:
        response = c.post(path, data={"anything": "1"}, follow_redirects=False)
        assert response.status_code == 403, path
        assert response.text == CSRF_FAILED, path
        response = c.post(path, data={"csrf_token": token + "x"}, follow_redirects=False)
        assert response.status_code == 403, path
    # The session survived every refused request.
    assert c.get("/", follow_redirects=False).status_code == 200
