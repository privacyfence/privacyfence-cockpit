"""Settings merged from cockpit.yaml, the owner's overrides and the environment."""

from __future__ import annotations

from dataclasses import dataclass

from privacyfence_cockpit.cockpit_yaml import CockpitConfig
from privacyfence_cockpit.config import EnvConfig
from privacyfence_cockpit.db import Database

DEFAULT_TIMEZONE = "UTC"
DEFAULT_RETENTION_DAYS = 90
DEFAULT_FETCH_INTERVAL_MINUTES = 15


@dataclass(frozen=True)
class Effective:
    server_url: str | None
    server_source: str
    timezone: str
    timezone_source: str
    timeout_minutes: int
    retention_days: int
    fetch_interval_minutes: int
    auto_update: bool


def _positive_int(text: str | None, default: int) -> int:
    try:
        value = int(text) if text is not None else default
    except ValueError:
        return default
    return value if value >= 1 else default


def effective(db: Database, config: CockpitConfig, env: EnvConfig) -> Effective:
    setting_server = db.get_setting("server_url")
    if config.server:
        server_url, server_source = config.server, "cockpit.yaml"
    elif setting_server:
        server_url, server_source = setting_server, "settings"
    elif env.server_url:
        server_url, server_source = env.server_url, "COCKPIT_SERVER_URL"
    else:
        server_url, server_source = None, "none"

    setting_timezone = db.get_setting("timezone")
    if config.timezone:
        timezone, timezone_source = config.timezone, "cockpit.yaml"
    elif setting_timezone:
        timezone, timezone_source = setting_timezone, "settings"
    else:
        timezone, timezone_source = DEFAULT_TIMEZONE, "default"

    return Effective(
        server_url=server_url,
        server_source=server_source,
        timezone=timezone,
        timezone_source=timezone_source,
        timeout_minutes=config.timeout_minutes,
        retention_days=_positive_int(db.get_setting("log_retention_days"), DEFAULT_RETENTION_DAYS),
        fetch_interval_minutes=_positive_int(
            db.get_setting("git.fetch_interval_minutes"), DEFAULT_FETCH_INTERVAL_MINUTES
        ),
        auto_update=db.get_setting("git.auto_update") == "1",
    )
