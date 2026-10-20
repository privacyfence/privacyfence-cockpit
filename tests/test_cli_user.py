import base64
import os
import re
from pathlib import Path

import pytest
from webclient import client, form_token

from privacyfence_cockpit.cli import main
from privacyfence_cockpit.config import EnvConfig
from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web.auth import create_session, password_is_set, set_password


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    key = tmp_path / "key"
    key.write_text(base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COCKPIT_MASTER_KEY_FILE", str(key))
    monkeypatch.delenv("PRIVACYFENCE_TOKEN", raising=False)
    monkeypatch.delenv("COCKPIT_PUBLIC_URL", raising=False)
    return tmp_path


def open_cockpit() -> Cockpit:
    return Cockpit.open(EnvConfig.from_environ(dict(os.environ)))


def test_reset_password_clears_the_hash_and_sessions_and_prints_a_working_code(
    env: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    cockpit = open_cockpit()
    set_password(cockpit.db, "an old password!")
    create_session(cockpit.db, cockpit.clock())
    create_session(cockpit.db, cockpit.clock())
    cockpit.db.close()

    assert main(["user", "reset-password"]) == 0
    out = capsys.readouterr().out
    found = re.fullmatch(
        r"Password cleared\. Setup code: ([A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4})\n"
        r"Open the cockpit/setup to set one\.\n",
        out,
    )
    assert found is not None, out
    code = found.group(1)
    assert code not in caplog.text

    cockpit = open_cockpit()
    assert not password_is_set(cockpit.db)
    assert cockpit.db.query("SELECT * FROM sessions") == []
    c = client(cockpit)
    page = c.get("/setup")
    response = c.post(
        "/setup",
        data={
            "code": code,
            "password": "a new password!",
            "confirm": "a new password!",
            "csrf_token": form_token(page.text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert password_is_set(cockpit.db)


def test_reset_password_names_the_public_url(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COCKPIT_PUBLIC_URL", "https://cockpit.example.invalid/")
    assert main(["user", "reset-password"]) == 0
    assert capsys.readouterr().out.splitlines()[1] == "Open https://cockpit.example.invalid/setup to set one."


def test_reset_password_without_a_master_key(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COCKPIT_MASTER_KEY_FILE", str(env / "missing"))
    assert main(["user", "reset-password"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip()


def test_user_needs_a_subcommand(env: Path) -> None:
    assert main(["user"]) == 1
