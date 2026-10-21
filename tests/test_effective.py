import pytest

from privacyfence_cockpit.cockpit_yaml import CockpitConfig
from privacyfence_cockpit.config import EnvConfig
from privacyfence_cockpit.db import Database
from privacyfence_cockpit.effective import effective


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "cockpit.db")
    database.migrate()
    yield database
    database.close()


def env(**environ):
    return EnvConfig.from_environ(dict(environ))


def test_defaults(db):
    result = effective(db, CockpitConfig(), env())
    assert result.server_url is None
    assert result.server_source == "none"
    assert result.timezone == "UTC"
    assert result.timezone_source == "default"
    assert result.timeout_minutes == 60
    assert result.retention_days == 90
    assert result.fetch_interval_minutes == 15
    assert result.auto_update is False


def test_server_precedence(db):
    environment = env(COCKPIT_SERVER_URL="http://env/mcp")
    result = effective(db, CockpitConfig(), environment)
    assert (result.server_url, result.server_source) == ("http://env/mcp", "COCKPIT_SERVER_URL")
    db.set_setting("server_url", "http://setting/mcp")
    result = effective(db, CockpitConfig(), environment)
    assert (result.server_url, result.server_source) == ("http://setting/mcp", "settings")
    result = effective(db, CockpitConfig(server="http://yaml/mcp"), environment)
    assert (result.server_url, result.server_source) == ("http://yaml/mcp", "cockpit.yaml")


def test_empty_server_setting_is_ignored(db):
    db.set_setting("server_url", "")
    result = effective(db, CockpitConfig(), env(COCKPIT_SERVER_URL="http://env/mcp"))
    assert result.server_source == "COCKPIT_SERVER_URL"


def test_timezone_precedence(db):
    db.set_setting("timezone", "Europe/Budapest")
    result = effective(db, CockpitConfig(), env())
    assert (result.timezone, result.timezone_source) == ("Europe/Budapest", "settings")
    result = effective(db, CockpitConfig(timezone="Asia/Tokyo"), env())
    assert (result.timezone, result.timezone_source) == ("Asia/Tokyo", "cockpit.yaml")


def test_timeout_from_config(db):
    assert effective(db, CockpitConfig(timeout_minutes=5), env()).timeout_minutes == 5


def test_numeric_settings(db):
    db.set_setting("log_retention_days", "30")
    db.set_setting("git.fetch_interval_minutes", "5")
    result = effective(db, CockpitConfig(), env())
    assert (result.retention_days, result.fetch_interval_minutes) == (30, 5)


@pytest.mark.parametrize("bad", ["abc", "", "0", "-3", "1.5"])
def test_bad_numeric_settings_fall_back(db, bad):
    db.set_setting("log_retention_days", bad)
    db.set_setting("git.fetch_interval_minutes", bad)
    result = effective(db, CockpitConfig(), env())
    assert (result.retention_days, result.fetch_interval_minutes) == (90, 15)


@pytest.mark.parametrize(("value", "expected"), [("1", True), ("0", False), ("yes", False)])
def test_auto_update(db, value, expected):
    db.set_setting("git.auto_update", value)
    assert effective(db, CockpitConfig(), env()).auto_update is expected
