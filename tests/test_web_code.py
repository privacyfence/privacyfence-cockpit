import asyncio
import json
from pathlib import Path

import pytest
from cockpit_factory import make_cockpit
from gitutil import make_remote, push_commit
from webclient import logged_in

from privacyfence_cockpit import gitauth
from privacyfence_cockpit.crypto import SecretDecryptError
from privacyfence_cockpit.gitauth import HostKey
from privacyfence_cockpit.gitrepo import GitError
from privacyfence_cockpit.jobs import Job, JobKind
from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web.routes_code import deploy_key_link, display_url

FILES = {"requirements.txt": "x\n", "notes.txt": "one\n"}
KEY_B64 = "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"


def setup(tmp_path: Path, *, clone: bool = True):
    cockpit = make_cockpit(tmp_path)
    remote = make_remote(tmp_path, FILES)
    c, token = logged_in(cockpit)
    c.post(
        "/code/setup",
        data={"csrf_token": token, "url": str(remote), "branch": "main", "auth": "https"},
    )
    cockpit.db.set_setting("git.auth", "none")
    if clone:
        cockpit.git.clone()
    return cockpit, c, token, remote


def post(c, csrf: str, path: str, **data: str):
    return c.post(path, data={"csrf_token": csrf, **data}, follow_redirects=False)


def follow_flash(c, response) -> str:
    return c.get(response.headers["location"]).text


def test_setup_form_when_unconfigured(tmp_path: Path) -> None:
    c, _ = logged_in(make_cockpit(tmp_path))
    page = c.get("/code").text
    assert 'action="/code/setup"' in page and 'value="main"' in page


def test_configure_then_clone_job(tmp_path: Path) -> None:
    cockpit, c, token, _ = setup(tmp_path, clone=False)
    assert cockpit.db.get_setting("git.branch") == "main"
    page = c.get("/code").text
    assert "not cloned yet" in page
    assert "<button type=\"submit\">Clone</button>" in page
    response = post(c, token, "/code/clone")
    assert response.headers["location"] == "/code"
    assert "Started." in follow_flash(c, response)
    (job,) = cockpit.queue.queued()
    assert job.kind is JobKind.GIT_CLONE


def test_flash_says_queued_while_a_job_runs(tmp_path: Path) -> None:
    cockpit, c, token, _ = setup(tmp_path)
    cockpit.queue._running = Job(JobKind.RUN, "manual")
    response = post(c, token, "/code/update")
    assert "Queued: it starts after the current run." in follow_flash(c, response)
    assert [j.kind for j in cockpit.queue.queued()] == [JobKind.GIT_UPDATE]


def test_rejected_url_is_not_echoed(tmp_path: Path) -> None:
    c, token = logged_in(make_cockpit(tmp_path))
    secret_url = "https://user:hunter2@example.com/o/r.git"
    response = post(c, token, "/code/setup", url=secret_url, branch="main", auth="https")
    assert response.status_code == 400
    assert "Do not put credentials" in response.text
    assert "hunter2" not in response.text and "example.com" not in response.text
    assert "hunter2" not in "".join(response.headers.values())
    bad_branch = post(c, token, "/code/setup", url="https://example.com/o/r.git", branch="-x", auth="ssh")
    assert "not a valid branch" in bad_branch.text


def test_changing_the_url_of_a_cloned_repo_asks_for_a_reclone(tmp_path: Path) -> None:
    cockpit, c, token, _ = setup(tmp_path)
    (tmp_path / "other").mkdir()
    other = make_remote(tmp_path / "other", FILES)
    response = post(c, token, "/code/setup", url=str(other), branch="main", auth="none")
    assert "use Re-clone" in follow_flash(c, response)
    again = post(c, token, "/code/setup", url=str(other), branch="main", auth="none")
    assert "Saved." in follow_flash(c, again)


def test_stored_url_credentials_are_scrubbed_in_the_page(tmp_path: Path) -> None:
    cockpit, c, _, _ = setup(tmp_path, clone=False)
    cockpit.db.set_setting("git.repo_url", "https://user:hunter2@example.com/o/r.git")
    page = c.get("/code").text
    assert "hunter2" not in page and "https://***@example.com/o/r.git" in page
    assert display_url("ssh://git:pw@h/x") == "ssh://***@h/x"
    assert display_url("ssh://git@h/x") == "ssh://git@h/x"
    assert display_url("git@h:x/y") == "git@h:x/y"


