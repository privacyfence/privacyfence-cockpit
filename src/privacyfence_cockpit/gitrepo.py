"""The recipe repository checkout: clone, fetch, inspect and move it, by running real git."""

from __future__ import annotations

import fcntl
import re
import shutil
import subprocess  # nosec B404  # fixed argv, no shell; git is the whole point of this module
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.events import Event, EventKind, EventSink
from privacyfence_cockpit.models import iso, parse_iso, utc_now
from privacyfence_cockpit.paths import DataPaths, branch_folder

TIMEOUT_SECONDS = 300
DIFF_LIMIT = 512 * 1024
FAILING_AFTER = timedelta(hours=24)
FETCH_NOTICE = "git:fetch-failing"

_URL = re.compile(r"^(https://|ssh://|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:)")
_SCHEME_URL = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://(.*)$", re.DOTALL)
_URL_USERINFO = re.compile(r"(://)\S*@")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_AUTH_MODES = ("ssh", "https", "none")

Credentials = Callable[[], AbstractContextManager[dict[str, str]]]


class GitError(RuntimeError):
    pass


def git_env(home: Path, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """The minimal environment every git process of the cockpit runs with."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        **(extra_env or {}),
    }


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    date: str
    subject: str


@dataclass(frozen=True)
class FileChange:
    path: str
    added: int | None
    deleted: int | None


@dataclass(frozen=True)
class CommitDetail:
    commit: Commit
    message: str
    files: tuple[FileChange, ...]


@dataclass(frozen=True)
class GitStatus:
    configured: bool
    cloned: bool
    branch: str | None
    head: str | None
    remote_tip: str | None
    pinned_sha: str | None
    new_commits: int
    dirty: bool
    last_fetch_ok_at: str | None
    last_fetch_error: str | None


def no_credentials() -> AbstractContextManager[dict[str, str]]:
    return nullcontext({})


class GitManager:
    def __init__(
        self,
        paths: DataPaths,
        db: Database,
        credentials: Credentials,
        events: EventSink,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._paths = paths
        self._db = db
        self._credentials = credentials
        self._events = events
        self._clock = clock

    def configure(self, url: str, branch: str, auth: str) -> None:
        url = url.strip()
        is_local = url.startswith("/") or url.startswith("file://")
        if not (_URL.match(url) or is_local):
            raise GitError("The repository URL must be https://, ssh://, user@host:path or a local path.")
        if _carries_credentials(url):
            raise GitError("Do not put credentials in the repository URL; use the access token field instead.")
        if not _BRANCH.match(branch) or branch.startswith("-"):
            raise GitError("That is not a valid branch name.")
        if auth not in _AUTH_MODES:
            raise GitError("The access method must be ssh, https or none.")
        self._db.set_setting("git.repo_url", url)
        self._db.set_setting("git.branch", branch)
        self._db.set_setting("git.auth", auth)

    def status(self) -> GitStatus:
        url = self._db.get_setting("git.repo_url")
        branch = self._db.get_setting("git.branch")
        cloned = self._cloned()
        head = self.head()
        remote_tip: str | None = None
        new_commits = 0
        dirty = False
        if cloned and branch:
            remote_tip = self._rev_parse(f"origin/{branch}")
            if remote_tip:
                new_commits = int(self._checked("rev-list", "--count", f"HEAD..origin/{branch}").stdout)
            dirty = self._dirty()
        return GitStatus(
            configured=url is not None,
            cloned=cloned,
            branch=branch,
            head=head,
            remote_tip=remote_tip,
            pinned_sha=self._db.get_setting("git.pinned_sha"),
            new_commits=new_commits,
            dirty=dirty,
            last_fetch_ok_at=self._db.get_setting("git.last_fetch_ok_at"),
            last_fetch_error=self._db.get_setting("git.last_fetch_error"),
        )

    def clone(self) -> None:
        url = self._db.get_setting("git.repo_url")
        branch = self._db.get_setting("git.branch")
        if url is None or branch is None:
            raise GitError("The repository is not configured.")
        repo = self._paths.repo
        if repo.exists() and any(repo.iterdir()):
            raise GitError("the checkout already exists")
        argv = ["git", "clone", "--branch", branch, "--single-branch", "--", url, str(repo)]
        with self._lock(), self._credentials() as extra:
            result = self._run(argv, extra)
        if result.returncode != 0:
            raise GitError(f"git clone failed: {_first_line(result.stderr)}")

    def fetch(self) -> None:
        if not self._cloned():
            # Nothing to fetch into; this is not a fetch failure, so it is neither recorded nor notified.
            raise GitError("The repository has not been cloned.")
        now = self._clock()
        self._db.set_setting("git.last_fetch_attempt_at", iso(now))
        try:
            with self._lock(), self._credentials() as extra:
                argv = self._argv("fetch", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")
                result = self._run(argv, extra)
        except GitError as exc:
            self._fetch_failed(now, str(exc))
            raise
        if result.returncode != 0:
            message = f"git fetch failed: {_first_line(result.stderr)}"
            self._fetch_failed(now, message)
            raise GitError(message)
        self._db.set_setting("git.last_fetch_ok_at", iso(now))
        self._db.delete_setting("git.last_fetch_error")
        self._db.delete_setting("git.failing_since")
        self._db.execute("DELETE FROM notices_sent WHERE key = ?", (FETCH_NOTICE,))

    def log(self, limit: int = 200) -> list[Commit]:
        branch = self._require_branch()
        if self._rev_parse(f"origin/{branch}") is None:
            raise GitError(
                f"The remote has no branch '{branch}'. Check the branch name, or push a first commit to it."
            )
        out = self._checked(
            "log", "--format=%H%x1f%an%x1f%aI%x1f%s%x1e", "-n", str(limit), f"origin/{branch}"
        ).stdout
        commits = []
        for record in out.split("\x1e"):
            record = record.strip("\n")
            if record:
                sha, author, date, subject = record.split("\x1f", 3)
                commits.append(Commit(sha, author, date, subject))
        return commits

    def show(self, sha: str) -> CommitDetail:
        self._require_known_commit(sha)
        head = self._checked("show", "-s", "--format=%H%x1f%an%x1f%aI%x1f%s", sha).stdout.strip("\n")
        message = self._checked("show", "-s", "--format=%B", sha).stdout.strip("\n")
        numstat = self._checked("show", "--format=", "--numstat", sha).stdout
        files = []
        for line in numstat.splitlines():
            added, deleted, path = line.split("\t", 2)
            files.append(FileChange(path, _count(added), _count(deleted)))
        full_sha, author, date, subject = head.split("\x1f", 3)
        return CommitDetail(Commit(full_sha, author, date, subject), message, tuple(files))

    def diff(self, sha: str, path: str) -> str:
        detail = self.show(sha)
        if path not in {change.path for change in detail.files}:
            raise GitError("unknown file")
        out = self._checked("show", "--format=", sha, "--", path).stdout
        raw = out.encode("utf-8", errors="replace")
        if len(raw) <= DIFF_LIMIT:
            return out
        cut = raw[:DIFF_LIMIT].decode("utf-8", errors="ignore")
        return f"{cut}\n… diff truncated at 512 KiB"

    def update(self) -> None:
        branch = self._require_branch()
        pinned = self._db.get_setting("git.pinned_sha")
        if pinned:
            raise GitError(f"The checkout is pinned to {pinned[:7]}; return to tracking first.")
        self._refuse_dirty()
        self.fetch()
        with self._lock():
            result = self._run(self._argv("merge", "--ff-only", f"origin/{branch}"), {})
        if result.returncode != 0:
            raise GitError(
                f"origin/{branch} cannot be fast-forwarded onto the checkout (the branch was rewritten "
                "upstream). Check out its newest commit to run it."
            )

    def pin(self, sha: str) -> None:
        self._require_known_commit(sha)
        self._refuse_dirty()
        with self._lock():
            self._checked("checkout", "--detach", sha)
        self._db.set_setting("git.pinned_sha", sha)

    def track(self) -> None:
        branch = self._require_branch()
        self._refuse_dirty()
        with self._lock():
            self._checked("checkout", branch)
        self._db.delete_setting("git.pinned_sha")

    def head(self) -> str | None:
        if not self._cloned():
            return None
        return self._rev_parse("HEAD")

    def configured_branch(self) -> str | None:
        return self._db.get_setting("git.branch")

    def remote_branches(self) -> list[str]:
        """The branches fetched from the remote, without touching the network; never raises."""
        if not self._cloned():
            return []
        try:
            result = self._git("for-each-ref", "--format=%(refname:lstrip=3)", "refs/remotes/origin")
        except GitError:
            return []
        if result.returncode != 0:
            return []
        names = (line.strip() for line in result.stdout.splitlines())
        return sorted(name for name in names if name != "HEAD" and _safe_branch(name))

    def branch_worktree(self, branch: str) -> tuple[Path, str]:
        """Check out the fetched tip of a remote branch in its own worktree; return (path, sha)."""
        configured = self._require_branch()
        if branch == configured:
            raise GitError(f"{branch} is the configured branch; run it without choosing a branch.")
        remote = self.remote_branches()
        missing = GitError(f"There is no branch {branch} on the remote.")
        if branch not in remote:
            raise missing
        sha = self._checked("rev-parse", "--verify", f"refs/remotes/origin/{branch}^{{commit}}").stdout.strip()
        path = self._paths.branches / branch_folder(branch)
        if path.parent != self._paths.branches:
            raise missing
        with self._lock():
            self._prune_worktrees(remote)
            if (path / ".git").exists():
                if self._checked_in(path, "status", "--porcelain", "--untracked-files=no").stdout.strip():
                    raise GitError(
                        f"The checkout of {branch} has changes to tracked files and the cockpit never "
                        "overwrites them. Inspect them with: "
                        f"docker exec <container> git -C /data/branches/{branch_folder(branch)} status"
                    )
                self._checked_in(path, "checkout", "--quiet", "--detach", sha)
            else:
                self._paths.branches.mkdir(parents=True, exist_ok=True)
                self._paths.branches.chmod(0o700)
                shutil.rmtree(path, ignore_errors=True)
                self._checked("worktree", "add", "--detach", str(path), sha)
        return path, sha

    def _prune_worktrees(self, remote: list[str]) -> None:
        self._git("worktree", "prune")
        if not self._paths.branches.is_dir():
            return
        for folder in self._paths.branches.iterdir():
            if folder.name.replace("%2F", "/") not in remote:
                self._git("worktree", "remove", "--force", str(folder))
                shutil.rmtree(folder, ignore_errors=True)

    def _checked_in(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        result = self._run(["git", "-C", str(cwd), *args], {})
        if result.returncode != 0:
            raise GitError(f"git {args[0]} failed: {_first_line(result.stderr)}")
        return result

    def _cloned(self) -> bool:
        return (self._paths.repo / ".git").exists()

    def _require_branch(self) -> str:
        branch = self._db.get_setting("git.branch")
        if branch is None or not self._cloned():
            raise GitError("The repository has not been cloned.")
        return branch

    def _require_known_commit(self, sha: str) -> None:
        branch = self._require_branch()
        if not _SHA.match(sha):
            raise GitError("unknown commit")
        result = self._run(self._argv("merge-base", "--is-ancestor", sha, f"origin/{branch}"), {})
        if result.returncode != 0:
            raise GitError("unknown commit")

    def _refuse_dirty(self) -> None:
        if self._dirty():
            raise GitError(
                "The checkout has changes to tracked files and the cockpit never overwrites them. "
                "Inspect them with: docker exec <container> git -C /data/repo status"
            )

    def _dirty(self) -> bool:
        return bool(self._checked("status", "--porcelain", "--untracked-files=no").stdout.strip())

    def _rev_parse(self, rev: str) -> str | None:
        result = self._git("rev-parse", "--verify", "--quiet", rev)
        return result.stdout.strip() or None if result.returncode == 0 else None

    def _fetch_failed(self, now: datetime, message: str) -> None:
        self._db.set_setting("git.last_fetch_error", message)
        since = self._db.get_setting("git.failing_since")
        if since is None:
            since = iso(now)
            self._db.set_setting("git.failing_since", since)
        if now - parse_iso(since) <= FAILING_AFTER:
            return
        if self._db.query_one("SELECT 1 FROM notices_sent WHERE key = ?", (FETCH_NOTICE,)) is not None:
            return
        self._events.emit(Event(kind=EventKind.GIT_FETCH_FAILING, time=now, detail=message))
        self._db.execute("INSERT INTO notices_sent (key, sent_at) VALUES (?, ?)", (FETCH_NOTICE, iso(now)))

    def _argv(self, *args: str) -> list[str]:
        return ["git", "-C", str(self._paths.repo), *args]

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._run(self._argv(*args), {})

    def _checked(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = self._git(*args)
        if result.returncode != 0:
            raise GitError(f"git {args[0]} failed: {_first_line(result.stderr)}")
        return result

    def _run(self, argv: list[str], extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        env = git_env(self._paths.home, extra_env)
        try:
            return subprocess.run(  # noqa: S603  # nosec B603  # fixed argv, no shell
                argv, env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git timed out after {TIMEOUT_SECONDS} seconds") from exc
        except OSError as exc:
            raise GitError(f"git could not be run: {exc.strerror}") from exc

    @contextmanager
    def _lock(self) -> Iterator[None]:
        lock_file: Path = self._paths.git_lock
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        with lock_file.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _carries_credentials(url: str) -> bool:
    match = _SCHEME_URL.match(url)
    if match is None:
        return False
    scheme, rest = match.group(1).lower(), match.group(2)
    userinfo, at, _ = rest.rpartition("@")
    if not at:
        return False
    if scheme in ("http", "https"):
        return True
    return scheme == "ssh" and (":" in userinfo or "/" in userinfo)


def _safe_branch(name: str) -> bool:
    return (
        _BRANCH.match(name) is not None
        and not name.startswith("-")
        and not any(part.startswith(".") for part in name.split("/"))
    )


def _scrub(text: str) -> str:
    return _URL_USERINFO.sub(r"\1***@", text)


def _first_line(text: str) -> str:
    lines = _scrub(text).strip().splitlines()
    return lines[0][:200] if lines else "unknown error"


def _count(text: str) -> int | None:
    return None if text == "-" else int(text)
