import json
from pathlib import Path

import httpx2
from cockpit_factory import make_cockpit
from webclient import logged_in

from privacyfence_cockpit.notify import Notifier

SECRET_URL = "https://hooks.example.com/services/T000/B000/s3cr3t-token"


def setup(tmp_path: Path, status: int = 200):
    cockpit = make_cockpit(tmp_path)
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(status)

    cockpit.notifier = Notifier(
        cockpit.db, cockpit.store, None, transport=httpx2.MockTransport(handler), clock=cockpit.clock
    )
    c, token = logged_in(cockpit)
    return cockpit, c, token, requests


def add(c, token: str, **fields: str):
    data = {"csrf_token": token, "name": "Ops", "format": "slack", "url": SECRET_URL, **fields}
    return c.post("/notifications", data=data, follow_redirects=False)


def test_page_lists_events_and_an_empty_state(tmp_path: Path) -> None:
    _, c, _, _ = setup(tmp_path)
    page = c.get("/notifications")
    assert page.status_code == 200
    assert "No webhooks yet" in page.text
    assert 'type="url"' in page.text
    assert "scheduled run ends as" in page.text
    assert "Fetching the recipe repository has failed for over 24 hours" in page.text
    assert "Test notification" not in page.text


def test_add_lists_name_and_format_but_never_the_url(tmp_path: Path) -> None:
    cockpit, c, token, _ = setup(tmp_path)
    response = add(c, token)
    assert response.status_code == 303
    assert SECRET_URL not in response.text
    page = c.get("/notifications")
    assert "Added the webhook Ops." in page.text
    assert "Ops" in page.text and "slack" in page.text
    assert "hooks.example.com" not in page.text and "s3cr3t" not in page.text
    assert [w.name for w in cockpit.notifier.list()] == ["Ops"]
    assert b"s3cr3t" not in (tmp_path / "data" / "cockpit.db").read_bytes()


def test_invalid_url_shows_the_message_and_stores_nothing(tmp_path: Path) -> None:
    cockpit, c, token, _ = setup(tmp_path)
    response = add(c, token, url="ftp://s3cr3t.example.com/x")
    assert response.status_code == 400
    assert "The webhook URL must start with https:// or http://" in response.text
    assert "s3cr3t" not in response.text
    assert cockpit.notifier.list() == []


def test_invalid_name_and_format_are_refused(tmp_path: Path) -> None:
    cockpit, c, token, _ = setup(tmp_path)
    assert "1 to 60 characters" in add(c, token, name=" ").text
    bad = add(c, token, format="carrier-pigeon")
    assert bad.status_code == 400 and "Pick a webhook format." in bad.text
    assert cockpit.notifier.list() == []


def test_delete(tmp_path: Path) -> None:
    cockpit, c, token, _ = setup(tmp_path)
    add(c, token)
    hook_id = cockpit.notifier.list()[0].id
    response = c.post(f"/notifications/{hook_id}/delete", data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert cockpit.notifier.list() == []
    assert cockpit.store.get("webhook", str(hook_id)) is None
    again = c.post(f"/notifications/{hook_id}/delete", data={"csrf_token": token})
    assert again.status_code == 404


def test_send_test_shows_the_status_text(tmp_path: Path) -> None:
    cockpit, c, token, requests = setup(tmp_path)
    add(c, token, format="json")
    hook_id = cockpit.notifier.list()[0].id
    response = c.post(f"/notifications/{hook_id}/test", data={"csrf_token": token}, follow_redirects=True)
    assert "HTTP 200" in response.text
    assert len(requests) == 1 and str(requests[0].url) == SECRET_URL
    assert json.loads(requests[0].content)["event"] == "test"
    assert SECRET_URL not in response.text
    assert "HTTP 200" in c.get("/notifications").text


def test_send_test_failure_status_is_a_flash_not_an_error(tmp_path: Path) -> None:
    cockpit, c, token, _ = setup(tmp_path, status=500)
    add(c, token)
    hook_id = cockpit.notifier.list()[0].id
    response = c.post(f"/notifications/{hook_id}/test", data={"csrf_token": token}, follow_redirects=True)
    assert response.status_code == 200 and "HTTP 500" in response.text


def test_unknown_webhook_is_a_404_not_a_500(tmp_path: Path) -> None:
    _, c, token, _ = setup(tmp_path)
    assert c.post("/notifications/99/test", data={"csrf_token": token}).status_code == 404
    assert c.post("/notifications/abc/test", data={"csrf_token": token}).status_code == 404
