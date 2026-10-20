import base64
import hashlib
from pathlib import Path

import pytest
from cockpit_factory import make_cockpit
from gitutil import make_remote, push_commit
from starlette.testclient import TestClient
from webclient import client

from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web.app import create_app, localtime, outcome_label, sha7
from privacyfence_cockpit.web.context import banners

HTMX_SHA256 = "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447"


@pytest.fixture
def cockpit(tmp_path: Path) -> Cockpit:
    return make_cockpit(tmp_path)


def texts(cockpit: Cockpit) -> list[str]:
    return [b.text for b in banners(cockpit)]


def test_healthz_ok(cockpit: Cockpit) -> None:
    response = client(cockpit).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_error_when_database_is_closed(cockpit: Cockpit) -> None:
    c = client(cockpit)
    cockpit.db.close()
    response = c.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "error"}


def test_static_htmx_is_served(cockpit: Cockpit) -> None:
    response = client(cockpit).get("/static/htmx.min.js")
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == HTMX_SHA256


def test_home_placeholder_renders_nav(cockpit: Cockpit) -> None:
    response = client(cockpit).get("/")
    assert response.status_code == 200
    assert 'href="/history"' in response.text
    assert "No recipe repository yet." in response.text


def test_lifespan_starts_and_stops_background_tasks(cockpit: Cockpit) -> None:
    with TestClient(create_app(cockpit)) as c:
        assert c.get("/healthz").status_code == 200
        assert cockpit._tasks
    assert not cockpit._tasks


def test_banner_env_token(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path, environ={"PRIVACYFENCE_TOKEN": "pfst_" + "x" * 30})
    first = banners(cockpit)[0]
    assert first.level == "warning"
    assert first.text == "PRIVACYFENCE_TOKEN is set in the container environment: it overrides every stored token."


def test_banner_no_repository(cockpit: Cockpit) -> None:
    banner = banners(cockpit)[0]
    assert (banner.level, banner.text, banner.link) == ("info", "No recipe repository yet.", "/code")


def test_banner_cockpit_yaml_error(cockpit: Cockpit) -> None:
    cockpit.paths.repo.mkdir()
    (cockpit.paths.repo / "cockpit.yaml").write_text("version: 2\n")
    found = [b for b in banners(cockpit) if b.link == "/schedules"]
    assert len(found) == 1
    assert found[0].level == "error"
    assert found[0].text.startswith("cockpit.yaml: ")


def test_banner_discovery_error(cockpit: Cockpit) -> None:
    for folder in ("10-imports", "40-imports"):
        (cockpit.paths.repo / "workflows" / folder / "r").mkdir(parents=True)
        (cockpit.paths.repo / "workflows" / folder / "r" / "recipe.yaml").write_text("steps: []\n")
    found = [b for b in banners(cockpit) if "both have the group name" in b.text]
    assert len(found) == 1
    assert found[0].level == "error"
    assert found[0].link is None


def test_banner_pinned(cockpit: Cockpit) -> None:
    cockpit.db.set_setting("git.repo_url", "https://example.com/r.git")
    cockpit.db.set_setting("git.branch", "main")
    cockpit.db.set_setting("git.pinned_sha", "abcdef0123456789")
    assert "Schedules run the pinned commit abcdef0, not the tip of main." in texts(cockpit)


def test_banner_venv_error(cockpit: Cockpit) -> None:
    cockpit.db.set_setting("venv.last_error", "pip install failed with exit code 1; see logs/venv/x.log")
    assert (
        "The recipe environment could not be built: pip install failed with exit code 1; see logs/venv/x.log"
        in texts(cockpit)
    )


def test_banner_fetch_failing(cockpit: Cockpit) -> None:
    cockpit.db.set_setting("git.failing_since", "2026-10-19T00:00:00Z")
    cockpit.db.set_setting("git.last_fetch_error", "git fetch failed: no route")
    assert "Fetching the recipe repository fails: git fetch failed: no route" in texts(cockpit)


def test_banner_fetch_failing_without_error_text(cockpit: Cockpit) -> None:
    cockpit.db.set_setting("git.failing_since", "2026-10-19T00:00:00Z")
    assert "Fetching the recipe repository fails: unknown error" in texts(cockpit)


def test_banner_new_commits(tmp_path: Path, cockpit: Cockpit) -> None:
    remote = make_remote(tmp_path, {"a.txt": "a"})
    cockpit.git.configure(str(remote), "main", "none")
    cockpit.git.clone()
    assert not [b for b in banners(cockpit) if "new commit" in b.text]
    push_commit(remote, {"b.txt": "b"}, "second")
    cockpit.git.fetch()
    banner = banners(cockpit)[-1]
    assert (banner.level, banner.text, banner.link) == ("info", "1 new commit(s) on main.", "/code")


def test_flash_cookie_round_trip(cockpit: Cockpit) -> None:
    from privacyfence_cockpit.web.render import redirect  # noqa: PLC0415

    response = redirect("/code", flash="Saved.")
    assert response.status_code == 303
    assert response.headers["location"] == "/code"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=60" in cookie

    c = client(cockpit)
    value = base64.urlsafe_b64encode(b"Saved.").decode()
    c.cookies.set("cockpit_flash", value)
    first = c.get("/")
    assert "Saved." in first.text
    assert "cockpit_flash=" in first.headers["set-cookie"]
    c.cookies.clear()
    assert "Saved." not in c.get("/").text


def test_flash_is_cut_and_bad_cookie_ignored(cockpit: Cockpit) -> None:
    from privacyfence_cockpit.web.render import redirect  # noqa: PLC0415

    cookie = redirect("/", flash="a" * 500).headers["set-cookie"]
    encoded = cookie.split("cockpit_flash=")[1].split(";")[0]
    assert base64.urlsafe_b64decode(encoded).decode() == "a" * 300

    c = client(cockpit)
    c.cookies.set("cockpit_flash", "%%%not-base64")
    assert c.get("/").status_code == 200
    c.cookies.set("cockpit_flash", base64.urlsafe_b64encode(b"\xff\xfe").decode())
    assert c.get("/").status_code == 200


def test_filters() -> None:
    assert localtime("2026-10-20T06:15:00Z", "Europe/Budapest") == "2026-10-20 08:15 Europe/Budapest"
    assert localtime(None, "UTC") == ""
    assert localtime("not a date", "UTC") == "not a date"
    assert localtime("2026-10-20T06:15:00Z", "No/Such_Zone") == "2026-10-20T06:15:00Z"
    assert outcome_label("no_change") == "no change"
    assert outcome_label(None) == ""
    assert outcome_label("weird") == "weird"
    assert sha7("0123456789abcdef") == "0123456"
    assert sha7(None) == ""
