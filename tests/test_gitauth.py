import re
import shlex
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

from privacyfence_cockpit import gitauth
from privacyfence_cockpit.db import Database
from privacyfence_cockpit.gitauth import (
    GITHUB_KNOWN_HOSTS,
    HostKey,
    confirm_host_keys,
    credentials_factory,
    generate_deploy_key,
    host_of,
    is_known,
    scan_host,
    set_pat,
    stage_host_keys,
    write_builtin_known_hosts,
)
from privacyfence_cockpit.gitrepo import GitError
from privacyfence_cockpit.paths import DataPaths
from privacyfence_cockpit.secrets_store import SecretStore

KEY = bytes(range(32))
PAT = "github_pat_SECRETVALUE123"
ED = "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"


@pytest.fixture
def paths(tmp_path):
    return DataPaths(tmp_path / "data").ensure()


@pytest.fixture
def db(paths):
    database = Database(paths.db_file)
    database.migrate()
    return database


@pytest.fixture
def store(paths):
    return SecretStore(paths.secrets, KEY)


def test_github_fingerprints():
    keys = [HostKey("github.com", 22, *line.split()[1:3]) for line in GITHUB_KNOWN_HOSTS.splitlines()]
    assert [key.fingerprint for key in keys] == [
        "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU",
        "SHA256:p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM",
        "SHA256:uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s",
    ]


def test_known_hosts_line_forms():
    assert HostKey("h.example", 22, "ssh-ed25519", "AAAA").known_hosts_line == "h.example ssh-ed25519 AAAA"
    assert HostKey("h.example", 2222, "ssh-ed25519", "AAAA").known_hosts_line == "[h.example]:2222 ssh-ed25519 AAAA"


def test_generate_deploy_key(store, db, paths):
    public = generate_deploy_key(store, db)
    assert public.startswith("ssh-ed25519 ")
    assert public.endswith(" privacyfence-cockpit")
    assert db.get_setting("git.ssh_public_key") == public
    private = store.get("git", "ssh_key")
    assert private is not None and "BEGIN OPENSSH PRIVATE KEY" in private
    for path in paths.root.rglob("*"):
        if path.is_file() and path.parent != paths.secrets / "git":
            assert b"OPENSSH PRIVATE KEY" not in path.read_bytes()
    assert generate_deploy_key(store, db) != public


def test_set_pat(store):
    set_pat(store, f"  {PAT}\n")
    assert store.get("git", "pat") == PAT
    for bad in ("", "   ", "two words"):
        with pytest.raises(ValueError, match="does not look like a GitHub token"):
            set_pat(store, bad)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:org/repo.git", ("github.com", 22)),
        ("ssh://git@git.example.org:2222/org/repo.git", ("git.example.org", 2222)),
        ("ssh://git.example.org/org/repo.git", ("git.example.org", 22)),
        ("ssh://git@host:notaport/repo", None),
        ("ssh:///repo", None),
        ("https://github.com/org/repo.git", None),
        ("file:///srv/repo.git", None),
        ("/srv/repo.git", None),
    ],
)
def test_host_of(url, expected):
    assert host_of(url) == expected


def test_builtin_known_hosts_written_every_start(paths):
    write_builtin_known_hosts(paths)
    path = paths.home / ".ssh" / "known_hosts_github"
    assert path.read_text() == GITHUB_KNOWN_HOSTS
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("tampered")
    write_builtin_known_hosts(paths)
    assert path.read_text() == GITHUB_KNOWN_HOSTS


def test_scan_host_parses_output():
    seen = {}

    def run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        out = f"# host:22 SSH-2.0-x\nhost.example ssh-ed25519 {ED}\nhost.example ssh-rsa AAAAB3\n"
        return subprocess.CompletedProcess(argv, 0, out, "")

    keys = scan_host("host.example", 2222, run)
    assert keys == [
        HostKey("host.example", 2222, "ssh-ed25519", ED),
        HostKey("host.example", 2222, "ssh-rsa", "AAAAB3"),
    ]
    assert seen["argv"] == ["ssh-keyscan", "-T", "10", "-p", "2222", "-t", "ed25519,ecdsa,rsa", "host.example"]
    assert seen["kwargs"]["timeout"] == 30
    assert "GIT_TERMINAL_PROMPT" in seen["kwargs"]["env"]


