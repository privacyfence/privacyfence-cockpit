from pathlib import Path

from cockpit_factory import make_cockpit
from webclient import PASSWORD, client, form_token, logged_in

from privacyfence_cockpit.web.auth import check_password

NEW_PASSWORD = "another long passphrase"


def post(c, token: str, **fields: str):
    return c.post("/settings", data={"csrf_token": token, **fields}, follow_redirects=False)


def test_page_shows_effective_values_and_sources(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    c, _ = logged_in(cockpit)
    page = c.get("/settings")
    assert page.status_code == 200
    assert "from none" in page.text and "from default" in page.text
    assert "cockpit.yaml wins" not in page.text


def test_every_field_is_saved_and_shown(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    c, token = logged_in(cockpit)
    response = post(
        c,
        token,
        server_url="https://pf.example.com/mcp",
        timezone="Europe/Budapest",
        log_retention_days="30",
        git_fetch_interval_minutes="60",
        auto_update="1",
    )
    assert response.status_code == 303
    assert cockpit.db.get_setting("server_url") == "https://pf.example.com/mcp"
    assert cockpit.db.get_setting("timezone") == "Europe/Budapest"
    assert cockpit.db.get_setting("log_retention_days") == "30"
    assert cockpit.db.get_setting("git.fetch_interval_minutes") == "60"
    assert cockpit.db.get_setting("git.auto_update") == "1"
    eff = cockpit.effective()
    assert (eff.retention_days, eff.fetch_interval_minutes, eff.auto_update) == (30, 60, True)
    page = c.get("/settings")
    assert "Saved." in page.text
    assert 'value="Europe/Budapest"' in page.text and "checked" in page.text
    assert "(from settings)" in page.text


def test_empty_values_unset_the_overrides(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    c, token = logged_in(cockpit)
    post(c, token, server_url="https://pf.example.com", timezone="UTC", log_retention_days="5", auto_update="1")
    post(c, token)
    for key in ("server_url", "timezone", "log_retention_days", "git.fetch_interval_minutes"):
        assert cockpit.db.get_setting(key) is None
    assert cockpit.db.get_setting("git.auto_update") == "0"


def test_invalid_values_show_their_message_and_save_nothing(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    c, token = logged_in(cockpit)
    cases = [
        ({"timezone": "Mars/Base"}, "Timezone Mars/Base is unknown."),
        ({"server_url": "ftp://x"}, "Server URL must start with http:// or https://."),
        ({"log_retention_days": "0"}, "must be a whole number between 1 and 3650."),
        ({"log_retention_days": "3651"}, "must be a whole number between 1 and 3650."),
        ({"log_retention_days": "ten"}, "must be a whole number between 1 and 3650."),
        ({"git_fetch_interval_minutes": "4"}, "must be a whole number between 5 and 1440."),
        ({"git_fetch_interval_minutes": "-5"}, "must be a whole number between 5 and 1440."),
    ]
    for fields, message in cases:
        response = post(c, token, **fields)
        assert response.status_code == 400, fields
        assert message in response.text, fields
    assert cockpit.db.query("SELECT * FROM settings WHERE key NOT LIKE 'auth.%'") == []


def test_cockpit_yaml_wins_and_the_page_says_so(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    (tmp_path / "data" / "repo").mkdir(parents=True)
    (tmp_path / "data" / "repo" / "cockpit.yaml").write_text(
        "version: 1\nserver: https://yaml.example.com/mcp\ntimezone: Europe/Budapest\n"
    )
    c, token = logged_in(cockpit)
    post(c, token, server_url="https://settings.example.com", timezone="UTC")
    page = c.get("/settings")
    assert page.text.count("cockpit.yaml wins over this setting") == 2
    assert "https://yaml.example.com/mcp" in page.text and "(from cockpit.yaml)" in page.text


def test_a_broken_cockpit_yaml_is_shown_not_fatal(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    (tmp_path / "data" / "repo").mkdir(parents=True)
    (tmp_path / "data" / "repo" / "cockpit.yaml").write_text("version: 2\n")
    c, _ = logged_in(cockpit)
    page = c.get("/settings")
    assert page.status_code == 200 and "cockpit.yaml: " in page.text


def password(c, token: str, **fields: str):
    data = {"csrf_token": token, "current": PASSWORD, "password": NEW_PASSWORD, "confirm": NEW_PASSWORD}
    return c.post("/settings/password", data={**data, **fields}, follow_redirects=False)


def test_password_wrong_current_mismatch_and_short(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    c, token = logged_in(cockpit)
    wrong = password(c, token, current="nope nope nope")
    assert wrong.status_code == 400 and "Wrong password." in wrong.text
    assert "nope nope nope" not in wrong.text
    mismatch = password(c, token, confirm="something else entirely")
    assert "The passwords do not match." in mismatch.text
    short = password(c, token, password="short", confirm="short")
    assert "Use at least 12 characters." in short.text
    assert check_password(cockpit.db, PASSWORD)


def test_password_change_logs_out_other_sessions(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    c, token = logged_in(cockpit)
    other = client(cockpit)
    page = other.get("/login")
    other.post("/login", data={"password": PASSWORD, "csrf_token": form_token(page.text)})
    assert other.get("/settings", follow_redirects=False).status_code == 200
    response = password(c, token)
    assert response.status_code == 303
    assert check_password(cockpit.db, NEW_PASSWORD) and not check_password(cockpit.db, PASSWORD)
    assert other.get("/settings", follow_redirects=False).status_code == 303
    page = c.get("/settings")
    assert page.status_code == 200 and "Password changed" in page.text
    assert NEW_PASSWORD not in page.text


def test_repeated_wrong_current_passwords_are_throttled(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    c, token = logged_in(cockpit)
    for _ in range(5):
        assert password(c, token, current="wrong wrong wrong").status_code == 400
    refused = password(c, token)
    assert refused.status_code == 429 and "Too many failed attempts" in refused.text
    assert check_password(cockpit.db, PASSWORD)
