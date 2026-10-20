from pathlib import Path

import pytest

from privacyfence_cockpit.config import ConfigError, EnvConfig


def test_defaults():
    config = EnvConfig.from_environ({})
    assert config.data_dir == Path("/data")
    assert config.master_key_file == Path("/run/secrets/cockpit_master_key")
    assert config.public_url is None
    assert config.server_url is None
    assert config.listen_host == "0.0.0.0"  # noqa: S104
    assert config.listen_port == 8080
    assert config.log_level == "INFO"
    assert config.env_token is None
    assert config.secure_cookies is False


def test_every_variable():
    environ = {
        "COCKPIT_DATA_DIR": "/srv/data",
        "COCKPIT_MASTER_KEY_FILE": "/keys/master",
        "COCKPIT_PUBLIC_URL": "https://cockpit.example.org/",
        "COCKPIT_SERVER_URL": "https://pf.example.org/mcp",
        "COCKPIT_LISTEN_HOST": "127.0.0.1",
        "COCKPIT_LISTEN_PORT": "9000",
        "COCKPIT_LOG_LEVEL": "DEBUG",
        "PRIVACYFENCE_TOKEN": "  pfst_abcdefghijklmnopqrstuvwxyz  ",
    }
    config = EnvConfig.from_environ(environ)
    assert config.data_dir == Path("/srv/data")
    assert config.master_key_file == Path("/keys/master")
    assert config.public_url == "https://cockpit.example.org"
    assert config.server_url == "https://pf.example.org/mcp"
    assert config.listen_host == "127.0.0.1"
    assert config.listen_port == 9000
    assert config.log_level == "DEBUG"
    assert config.env_token == "pfst_abcdefghijklmnopqrstuvwxyz"


def test_empty_values_fall_back_to_defaults():
    config = EnvConfig.from_environ({"COCKPIT_PUBLIC_URL": "", "COCKPIT_LISTEN_PORT": "", "PRIVACYFENCE_TOKEN": " "})
    assert config.public_url is None
    assert config.listen_port == 8080
    assert config.env_token is None


def test_token_is_popped_from_the_mapping():
    environ = {"PRIVACYFENCE_TOKEN": "pfst_secretsecretsecret", "COCKPIT_LOG_LEVEL": "INFO"}
    EnvConfig.from_environ(environ)
    assert "PRIVACYFENCE_TOKEN" not in environ
    assert "COCKPIT_LOG_LEVEL" in environ


def test_repr_hides_the_token():
    config = EnvConfig.from_environ({"PRIVACYFENCE_TOKEN": "pfst_secretsecretsecret"})
    assert "pfst_" not in repr(config)
    assert "env_token='<set>'" in repr(config)
    assert "env_token=None" in repr(EnvConfig.from_environ({}))


@pytest.mark.parametrize("value", ["abc", "0", "65536", "-1"])
def test_bad_port(value):
    with pytest.raises(ConfigError, match="COCKPIT_LISTEN_PORT must be a number between 1 and 65535"):
        EnvConfig.from_environ({"COCKPIT_LISTEN_PORT": value})


def test_bad_public_url():
    with pytest.raises(ConfigError, match="COCKPIT_PUBLIC_URL must start with http:// or https://"):
        EnvConfig.from_environ({"COCKPIT_PUBLIC_URL": "cockpit.example.org"})


def test_bad_token_never_in_message():
    with pytest.raises(ConfigError) as info:
        EnvConfig.from_environ({"PRIVACYFENCE_TOKEN": "sk-not-scoped-token"})
    assert str(info.value) == "PRIVACYFENCE_TOKEN must be a PrivacyFence scoped token (pfst_…)"
    assert "sk-not" not in str(info.value)


def test_config_error_is_value_error():
    assert issubclass(ConfigError, ValueError)


@pytest.mark.parametrize(
    ("url", "expected"),
    [("https://c.example.org", True), ("http://127.0.0.1:8080", False), (None, False)],
)
def test_secure_cookies(url, expected):
    assert EnvConfig(public_url=url).secure_cookies is expected
