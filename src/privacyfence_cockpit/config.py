"""Settings the container receives through environment variables."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EnvConfig:
    data_dir: Path = Path("/data")
    master_key_file: Path = Path("/run/secrets/cockpit_master_key")
    public_url: str | None = None
    server_url: str | None = None
    listen_host: str = "0.0.0.0"  # noqa: S104  # nosec B104  # the container publishes the port on 127.0.0.1 only
    listen_port: int = 8080
    log_level: str = "INFO"
    env_token: str | None = field(default=None, repr=False)

    @property
    def secure_cookies(self) -> bool:
        return self.public_url is not None and self.public_url.startswith("https://")

    def __repr__(self) -> str:
        token = "'<set>'" if self.env_token else "None"
        return (
            f"EnvConfig(data_dir={self.data_dir!r}, master_key_file={self.master_key_file!r}, "
            f"public_url={self.public_url!r}, server_url={self.server_url!r}, "
            f"listen_host={self.listen_host!r}, listen_port={self.listen_port!r}, "
            f"log_level={self.log_level!r}, env_token={token})"
        )

    @classmethod
    def from_environ(cls, environ: MutableMapping[str, str]) -> EnvConfig:
        defaults = cls()
        token = environ.pop("PRIVACYFENCE_TOKEN", "").strip() or None
        if token is not None and not token.startswith("pfst_"):
            raise ConfigError("PRIVACYFENCE_TOKEN must be a PrivacyFence scoped token (pfst_…)")

        port_text = environ.get("COCKPIT_LISTEN_PORT", "").strip()
        port = defaults.listen_port
        if port_text:
            try:
                port = int(port_text)
            except ValueError:
                port = 0
            if not 1 <= port <= 65535:
                raise ConfigError("COCKPIT_LISTEN_PORT must be a number between 1 and 65535")

        public_url = environ.get("COCKPIT_PUBLIC_URL", "").strip().rstrip("/") or None
        if public_url is not None and not public_url.startswith(("http://", "https://")):
            raise ConfigError("COCKPIT_PUBLIC_URL must start with http:// or https://")

        return cls(
            data_dir=Path(environ.get("COCKPIT_DATA_DIR") or defaults.data_dir),
            master_key_file=Path(environ.get("COCKPIT_MASTER_KEY_FILE") or defaults.master_key_file),
            public_url=public_url,
            server_url=environ.get("COCKPIT_SERVER_URL", "").strip() or None,
            listen_host=environ.get("COCKPIT_LISTEN_HOST") or defaults.listen_host,
            listen_port=port,
            log_level=environ.get("COCKPIT_LOG_LEVEL") or defaults.log_level,
            env_token=token,
        )
