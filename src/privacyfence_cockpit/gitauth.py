"""Git credentials for the recipe repository: deploy key, access token, SSH host keys.

Secret material reaches git only as a short-lived file in the temp folder, never in argv or in the
environment (the environment carries only the file's path).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import shutil
import subprocess  # nosec B404  # fixed argv, no shell; ssh-keyscan is run to learn a server's host keys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.gitrepo import Credentials, GitError
from privacyfence_cockpit.paths import DataPaths
from privacyfence_cockpit.secrets_store import SecretStore

KEY_COMMENT = "privacyfence-cockpit"
SCAN_TIMEOUT_SECONDS = 30
GITHUB_HOST = "github.com"

_SCP_URL = re.compile(r"^[A-Za-z0-9._-]+@(?P<host>[A-Za-z0-9.-]+):")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
_GIT_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
}

# GitHub's published SSH host keys (https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)
GITHUB_KNOWN_HOSTS = (
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"  # noqa: E501
    "github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=\n"  # noqa: E501
    "github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=\n"  # noqa: E501
)


@dataclass(frozen=True)
class HostKey:
    host: str
    port: int
    key_type: str
    key_b64: str

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256(base64.b64decode(self.key_b64)).digest()
        return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")

    @property
    def known_hosts_line(self) -> str:
        name = self.host if self.port == 22 else f"[{self.host}]:{self.port}"
        return f"{name} {self.key_type} {self.key_b64}"


def generate_deploy_key(store: SecretStore, db: Database) -> str:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    store.set("git", "ssh_key", private.decode())
    text = f"{public.decode()} {KEY_COMMENT}"
    db.set_setting("git.ssh_public_key", text)
    return text


def set_pat(store: SecretStore, value: str) -> None:
    value = value.strip()
    if not value or any(ch.isspace() for ch in value):
        raise ValueError("That does not look like a GitHub token.")
    store.set("git", "pat", value)


def host_of(url: str) -> tuple[str, int] | None:
    match = _SCP_URL.match(url)
    if match:
        return match.group("host"), 22
    if url.startswith("ssh://"):
        try:
            parts = urlsplit(url)
            hostname, port = parts.hostname, parts.port
        except ValueError:
            return None
        if hostname:
            return hostname, port or 22
    return None


def _ssh_dir(paths: DataPaths) -> Path:
    folder = paths.home / ".ssh"
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    return folder


def _write_private(path: Path, text: str, mode: str = "w") -> None:
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    with path.open(mode) as handle:
        handle.write(text)


def write_builtin_known_hosts(paths: DataPaths) -> None:
    path = _ssh_dir(paths) / "known_hosts_github"
    path.write_text(GITHUB_KNOWN_HOSTS)
    path.chmod(0o600)


def is_known(paths: DataPaths, host: str, port: int) -> bool:
    if host == GITHUB_HOST and port == 22:
        return True
    wanted = host if port == 22 else f"[{host}]:{port}"
    known = paths.home / ".ssh" / "known_hosts"
    if not known.is_file():
        return False
    for line in known.read_text().splitlines():
        fields = line.split()
        if fields and not fields[0].startswith("#") and wanted in fields[0].split(","):
            return True
    return False


def scan_host(
    host: str,
    port: int,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[HostKey]:
    if not _HOST.match(host):
        raise GitError(f"{host!r} is not a valid host name")
    argv = ["ssh-keyscan", "-T", "10", "-p", str(port), "-t", "ed25519,ecdsa,rsa", host]
    try:
        # fixed argv, host validated above; `run` is subprocess.run outside tests
        result = run(
            argv, env=_GIT_ENV, capture_output=True, text=True, timeout=SCAN_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"no SSH host keys from {host}:{port}") from exc
    except OSError as exc:
        raise GitError(f"ssh-keyscan could not be run: {exc.strerror}") from exc
    keys: list[HostKey] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and not line.startswith("#"):
            keys.append(HostKey(host, port, fields[1], fields[2]))
    if not keys:
        raise GitError(f"no SSH host keys from {host}:{port}")
    return keys


def stage_host_keys(db: Database, keys: list[HostKey]) -> None:
    db.set_setting("git.pending_host_keys", json.dumps([asdict(key) for key in keys]))


def confirm_host_keys(db: Database, paths: DataPaths) -> list[HostKey]:
    raw = db.get_setting("git.pending_host_keys")
    keys = [HostKey(**item) for item in json.loads(raw)] if raw else []
    if keys:
        _write_private(_ssh_dir(paths) / "known_hosts", "".join(key.known_hosts_line + "\n" for key in keys), "a")
    db.delete_setting("git.pending_host_keys")
    return keys


def credentials_factory(db: Database, store: SecretStore, paths: DataPaths) -> Credentials:
    @contextmanager
    def credentials() -> Iterator[dict[str, str]]:
        auth = db.get_setting("git.auth")
        if auth not in ("ssh", "https"):
            yield {}
            return
        if auth == "ssh":
            secret = store.get("git", "ssh_key")
            if secret is None:
                raise GitError("No deploy key: generate one on the Code page.")
        else:
            secret = store.get("git", "pat")
            if secret is None:
                raise GitError("No access token: add one on the Code page.")
        folder = Path(tempfile.mkdtemp(prefix="cockpit-git-"))
        try:
            if auth == "ssh":
                key = folder / "id_ed25519"
                _write_private(key, secret if secret.endswith("\n") else secret + "\n")
                ssh_dir = paths.home / ".ssh"
                yield {
                    "GIT_SSH_COMMAND": shlex.join(
                        [
                            "ssh",
                            "-i",
                            str(key),
                            "-o",
                            "IdentitiesOnly=yes",
                            "-o",
                            "BatchMode=yes",
                            "-o",
                            "StrictHostKeyChecking=yes",
                            "-o",
                            f"UserKnownHostsFile={ssh_dir}/known_hosts_github {ssh_dir}/known_hosts",
                        ]
                    )
                }
            else:
                token_file = folder / "secret"
                _write_private(token_file, secret)
                helper = (
                    '!f() { test "$1" = get || exit 0; echo username=x-access-token; '
                    f"printf 'password=%s\\n' \"$(cat {shlex.quote(str(token_file))})\"; }}; f"
                )
                yield {
                    "GIT_CONFIG_COUNT": "2",
                    "GIT_CONFIG_KEY_0": "credential.helper",
                    "GIT_CONFIG_VALUE_0": "",
                    "GIT_CONFIG_KEY_1": "credential.helper",
                    "GIT_CONFIG_VALUE_1": helper,
                }
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    return credentials
