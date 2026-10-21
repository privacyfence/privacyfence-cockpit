from pathlib import Path

from cockpit_factory import make_cockpit
from webclient import logged_in

from privacyfence_cockpit.crypto import SecretDecryptError
from privacyfence_cockpit.token_check import TokenCheck, TokenState

TOKEN = "pfst_" + "a" * 30


def write_repo(root: Path) -> None:
    repo = root / "data" / "repo" / "workflows" / "10-imports"
    (repo / "10-sf").mkdir(parents=True)
    (repo / "10-sf" / "recipe.yaml").write_text("steps: []\n")


def setup(tmp_path: Path, environ: dict[str, str] | None = None):
    cockpit = make_cockpit(tmp_path, environ=environ)
    write_repo(tmp_path)
    c, token = logged_in(cockpit)
    return cockpit, c, token


def fake_check(monkeypatch, cockpit, check: TokenCheck) -> list[str]:
    seen: list[str] = []

    async def check_one(name: str) -> TokenCheck:
        seen.append(name)
        cockpit.token_checker._record(name, check)
        return check

    monkeypatch.setattr(cockpit.token_checker, "check_one", check_one)
    return seen


def test_unknown_name_shows_the_suggestion_and_stores_nothing(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    response = c.post("/tokens", data={"csrf_token": token, "name": "imports/10-sff", "value": TOKEN})
    assert response.status_code == 400
    assert "did you mean &#39;imports/10-sf&#39;" in response.text
    assert cockpit.store.names("token") == []
    assert TOKEN not in response.text


def test_a_value_without_the_prefix_is_refused_and_never_echoed(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    secret = "ghp_" + "b" * 30
    response = c.post("/tokens", data={"csrf_token": token, "name": "default", "value": secret})
    assert response.status_code == 400
    assert "Only PrivacyFence scoped tokens" in response.text
    assert secret not in response.text
    assert cockpit.store.names("token") == []


def test_set_stores_checks_and_never_echoes_the_value(tmp_path: Path, monkeypatch) -> None:
    cockpit, c, token = setup(tmp_path)
    seen = fake_check(monkeypatch, cockpit, TokenCheck(TokenState.OK, label="nightly", days_left=40))
    cockpit.token_checker._db.execute(
        "INSERT INTO notices_sent (key, sent_at) VALUES ('token:default:7', '2026-10-20T00:00:00Z')"
    )
    response = c.post(
        "/tokens", data={"csrf_token": token, "name": "default", "value": f"  {TOKEN}  "}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == "/tokens"
    assert cockpit.store.get("token", "default") == TOKEN
    assert seen == ["default"]
    assert cockpit.db.query("SELECT * FROM notices_sent WHERE key LIKE 'token:default:%'") == []
    page = c.get("/tokens")
    assert "nightly" in page.text and "40" in page.text and "chip-ok" in page.text
    assert TOKEN not in page.text and TOKEN not in response.text


def test_check_failure_after_a_decrypt_error_is_a_clean_message(tmp_path: Path, monkeypatch) -> None:
    cockpit, c, token = setup(tmp_path)

    async def boom(name: str) -> TokenCheck:
        raise SecretDecryptError("x")

    monkeypatch.setattr(cockpit.token_checker, "check_one", boom)
    response = c.post("/tokens", data={"csrf_token": token, "name": "default", "value": TOKEN}, follow_redirects=True)
    assert response.status_code == 200
    assert "cannot be decrypted" in response.text


def test_delete_removes_the_token_and_its_status(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    cockpit.store.set("token", "default", TOKEN)
    cockpit.token_checker._record("default", TokenCheck(TokenState.OK))
    response = c.post("/tokens/delete", data={"csrf_token": token, "name": "default"}, follow_redirects=True)
    assert "Deleted the token for default" in response.text
    assert cockpit.store.names("token") == []
    assert cockpit.db.query("SELECT * FROM token_status") == []


def test_check_all_runs_the_checker(tmp_path: Path, monkeypatch) -> None:
    cockpit, c, token = setup(tmp_path)
    cockpit.store.set("token", "default", TOKEN)
    cockpit.store.set("token", "imports", TOKEN)
    seen = fake_check(monkeypatch, cockpit, TokenCheck(TokenState.EXPIRING, days_left=3, error="soon"))
    response = c.post("/tokens/check", data={"csrf_token": token}, follow_redirects=True)
    assert "Checked 2 token(s)." in response.text
    assert sorted(seen) == ["default", "imports"]
    assert "expiring" in response.text and "soon" in response.text


def test_an_undecryptable_token_does_not_break_the_page_or_check_all(tmp_path: Path) -> None:
    cockpit, c, token = setup(tmp_path)
    cockpit.store.set("token", "default", TOKEN)
    next((tmp_path / "data" / "secrets" / "token").glob("*.bin")).write_bytes(b"PFC1" + b"\0" * 40)
    assert c.get("/tokens").status_code == 200
    cockpit.db.set_setting("server_url", "http://pf.example:8765/mcp")
    response = c.post("/tokens/check", data={"csrf_token": token}, follow_redirects=True)
    assert response.status_code == 200 and "cannot be decrypted" in response.text


def test_missing_names_are_listed(tmp_path: Path) -> None:
    cockpit, c, _ = setup(tmp_path)
    cockpit.store.set("token", "default", TOKEN)
    page = c.get("/tokens").text
    assert "Without a token" in page
    assert "imports/10-sf" in page and "(missing)" in page
    assert "<option value=\"imports\">" in page
    assert "not checked" in page


def test_env_override_banner(tmp_path: Path) -> None:
    _, c, _ = setup(tmp_path, {"PRIVACYFENCE_TOKEN": TOKEN})
    page = c.get("/tokens").text
    assert "overrides every stored token" in page
    assert TOKEN not in page


def test_settings_link_only_with_a_server_url(tmp_path: Path) -> None:
    cockpit, c, _ = setup(tmp_path)
    assert "Last use and last denial" not in c.get("/tokens").text
    cockpit.db.set_setting("server_url", "https://pf.example:8765/mcp")
    page = c.get("/tokens").text
    assert "Last use and last denial of each token" in page
    assert 'href="https://pf.example:8765/settings"' in page
    cockpit.db.set_setting("server_url", "not a url")
    assert "Last use and last denial" not in c.get("/tokens").text
