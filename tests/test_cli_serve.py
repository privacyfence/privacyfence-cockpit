import base64
import fcntl
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import uvicorn

from privacyfence_cockpit.cli import main
from privacyfence_cockpit.models import Mode, utc_now


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    key = tmp_path / "key"
    key.write_text(base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COCKPIT_MASTER_KEY_FILE", str(key))
    monkeypatch.delenv("PRIVACYFENCE_TOKEN", raising=False)
    return tmp_path


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> list[uvicorn.Server]:
    servers: list[uvicorn.Server] = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: servers.append(self))
    return servers


def add_running_row(data: Path) -> None:
    from privacyfence_cockpit.config import EnvConfig  # noqa: PLC0415
    from privacyfence_cockpit.services import Cockpit  # noqa: PLC0415

    cockpit = Cockpit.open(EnvConfig.from_environ(dict(os.environ)))
    cockpit.history.start(
        job_id=None,
        trigger="manual",
        recipe_path="g/r",
        mode=Mode.UNATTENDED,
        started_at=utc_now(),
        log_path=None,
        commit_sha=None,
        token_source="none",
    )
    cockpit.db.close()


def row_status(data: Path) -> str:
    from privacyfence_cockpit.db import Database  # noqa: PLC0415

    db = Database(data / "cockpit.db")
    status = str(db.query_one("SELECT status FROM runs")["status"])
    db.close()
    return status


def test_serve_runs_uvicorn_with_the_environment(
    env: Path, ran: list[uvicorn.Server], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COCKPIT_LISTEN_HOST", "127.0.0.1")
    monkeypatch.setenv("COCKPIT_LISTEN_PORT", "9123")
    assert main(["serve"]) == 0
    (server,) = ran
    assert server.config.host == "127.0.0.1"
    assert server.config.port == 9123
    assert server.config.log_config is None
    assert server.config.proxy_headers is False


def test_serve_missing_master_key_returns_2(
    env: Path, ran: list[uvicorn.Server], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = env / "nokey"
    monkeypatch.setenv("COCKPIT_MASTER_KEY_FILE", str(missing))
    assert main(["serve"]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"No master key at {missing} (COCKPIT_MASTER_KEY_FILE). Create one with\n")
    assert "openssl rand -base64 32 > cockpit_master_key" in err
    assert ran == []


def test_serve_bad_config_returns_2(
    env: Path, ran: list[uvicorn.Server], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COCKPIT_LISTEN_PORT", "nope")
    assert main(["serve"]) == 2
    assert "COCKPIT_LISTEN_PORT must be a number between 1 and 65535" in capsys.readouterr().err


def test_serve_newer_database_returns_2(
    env: Path, ran: list[uvicorn.Server], capsys: pytest.CaptureFixture[str]
) -> None:
    add_running_row(env / "data")
    import sqlite3  # noqa: PLC0415

    connection = sqlite3.connect(env / "data" / "cockpit.db")
    connection.execute("PRAGMA user_version = 9")
    connection.close()
    assert main(["serve"]) == 2
    assert "written by a newer cockpit" in capsys.readouterr().err


def test_serve_marks_orphans_when_the_lock_is_free(env: Path, ran: list[uvicorn.Server]) -> None:
    add_running_row(env / "data")
    assert main(["serve"]) == 0
    assert row_status(env / "data") == "finished"


def test_serve_leaves_rows_alone_while_another_process_holds_the_lock(env: Path, ran: list[uvicorn.Server]) -> None:
    add_running_row(env / "data")
    script = textwrap.dedent(
        """
        import fcntl, sys, time
        handle = open(sys.argv[1], "a+")
        fcntl.flock(handle, fcntl.LOCK_EX)
        print("held", flush=True)
        sys.stdin.readline()
        """
    )
    holder = subprocess.Popen(  # noqa: S603  # fixed argv
        [sys.executable, "-c", script, str(env / "data" / "run.lock")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdin is not None
        assert holder.stdout.readline().strip() == "held"
        assert main(["serve"]) == 0
        assert row_status(env / "data") == "running"
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=10)
    handle = (env / "data" / "run.lock").open("a+")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # the lock is free again
    handle.close()


def test_serve_keeps_the_httpx2_logger_quiet(
    env: Path, ran: list[uvicorn.Server], monkeypatch: pytest.MonkeyPatch
) -> None:
    import logging  # noqa: PLC0415

    monkeypatch.setenv("COCKPIT_LOG_LEVEL", "DEBUG")
    assert main(["serve"]) == 0
    assert logging.getLogger("httpx2").level == logging.WARNING
    assert logging.getLogger().level == logging.DEBUG


def test_serve_unknown_log_level_falls_back_to_info(
    env: Path, ran: list[uvicorn.Server], monkeypatch: pytest.MonkeyPatch
) -> None:
    import logging  # noqa: PLC0415

    monkeypatch.setenv("COCKPIT_LOG_LEVEL", "loud")
    assert main(["serve"]) == 0
    assert logging.getLogger().level == logging.INFO


def test_main_usage_errors_return_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    assert main(["nonsense"]) == 1
    assert main(["--bogus"]) == 1
    capsys.readouterr()


def test_main_help_returns_0(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0
    assert "serve" in capsys.readouterr().out
