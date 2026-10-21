"""The Code page: the recipe repository, its commits, how it is updated, and its credentials."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from privacyfence_cockpit import gitauth
from privacyfence_cockpit.crypto import SecretDecryptError
from privacyfence_cockpit.gitauth import HostKey
from privacyfence_cockpit.gitrepo import Commit, CommitDetail, GitError
from privacyfence_cockpit.jobs import Job, JobKind
from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web.auth import require_csrf
from privacyfence_cockpit.web.render import redirect, render

COMMIT_LIMIT = 200
DEFAULT_BRANCH = "main"
SECRETS_UNREADABLE = (
    "The stored credentials cannot be read with this master key. Use the original key file, "
    "or replace the credential."
)

_SHA = re.compile(r"^[0-9a-f]{40}$")
_GITHUB_REPO = re.compile(
    r"^(?:git@github\.com:|ssh://(?:[^@/]+@)?github\.com(?::22)?/)(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)
_USERINFO = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://(?P<userinfo>[^/@]*)@")


def _cockpit(request: Request) -> Cockpit:
    cockpit: Cockpit = request.app.state.cockpit
    return cockpit


def display_url(url: str) -> str:
    """The repository URL without any credentials an older cockpit may have stored in it."""
    match = _USERINFO.match(url)
    if match is None:
        return url
    scheme = match.group("scheme").lower()
    if scheme in ("http", "https") or (scheme == "ssh" and ":" in match.group("userinfo")):
        return f"{match.group('scheme')}://***@{url[match.end():]}"
    return url


def deploy_key_link(url: str) -> str | None:
    match = _GITHUB_REPO.match(url)
    if match is None:
        return None
    return f"https://github.com/{match.group('owner')}/{match.group('repo')}/settings/keys/new"


def _pending_keys(cockpit: Cockpit) -> list[HostKey]:
    raw = cockpit.db.get_setting("git.pending_host_keys")
    return [HostKey(**item) for item in json.loads(raw)] if raw else []


@dataclass(frozen=True)
class Credential:
    """What the credentials box shows, and whether a clone can start with it."""

    auth: str
    public_key: str | None = None
    key_link: str | None = None
    token_present: bool = False
    host: str | None = None
    port: int = 22
    host_known: bool = True
    pending: tuple[HostKey, ...] = ()
    error: str | None = None

    @property
    def clone_ready(self) -> bool:
        if self.error is not None:
            return False
        if self.auth == "ssh":
            return self.public_key is not None and self.host_known
        if self.auth == "https":
            return self.token_present
        return True


def _credential(cockpit: Cockpit, url: str, auth: str) -> Credential:
    try:
        has_key = cockpit.store.has("git", "ssh_key")
        has_token = cockpit.store.has("git", "pat")
    except SecretDecryptError:
        return Credential(auth, error=SECRETS_UNREADABLE)
    if auth == "ssh":
        target = gitauth.host_of(url)
        known = target is None or gitauth.is_known(cockpit.paths, *target)
        return Credential(
            auth,
            public_key=cockpit.db.get_setting("git.ssh_public_key") if has_key else None,
            key_link=deploy_key_link(url),
            host=target[0] if target else None,
            port=target[1] if target else 22,
            host_known=known,
            pending=tuple(_pending_keys(cockpit)),
        )
    return Credential(auth, token_present=has_token)


def _commits(cockpit: Cockpit) -> tuple[list[Commit], str | None]:
    try:
        return cockpit.git.log(COMMIT_LIMIT), None
    except GitError as exc:
        return [], str(exc)


def _context(cockpit: Cockpit) -> dict[str, Any]:
    status = cockpit.git.status()
    context: dict[str, Any] = {
        "status": status,
        "tz": cockpit.effective().timezone,
        "default_branch": DEFAULT_BRANCH,
        "form_auth": "ssh",
        "setup_error": None,
    }
    if status.configured:
        url = cockpit.db.get_setting("git.repo_url") or ""
        auth = cockpit.db.get_setting("git.auth") or "none"
        commits, commits_error = _commits(cockpit) if status.cloned else ([], None)
        context |= {
            "url": display_url(url),
            "auth": auth,
            "credential": _credential(cockpit, url, auth),
            "commits": commits,
            "commits_error": commits_error,
        }
    return context


async def code_page(request: Request) -> Response:
    return render(request, "code.html", _context(_cockpit(request)))


def _commit_or_404(cockpit: Cockpit, sha: str) -> CommitDetail:
    try:
        return cockpit.git.show(sha)
    except GitError as exc:
        raise HTTPException(404, str(exc)) from exc


async def commit_page(request: Request) -> Response:
    cockpit = _cockpit(request)
    detail = await asyncio.to_thread(_commit_or_404, cockpit, request.path_params["sha"])
    return render(request, "commit.html", {"detail": detail, "tz": cockpit.effective().timezone})


async def diff_fragment(request: Request) -> Response:
    cockpit = _cockpit(request)
    sha = request.path_params["sha"]
    path = request.query_params.get("path", "")
    try:
        text = await asyncio.to_thread(cockpit.git.diff, sha, path)
    except GitError as exc:
        raise HTTPException(404, str(exc)) from exc
    lines = [(_diff_class(line), line) for line in text.splitlines()]
    return render(request, "diff.html", {"lines": lines})


def _diff_class(line: str) -> str:
    if line.startswith("+") and not line.startswith("+++"):
        return "add"
    if line.startswith("-") and not line.startswith("---"):
        return "del"
    return ""


async def setup(request: Request) -> Response:
    form = await require_csrf(request)
    cockpit = _cockpit(request)
    url = str(form.get("url", "")).strip()
    branch = str(form.get("branch", "")).strip() or DEFAULT_BRANCH
    auth = str(form.get("auth", "ssh"))
    previous = cockpit.db.get_setting("git.repo_url")
    try:
        cockpit.git.configure(url, branch, auth)
    except GitError as exc:
        # Never echo the submitted URL back: it may carry credentials.
        context = _context(cockpit) | {"default_branch": branch, "form_auth": auth, "setup_error": str(exc)}
        return render(request, "code.html", context, status_code=400)
    if previous is not None and previous != url and cockpit.git.status().cloned:
        return redirect("/code", "Saved. The URL changed: use Re-clone to fetch the new repository.")
    return redirect("/code", "Saved.")


async def _fetch(cockpit: Cockpit) -> None:
    try:
        await asyncio.to_thread(cockpit.git.fetch)
    except GitError:
        pass  # recorded in git.last_fetch_error, which the page shows


async def fetch_now(request: Request) -> Response:
    await require_csrf(request)
    cockpit = _cockpit(request)
    task: asyncio.Task[None] | None = getattr(request.app.state, "code_fetch_task", None)
    if task is not None and not task.done():
        return redirect("/code", "Already fetching…")
    request.app.state.code_fetch_task = asyncio.create_task(_fetch(cockpit))
    return redirect("/code", "Fetching…")


def _submit(request: Request, job: Job) -> Response:
    queue = _cockpit(request).queue
    busy = queue.running() is not None
    queue.submit(job)
    return redirect("/code", "Queued: it starts after the current run." if busy else "Started.")


async def update(request: Request) -> Response:
    await require_csrf(request)
    return _submit(request, Job(JobKind.GIT_UPDATE, "manual"))


async def track(request: Request) -> Response:
    await require_csrf(request)
    return _submit(request, Job(JobKind.GIT_TRACK, "manual"))


async def checkout(request: Request) -> Response:
    form = await require_csrf(request)
    sha = str(form.get("sha", ""))
    if not _SHA.match(sha):
        return redirect("/code", "That is not a commit.")
    return _submit(request, Job(JobKind.GIT_PIN, "manual", sha=sha))


async def clone(request: Request) -> Response:
    await require_csrf(request)
    cockpit = _cockpit(request)
    url = cockpit.db.get_setting("git.repo_url")
    if url is None:
        return redirect("/code", "Set the repository first.")
    credential = _credential(cockpit, url, cockpit.db.get_setting("git.auth") or "none")
    if not credential.clone_ready:
        return redirect("/code", credential.error or "Add the credential below before cloning.")
    return _submit(request, Job(JobKind.GIT_CLONE, "manual"))


async def ssh_key(request: Request) -> Response:
    form = await require_csrf(request)
    cockpit = _cockpit(request)
    try:
        if cockpit.store.has("git", "ssh_key") and form.get("confirm") != "1":
            return redirect(
                "/code",
                "A deploy key exists. Tick the box to confirm replacing it; the repository stops "
                "fetching until the new key is added there.",
            )
        gitauth.generate_deploy_key(cockpit.store, cockpit.db)
    except SecretDecryptError:
        return redirect("/code", SECRETS_UNREADABLE)
    return redirect("/code", "Deploy key generated. Add its public half to the repository.")


async def pat(request: Request) -> Response:
    form = await require_csrf(request)
    try:
        gitauth.set_pat(_cockpit(request).store, str(form.get("token", "")))
    except ValueError as exc:
        return redirect("/code", str(exc))
    return redirect("/code", "Access token saved.")


async def scan_host_keys(request: Request) -> Response:
    await require_csrf(request)
    cockpit = _cockpit(request)
    target = gitauth.host_of(cockpit.db.get_setting("git.repo_url") or "")
    if target is None:
        return redirect("/code", "This repository does not use an SSH host.")
    try:
        keys = await asyncio.to_thread(gitauth.scan_host, *target)
    except GitError as exc:
        return redirect("/code", str(exc))
    gitauth.stage_host_keys(cockpit.db, keys)
    return redirect("/code", "Check the fingerprints below against the server's, then confirm.")


async def confirm_host_keys(request: Request) -> Response:
    await require_csrf(request)
    cockpit = _cockpit(request)
    keys = gitauth.confirm_host_keys(cockpit.db, cockpit.paths)
    return redirect("/code", "Host keys trusted." if keys else "No host keys are waiting for confirmation.")


ROUTES: list[BaseRoute] = [
    Route("/code", code_page),
    Route("/code/commits/{sha}", commit_page),
    Route("/code/commits/{sha}/diff", diff_fragment),
    Route("/code/setup", setup, methods=["POST"]),
    Route("/code/fetch", fetch_now, methods=["POST"]),
    Route("/code/update", update, methods=["POST"]),
    Route("/code/track", track, methods=["POST"]),
    Route("/code/checkout", checkout, methods=["POST"]),
    Route("/code/clone", clone, methods=["POST"]),
    Route("/code/ssh-key", ssh_key, methods=["POST"]),
    Route("/code/pat", pat, methods=["POST"]),
    Route("/code/host-keys/scan", scan_host_keys, methods=["POST"]),
    Route("/code/host-keys/confirm", confirm_host_keys, methods=["POST"]),
]