def test_commit_list_marks_the_checkout_and_the_remote_tip(tmp_path: Path) -> None:
    cockpit, c, token, remote = setup(tmp_path)
    first = cockpit.git.head()
    tip = push_commit(remote, {"notes.txt": "two\n"}, "second commit")
    cockpit.git.fetch()
    page = c.get("/code").text
    assert first and tip
    assert f"/code/commits/{tip}" in page and f"/code/commits/{first}" in page
    assert "checked out" in page and "origin/main" in page
    assert "second commit" in page
    assert "1</td>" in page  # new commits
    # a checked-out commit has no checkout button; the other one has
    assert page.count('name="sha"') == 1 and f'value="{tip}"' in page


def test_commit_detail_and_escaped_diff(tmp_path: Path) -> None:
    cockpit, c, token, remote = setup(tmp_path)
    tip = push_commit(
        remote, {"notes.txt": "<script>alert(1)</script>\n", "bin.dat": b"\x00\x01\x02"}, "<b>sneaky</b>"
    )
    cockpit.git.fetch()
    detail = c.get(f"/code/commits/{tip}").text
    assert "&lt;b&gt;sneaky&lt;/b&gt;" in detail and "<b>sneaky</b>" not in detail
    assert "binary" in detail and "+1" in detail
    assert f"/code/commits/{tip}/diff?path=notes.txt" in detail
    fragment = c.get(f"/code/commits/{tip}/diff", params={"path": "notes.txt"}).text
    assert "<script>" not in fragment
    assert '<span class="add">+&lt;script&gt;alert(1)&lt;/script&gt;</span>' in fragment
    assert '<span class="del">-one</span>' in fragment
    assert "<html" not in fragment


def test_unknown_sha_and_path_are_404(tmp_path: Path) -> None:
    cockpit, c, _, _ = setup(tmp_path)
    assert c.get("/code/commits/" + "0" * 40).status_code == 404
    assert c.get("/code/commits/nonsense").status_code == 404
    head = cockpit.git.head()
    assert c.get(f"/code/commits/{head}/diff", params={"path": "nope.txt"}).status_code == 404
    assert c.get(f"/code/commits/{head}/diff").status_code == 404


def test_commit_pages_404_when_not_cloned(tmp_path: Path) -> None:
    _, c, _, _ = setup(tmp_path, clone=False)
    assert c.get("/code/commits/" + "0" * 40).status_code == 404


def test_commit_list_error_is_shown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cockpit, c, _, _ = setup(tmp_path)

    def broken(limit: int = 200):
        raise GitError("git log failed: boom")

    monkeypatch.setattr(cockpit.git, "log", broken)
    assert "git log failed: boom" in c.get("/code").text


def test_update_track_and_checkout_submit_jobs(tmp_path: Path) -> None:
    cockpit, c, token, _ = setup(tmp_path)
    head = cockpit.git.head()
    assert "Started." in follow_flash(c, post(c, token, "/code/update"))
    post(c, token, "/code/track")
    post(c, token, "/code/checkout", sha=str(head))
    kinds = [(j.kind, j.sha) for j in cockpit.queue.queued()]
    assert kinds == [(JobKind.GIT_UPDATE, None), (JobKind.GIT_TRACK, None), (JobKind.GIT_PIN, head)]
    bad = post(c, token, "/code/checkout", sha="HEAD; rm")
    assert "not a commit" in follow_flash(c, bad)
    assert len(cockpit.queue.queued()) == 3


def test_pinned_page_offers_return_to_tracking(tmp_path: Path) -> None:
    cockpit, c, token, _ = setup(tmp_path)
    assert "Return to tracking" not in c.get("/code").text
    cockpit.db.set_setting("git.pinned_sha", cockpit.git.head() or "")
    page = c.get("/code").text
    assert "Return to tracking" in page and "(pinned)" in page and "Re-clone" in page


def test_dirty_checkout_warning(tmp_path: Path) -> None:
    cockpit, c, _, _ = setup(tmp_path)
    (tmp_path / "data" / "repo" / "notes.txt").write_text("local edit\n")
    assert "changes to tracked files" in c.get("/code").text


async def _await(task: "asyncio.Task[None]") -> None:
    await task


def test_fetch_now_runs_in_a_stored_background_task(tmp_path: Path) -> None:
    cockpit, c, token, remote = setup(tmp_path)
    push_commit(remote, {"notes.txt": "two\n"}, "later")
    with c:  # keeps one event loop alive, so the background task survives the request
        response = post(c, token, "/code/fetch")
        assert "Fetching…" in follow_flash(c, response)
        task = c.app.state.code_fetch_task  # type: ignore[attr-defined]
        assert c.portal is not None
        c.portal.call(_await, task)
    assert cockpit.git.status().new_commits == 1


