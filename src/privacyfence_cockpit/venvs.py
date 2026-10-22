"""The recipe environment: one virtualenv per requirements fingerprint, gated on the sync version."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404  # fixed argv, no shell; building the environment is this module's job
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.events import Event, EventKind, EventSink
from privacyfence_cockpit.models import iso, utc_now
from privacyfence_cockpit.paths import DataPaths

GATE_MESSAGE = (
    "privacyfence-sync in requirements.txt is too old for the cockpit: "
    "pin the first version with run --result-file and permissions"
)
PIN_MESSAGE = "requirements.txt must pin privacyfence-sync (privacyfence-sync==X.Y.Z)"
PIP_TIMEOUT_SECONDS = 900
VERIFY_TIMEOUT_SECONDS = 60
KEEP_PREVIOUS = 2
KEEP_BRANCH_VENVS = 2
READY_MARKER = ".cockpit-ready"
_BASE_PATH = "/usr/local/bin:/usr/bin:/bin"

_PIN = re.compile(r"^privacyfence[-_.]sync\s*==\s*\S+$", re.IGNORECASE)
_EDITABLE = re.compile(r"^(?:-e|--editable)\s+(\S+)$")

Run = Callable[..., subprocess.CompletedProcess[str]]


class VenvError(RuntimeError):
    pass


@dataclass(frozen=True)
class VenvResult:
    path: Path | None
    built: bool
    error: str | None


def _requirement_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if line:
            lines.append(line)
    return lines


def _local_path(line: str) -> str | None:
    match = _EDITABLE.match(line)
    if match:
        return match.group(1)
    if line.startswith("./") and not any(ch.isspace() for ch in line):
        return line
    return None


def requirement_files(repo: Path) -> list[Path]:
    requirements = repo / "requirements.txt"
    if not requirements.is_file():
        raise VenvError("the recipe repository has no requirements.txt")
    root = repo.resolve()
    found = {Path("requirements.txt")}
    for line in _requirement_lines(requirements):
        local = _local_path(line)
        if local is None:
            continue
        folder = (root / local).resolve()
        if not folder.is_relative_to(root):
            raise VenvError(f"requirements.txt names {local}, which is outside the repository")
        pyproject = folder / "pyproject.toml"
        if pyproject.is_file():
            found.add(pyproject.relative_to(root))
    return [repo / rel for rel in sorted(found)]


def _has_local_packages(repo: Path) -> bool:
    """True when the environment would embed paths of, or copies from, the checkout it is built in."""
    if len(requirement_files(repo)) > 1:
        return True
    return any(_local_path(line) is not None for line in _requirement_lines(repo / "requirements.txt"))


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pin_problem(repo: Path) -> str | None:
    files = requirement_files(repo)
    for line in _requirement_lines(repo / "requirements.txt"):
        if _PIN.match(line):
            return None
    for path in files:
        if path.name != "pyproject.toml":
            continue
        try:
            name = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {}).get("name")
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            continue
        if isinstance(name, str) and _normalise(name) == "privacyfence-sync":
            return None
    return PIN_MESSAGE


def venv_hash(repo: Path, python_version: str) -> str:
    digest = hashlib.sha256()
    for path in requirement_files(repo):
        digest.update(path.relative_to(repo).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    digest.update(b"python " + python_version.encode())
    return digest.hexdigest()


class VenvManager:
    def __init__(
        self,
        paths: DataPaths,
        db: Database,
        events: EventSink,
        python: str = sys.executable,
        run: Run = subprocess.run,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.paths = paths
        self.db = db
        self.events = events
        self.python = python
        self._run = run
        self.clock = clock

    def sync_bin(self, venv: Path) -> Path:
        return venv / "bin" / "privacyfence-sync"

    def current(self) -> Path | None:
        name = self.db.get_setting("venv.current")
        if not name:
            return None
        venv = self.paths.venvs / name
        return venv if (venv / READY_MARKER).exists() else None

    def ensure(self) -> VenvResult:
        repo = self.paths.repo
        try:
            problem = pin_problem(repo)
            digest = venv_hash(repo, self._python_version())
        except VenvError as exc:
            return self._fail(hashlib.sha256(str(exc).encode()).hexdigest(), str(exc), None)
        if problem is not None:
            return self._fail(digest, problem, None)
        target = self.paths.venvs / digest
        if (target / READY_MARKER).exists():
            self._switch(digest)
            return VenvResult(target, False, None)
        if target.exists():
            shutil.rmtree(target)
        error = self._build(target, digest, self.paths.repo)
        if error is not None:
            return self._fail(digest, error, target)
        (target / READY_MARKER).write_text("")
        self._switch(digest)
        return VenvResult(target, True, None)

    def ensure_for(self, repo: Path) -> VenvResult:
        """The environment for a branch worktree; never touches the configured one or reports a failure."""
        try:
            problem = pin_problem(repo)
            digest = venv_hash(repo, self._python_version())
            local = _has_local_packages(repo)
        except VenvError as exc:
            return VenvResult(None, False, str(exc))
        if problem is not None:
            return VenvResult(None, False, problem)
        configured = self.paths.venvs / digest
        if not local and (configured / READY_MARKER).exists():
            return VenvResult(configured, False, None)
        target = self.paths.branch_venvs / (f"{repo.name}-{digest}" if local else digest)
        marker = target / READY_MARKER
        if marker.exists():
            self._touch(marker)
            return VenvResult(target, False, None)
        self.paths.branch_venvs.mkdir(parents=True, exist_ok=True)
        self.paths.branch_venvs.chmod(0o700)
        if target.exists():
            shutil.rmtree(target)
        error = self._build(target, digest, repo)
        if error is not None:
            shutil.rmtree(target, ignore_errors=True)
            return VenvResult(None, False, error)
        marker.write_text("")
        self._touch(marker)
        self._prune_branch_venvs(target)
        return VenvResult(target, True, None)

    def _touch(self, marker: Path) -> None:
        stamp = self.clock().timestamp()
        os.utime(marker, (stamp, stamp))

    def _prune_branch_venvs(self, target: Path) -> None:
        others = [f for f in self.paths.branch_venvs.iterdir() if f.is_dir() and f != target]

        def age(folder: Path) -> float:
            marker = folder / READY_MARKER
            return marker.stat().st_mtime if marker.exists() else -1.0

        others.sort(key=age, reverse=True)
        for folder in others[KEEP_BRANCH_VENVS - 1 :]:
            shutil.rmtree(folder, ignore_errors=True)

    def _python_version(self) -> str:
        if self.python == sys.executable:
            return ".".join(map(str, sys.version_info[:3]))
        result = self._run(
            [self.python, "-c", "import platform; print(platform.python_version())"],
            capture_output=True,
            text=True,
            timeout=VERIFY_TIMEOUT_SECONDS,
            check=False,
        )
        return str(result.stdout).strip() if result.returncode == 0 else "unknown"

    def _build(self, target: Path, digest: str, repo: Path) -> str | None:
        log = self.paths.logs / "venv" / f"{digest}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        env = {
            "PATH": _BASE_PATH,
            "HOME": str(self.paths.home),
            "LANG": "C.UTF-8",
            "PIP_CACHE_DIR": str(self.paths.home / ".cache/pip"),
        }
        create = [self.python, "-m", "venv", str(target)]
        pip = [
            str(target / "bin/python"), "-m", "pip", "install",
            "--disable-pip-version-check", "--no-input", "-r", "requirements.txt",
        ]  # fmt: skip
        for argv, cwd in ((create, None), (pip, repo)):
            try:
                result = self._run(
                    argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=PIP_TIMEOUT_SECONDS, check=False
                )
            except (subprocess.TimeoutExpired, OSError):
                self._append(log, argv, "did not finish")
                return f"{self._step(argv)} did not finish; see logs/venv/{digest}.log"
            self._append(log, argv, f"{result.stdout}{result.stderr}")
            if result.returncode != 0:
                return f"{self._step(argv)} failed with exit code {result.returncode}; see logs/venv/{digest}.log"
        return self._verify(target)

    @staticmethod
    def _step(argv: list[str]) -> str:
        return "pip install" if "pip" in argv else "creating the environment"

    @staticmethod
    def _append(log: Path, argv: list[str], output: str) -> None:
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"$ {' '.join(argv)}\n{output}\n")

    def _verify(self, target: Path) -> str | None:
        sync = str(self.sync_bin(target))
        env = {"PATH": f"{target}/bin:{_BASE_PATH}", "HOME": str(self.paths.home), "LANG": "C.UTF-8"}

        def probe(*args: str) -> subprocess.CompletedProcess[str] | None:
            try:
                return self._run(
                    [sync, *args], env=env, capture_output=True, text=True, timeout=VERIFY_TIMEOUT_SECONDS, check=False
                )
            except (subprocess.TimeoutExpired, OSError):
                return None

        base = probe("--help")
        if base is None or base.returncode != 0:
            return "privacyfence-sync --help failed in the new environment"
        run_help = probe("run", "--help")
        if run_help is None or "--result-file" not in f"{run_help.stdout}{run_help.stderr}":
            return GATE_MESSAGE
        permissions_help = probe("permissions", "--help")
        if (
            permissions_help is None
            or permissions_help.returncode != 0
            or "--json" not in f"{permissions_help.stdout}{permissions_help.stderr}"
        ):
            return GATE_MESSAGE
        return None

    def _fail(self, digest: str, error: str, target: Path | None) -> VenvResult:
        now = self.clock()
        self.db.set_setting("venv.last_error", error)
        self.db.set_setting("venv.last_error_at", iso(now))
        key = f"venv:{digest}"
        if self.db.query_one("SELECT 1 FROM notices_sent WHERE key = ?", (key,)) is None:
            self.events.emit(Event(kind=EventKind.VENV_FAILED, time=now, detail=error))
            self.db.execute("INSERT INTO notices_sent (key, sent_at) VALUES (?, ?)", (key, iso(now)))
        if target is not None:
            shutil.rmtree(target, ignore_errors=True)
        return VenvResult(self.current(), False, error)

    def _previous(self) -> list[str]:
        raw = self.db.get_setting("venv.previous")
        try:
            names = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            return []
        return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []

    def _switch(self, digest: str) -> None:
        old = self.db.get_setting("venv.current")
        previous = [name for name in self._previous() if name != digest]
        if old and old != digest:
            previous = [old, *[name for name in previous if name != old]]
        previous = previous[:KEEP_PREVIOUS]
        self.db.set_setting("venv.current", digest)
        self.db.set_setting("venv.previous", json.dumps(previous))
        self.db.delete_setting("venv.last_error")
        self.db.delete_setting("venv.last_error_at")
        keep = {digest, *previous}
        for folder in self.paths.venvs.iterdir():
            if folder.is_dir() and folder.name not in keep:
                shutil.rmtree(folder, ignore_errors=True)