def test_scan_host_errors():
    def empty(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "# only comments\n", "")

    with pytest.raises(GitError, match=r"no SSH host keys from host\.example:22"):
        scan_host("host.example", 22, empty)
    for bad in ("-oProxyCommand=x", "a b", "", ".x"):
        with pytest.raises(GitError, match="not a valid host name"):
            scan_host(bad, 22, empty)

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 30)

    with pytest.raises(GitError, match="no SSH host keys"):
        scan_host("host.example", 22, timeout)

    def missing(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    with pytest.raises(GitError, match="ssh-keyscan could not be run"):
        scan_host("host.example", 22, missing)


def test_scan_host_default_runner_is_subprocess_run():
    assert scan_host.__defaults__ == (subprocess.run,)


def test_stage_and_confirm_host_keys(db, paths):
    assert confirm_host_keys(db, paths) == []
    assert not (paths.home / ".ssh" / "known_hosts").exists()
    keys = [HostKey("a.example", 22, "ssh-ed25519", ED), HostKey("b.example", 2222, "ssh-ed25519", ED)]
    stage_host_keys(db, keys)
    assert db.get_setting("git.pending_host_keys") is not None
    assert not is_known(paths, "a.example", 22)
    assert confirm_host_keys(db, paths) == keys
    known = paths.home / ".ssh" / "known_hosts"
    assert known.read_text().splitlines() == [f"a.example ssh-ed25519 {ED}", f"[b.example]:2222 ssh-ed25519 {ED}"]
    assert known.stat().st_mode & 0o777 == 0o600
    assert db.get_setting("git.pending_host_keys") is None
    assert is_known(paths, "a.example", 22)
    assert is_known(paths, "b.example", 2222)
    assert not is_known(paths, "b.example", 22)
    assert not is_known(paths, "a.example", 2222)
    stage_host_keys(db, [HostKey("c.example", 22, "ssh-ed25519", ED)])
    confirm_host_keys(db, paths)
    assert len(known.read_text().splitlines()) == 3


def test_is_known_github_and_file_forms(paths):
    assert is_known(paths, "github.com", 22)
    assert not is_known(paths, "github.com", 2222)
    assert not is_known(paths, "other.example", 22)
    (paths.home / ".ssh" / "known_hosts").write_text("# c\n\nx.example,10.0.0.1 ssh-ed25519 AAAA\n")
    assert is_known(paths, "10.0.0.1", 22)
    assert is_known(paths, "x.example", 22)


def test_ssh_credentials(db, store, paths):
    db.set_setting("git.auth", "ssh")
    factory = credentials_factory(db, store, paths)
    with pytest.raises(GitError, match="No deploy key: generate one on the Code page."):
        with factory():
            pass
    generate_deploy_key(store, db)
    private = store.get("git", "ssh_key")
    with factory() as env:
        assert set(env) == {"GIT_SSH_COMMAND"}
        argv = shlex.split(env["GIT_SSH_COMMAND"])
        key = Path(argv[argv.index("-i") + 1])
        assert key.is_file()
        assert key.stat().st_mode & 0o777 == 0o600
        assert key.read_text().startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert argv[0] == "ssh"
        for option in ("IdentitiesOnly=yes", "BatchMode=yes", "StrictHostKeyChecking=yes"):
            assert option in argv
        ssh_dir = paths.home / ".ssh"
        assert f"UserKnownHostsFile={ssh_dir}/known_hosts_github {ssh_dir}/known_hosts" in argv
        assert private is not None and private.strip() not in "".join(env.values())
        folder = key.parent
    assert not key.exists() and not folder.exists()


def test_ssh_key_file_matches_public_key(db, store, paths):
    db.set_setting("git.auth", "ssh")
    public = generate_deploy_key(store, db)
    with credentials_factory(db, store, paths)() as env:
        key = Path(shlex.split(env["GIT_SSH_COMMAND"])[2])
        loaded = serialization.load_ssh_private_key(key.read_bytes(), password=None)
    derived = loaded.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    assert derived.decode() == public.rsplit(" ", 1)[0]


def test_https_credentials_with_real_git(db, store, paths):
    db.set_setting("git.auth", "https")
    db.set_setting("git.repo_url", "https://github.com/org/repo.git")
    factory = credentials_factory(db, store, paths)
    with pytest.raises(GitError, match="No access token: add one on the Code page."):
        with factory():
            pass
    set_pat(store, PAT)
    git_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(paths.home),
        "LANG": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    with factory() as env:
        assert env["GIT_CONFIG_COUNT"] == "2"
        assert env["GIT_CONFIG_VALUE_0"] == ""
        assert PAT not in "".join(env.values())
        secret = Path(re.search(r"cat (\S+?)\)", env["GIT_CONFIG_VALUE_1"]).group(1).strip("'\""))
        assert secret.read_text() == PAT
        assert secret.stat().st_mode & 0o777 == 0o600
        result = subprocess.run(  # noqa: S603, S607
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            env={**git_env, **env},
            capture_output=True,
            text=True,
            check=False,
        )
        folder = secret.parent
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "username=x-access-token" in lines
    assert f"password={PAT}" in lines
    assert not secret.exists() and not folder.exists()


def test_https_helper_ignores_store_and_erase(db, store, paths):
    db.set_setting("git.auth", "https")
    db.set_setting("git.repo_url", "https://github.com/org/repo.git")
    set_pat(store, PAT)
    with credentials_factory(db, store, paths)() as env:
        out = subprocess.run(  # noqa: S603, S607
            ["git", "credential", "reject"],
            input="protocol=https\nhost=github.com\nusername=x\npassword=y\n\n",
            env={"PATH": "/usr/bin:/bin", "HOME": str(paths.home), "GIT_TERMINAL_PROMPT": "0", **env},
            capture_output=True,
            text=True,
            check=False,
        )
    assert out.returncode == 0 and out.stdout == ""


@pytest.mark.parametrize("auth", [None, "none"])
def test_no_credentials_for_other_modes(db, store, paths, auth):
    if auth:
        db.set_setting("git.auth", auth)
    with credentials_factory(db, store, paths)() as env:
        assert env == {}


def test_temp_folder_removed_when_body_raises(db, store, paths):
    db.set_setting("git.auth", "https")
    db.set_setting("git.repo_url", "https://github.com/org/repo.git")
    set_pat(store, PAT)
    seen = []
    with pytest.raises(RuntimeError):
        with credentials_factory(db, store, paths)() as env:
            seen.append(re.search(r"cat (\S+?)\)", env["GIT_CONFIG_VALUE_1"]).group(1).strip("'\""))
            raise RuntimeError("boom")
    assert not Path(seen[0]).exists()
    assert gitauth.GITHUB_HOST == "github.com"


@pytest.mark.parametrize(
    ("host", "expected"),
    [("github.com", True), ("evil.example", False), ("github.com.evil.example", False), ("redirect.example", False)],
)
def test_https_helper_only_answers_for_the_configured_host(db, store, paths, host, expected):
    db.set_setting("git.auth", "https")
    db.set_setting("git.repo_url", "https://github.com/org/repo.git")
    set_pat(store, PAT)
    with credentials_factory(db, store, paths)() as env:
        result = subprocess.run(  # noqa: S603, S607
            ["git", "credential", "fill"],
            input=f"protocol=https\nhost={host}\n\n",
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(paths.home),
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                **env,
            },
            capture_output=True,
            text=True,
            check=False,
        )
    assert (f"password={PAT}" in result.stdout.splitlines()) is expected
    assert expected or PAT not in result.stdout + result.stderr


@pytest.mark.parametrize("url", [None, "ssh://git@github.com/org/repo.git", "/srv/repo", "https://:x"])
def test_https_credentials_need_an_https_repository(db, store, paths, url):
    db.set_setting("git.auth", "https")
    if url:
        db.set_setting("git.repo_url", url)
    set_pat(store, PAT)
    with pytest.raises(GitError, match="https:// repository URL"):
        with credentials_factory(db, store, paths)():
            pass