def test_fetch_failure_is_recorded_not_raised(tmp_path: Path) -> None:
    cockpit, c, token, remote = setup(tmp_path)
    config = tmp_path / "data" / "repo" / ".git" / "config"
    config.write_text(config.read_text().replace(str(remote), str(tmp_path / "gone.git")))
    with c:
        post(c, token, "/code/fetch")
        assert c.portal is not None
        c.portal.call(_await, c.app.state.code_fetch_task)  # type: ignore[attr-defined]
        assert "git fetch failed" in c.get("/code").text


def test_second_fetch_while_running_is_refused(tmp_path: Path) -> None:
    _, c, token, _ = setup(tmp_path)
    release = asyncio.Event()

    async def hold() -> None:
        await release.wait()

    with c:
        assert c.portal is not None

        async def start() -> "asyncio.Task[None]":
            return asyncio.create_task(hold())

        task = c.portal.call(start)
        c.app.state.code_fetch_task = task  # type: ignore[attr-defined]
        response = post(c, token, "/code/fetch")
        assert "Already fetching" in follow_flash(c, response)
        assert c.app.state.code_fetch_task is task  # type: ignore[attr-defined]


def configure_ssh(cockpit: Cockpit, c, token: str, url: str) -> None:
    cockpit.db.set_setting("git.repo_url", url)
    cockpit.db.set_setting("git.branch", "main")
    cockpit.db.set_setting("git.auth", "ssh")


def test_ssh_key_generation_and_the_confirm_rule(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    c, token = logged_in(cockpit)
    configure_ssh(cockpit, c, token, "git@github.com:acme/recipes.git")
    page = c.get("/code").text
    assert "No deploy key yet" in page and "Generate a deploy key" in page
    assert "disabled>Clone" in page
    assert "Deploy key generated" in follow_flash(c, post(c, token, "/code/ssh-key"))
    public = cockpit.db.get_setting("git.ssh_public_key")
    assert public and public.startswith("ssh-ed25519 ")
    page = c.get("/code").text
    assert public in page
    assert 'href="https://github.com/acme/recipes/settings/keys/new"' in page
    assert 'Allow write access" left off' in page
    assert "disabled>Clone" not in page  # github.com is always known
    refused = post(c, token, "/code/ssh-key")
    assert "Tick the box" in follow_flash(c, refused)
    assert cockpit.db.get_setting("git.ssh_public_key") == public
    replaced = post(c, token, "/code/ssh-key", confirm="1")
    assert "Deploy key generated" in follow_flash(c, replaced)
    assert cockpit.db.get_setting("git.ssh_public_key") != public


def test_no_deploy_key_link_for_other_hosts() -> None:
    assert deploy_key_link("ssh://git@github.com/acme/recipes") == (
        "https://github.com/acme/recipes/settings/keys/new"
    )
    assert deploy_key_link("git@gitlab.com:acme/recipes.git") is None
    assert deploy_key_link("https://github.com/acme/recipes.git") is None


def test_pat_form_never_echoes_the_value(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    c, token = logged_in(cockpit)
    c_url = "https://example.com/acme/recipes.git"
    cockpit.db.set_setting("git.repo_url", c_url)
    cockpit.db.set_setting("git.branch", "main")
    cockpit.db.set_setting("git.auth", "https")
    page = c.get("/code").text
    assert "<strong>missing</strong>" in page and 'autocomplete="off"' in page
    assert "disabled>Clone" in page
    assert 'read-only "Contents" access' in page
    bad = post(c, token, "/code/pat", token="has spaces in it")
    flash = follow_flash(c, bad)
    assert "does not look like" in flash and "has spaces" not in flash
    good = post(c, token, "/code/pat", token="ghp_supersecretvalue")
    page = follow_flash(c, good)
    assert "ghp_supersecretvalue" not in page and "ghp_supersecretvalue" not in "".join(good.headers.values())
    assert "<strong>present</strong>" in c.get("/code").text
    assert cockpit.store.get("git", "pat") == "ghp_supersecretvalue"
    assert "disabled>Clone" not in c.get("/code").text
    assert ">Clone<" in c.get("/code").text


def test_host_key_scan_lists_fingerprints_and_confirm_writes_known_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cockpit = make_cockpit(tmp_path)
    c, token = logged_in(cockpit)
    configure_ssh(cockpit, c, token, "ssh://git@git.example.com:2222/acme/recipes.git")
    cockpit.store.set("git", "ssh_key", "k")
    cockpit.db.set_setting("git.ssh_public_key", "ssh-ed25519 AAAA test")
    page = c.get("/code").text
    assert "Scan host keys" in page and "disabled>Clone" in page
    scanned: list[tuple[str, int]] = []

    def fake_scan(host: str, port: int):
        scanned.append((host, port))
        return [HostKey(host, port, "ssh-ed25519", KEY_B64)]

    monkeypatch.setattr(gitauth, "scan_host", fake_scan)
    assert "Check the fingerprints" in follow_flash(c, post(c, token, "/code/host-keys/scan"))
    assert scanned == [("git.example.com", 2222)]
    fingerprint = HostKey("h", 22, "ssh-ed25519", KEY_B64).fingerprint
    page = c.get("/code").text
    assert fingerprint in page and "ssh-ed25519" in page and "Trust these host keys" in page
    assert "disabled>Clone" in page  # still not confirmed
    assert "Host keys trusted." in follow_flash(c, post(c, token, "/code/host-keys/confirm"))
    known = (tmp_path / "data" / "home" / ".ssh" / "known_hosts").read_text()
    assert known == f"[git.example.com]:2222 ssh-ed25519 {KEY_B64}\n"
    assert gitauth.is_known(cockpit.paths, "git.example.com", 2222)
    page = c.get("/code").text
    assert "disabled>Clone" not in page and "Scan host keys" not in page
    assert "No host keys are waiting" in follow_flash(c, post(c, token, "/code/host-keys/confirm"))


def test_host_key_scan_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cockpit, c, token, _ = setup(tmp_path, clone=False)
    assert "does not use an SSH host" in follow_flash(c, post(c, token, "/code/host-keys/scan"))
    configure_ssh(cockpit, c, token, "git@git.example.com:acme/recipes.git")

    def failing(host: str, port: int):
        raise GitError(f"no SSH host keys from {host}:{port}")

    monkeypatch.setattr(gitauth, "scan_host", failing)
    assert "no SSH host keys from git.example.com:22" in follow_flash(c, post(c, token, "/code/host-keys/scan"))


def test_clone_is_refused_until_the_credential_exists(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    c, token = logged_in(cockpit)
    assert "Set the repository first" in follow_flash(c, post(c, token, "/code/clone"))
    configure_ssh(cockpit, c, token, "git@github.com:acme/recipes.git")
    assert "Add the credential" in follow_flash(c, post(c, token, "/code/clone"))
    assert cockpit.queue.queued() == []


def test_unreadable_secrets_show_a_clean_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cockpit = make_cockpit(tmp_path)
    c, token = logged_in(cockpit)
    configure_ssh(cockpit, c, token, "git@github.com:acme/recipes.git")

    def broken(*args: object, **kwargs: object):
        raise SecretDecryptError("stored secret could not be decrypted")

    monkeypatch.setattr(cockpit.store, "has", broken)
    response = c.get("/code")
    assert response.status_code == 200 and "cannot be read with this master key" in response.text
    assert "disabled>Clone" in response.text
    assert "cannot be read" in follow_flash(c, post(c, token, "/code/clone"))
    assert "cannot be read" in follow_flash(c, post(c, token, "/code/ssh-key"))


def test_auth_none_needs_no_credentials(tmp_path: Path) -> None:
    _, c, _, _ = setup(tmp_path, clone=False)
    page = c.get("/code").text
    assert "needs no credentials" in page and "disabled>Clone" not in page


def test_pending_keys_setting_roundtrip(tmp_path: Path) -> None:
    cockpit = make_cockpit(tmp_path)
    cockpit.db.set_setting(
        "git.pending_host_keys",
        json.dumps([{"host": "h", "port": 22, "key_type": "ssh-ed25519", "key_b64": KEY_B64}]),
    )
    from privacyfence_cockpit.web.routes_code import _pending_keys

    assert _pending_keys(cockpit)[0].host == "h"


def test_fetch_now_before_the_clone_says_to_clone_first(tmp_path: Path) -> None:
    cockpit, c, token, _ = setup(tmp_path, clone=False)
    assert 'action="/code/fetch"' not in c.get("/code").text
    response = post(c, token, "/code/fetch")
    assert "use Clone first" in follow_flash(c, response)
    assert getattr(c.app.state, "code_fetch_task", None) is None
    assert cockpit.db.get_setting("git.last_fetch_error") is None
