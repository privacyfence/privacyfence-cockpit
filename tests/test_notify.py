import asyncio
import json
import logging
import threading
from datetime import UTC, datetime

import httpx2
import pytest

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.events import Event, EventKind
from privacyfence_cockpit.models import Outcome
from privacyfence_cockpit.notify import (
    Notifier,
    WebhookFormat,
    build_request,
    link_for,
    message_for,
)
from privacyfence_cockpit.secrets_store import SecretStore

KEY = bytes(range(32))
NOW = datetime(2026, 10, 20, 6, 15, tzinfo=UTC)
URL = "https://hooks.example.test/secret-path-12345"
PUBLIC = "https://cockpit.example.test"


class Capture:
    def __init__(self, status=200, exc=None):
        self.requests: list[httpx2.Request] = []
        self.status = status
        self.exc = exc

    def __call__(self, request):
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return httpx2.Response(self.status)


def make(tmp_path, capture=None, public=PUBLIC):
    db = Database(tmp_path / "cockpit.db")
    db.migrate()
    store = SecretStore(tmp_path / "secrets", KEY)
    capture = capture or Capture()
    notifier = Notifier(db, store, public, transport=httpx2.MockTransport(capture), clock=lambda: NOW)
    return notifier, capture, db


def event(kind=EventKind.TEST, **kwargs):
    return Event(kind, NOW, **kwargs)


def test_add_validates_name_and_url(tmp_path):
    notifier, _, _ = make(tmp_path)
    with pytest.raises(ValueError, match="name"):
        notifier.add("  ", WebhookFormat.JSON, URL)
    with pytest.raises(ValueError, match="name"):
        notifier.add("x" * 61, WebhookFormat.JSON, URL)
    with pytest.raises(ValueError, match="must start with https:// or http://"):
        notifier.add("a", WebhookFormat.JSON, "ftp://nope")
    assert notifier.list() == []
    assert notifier.add("x" * 60, WebhookFormat.JSON, "http://plain.test/x") == 1


def test_url_stored_encrypted_not_in_db(tmp_path):
    notifier, _, db = make(tmp_path)
    webhook_id = notifier.add("Ops", WebhookFormat.SLACK, URL)
    db.close()
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert URL.encode() not in path.read_bytes()
    assert notifier._store.get("webhook", str(webhook_id)) == URL


def test_add_rolls_back_when_secret_cannot_be_stored(tmp_path):
    notifier, _, _ = make(tmp_path)

    def boom(*args):
        raise OSError("disk full")

    notifier._store.set = boom  # type: ignore[method-assign]
    with pytest.raises(OSError):
        notifier.add("Ops", WebhookFormat.JSON, URL)
    assert notifier.list() == []


def test_list_and_delete(tmp_path):
    notifier, _, _ = make(tmp_path)
    first = notifier.add("One", WebhookFormat.JSON, URL)
    second = notifier.add("Two", WebhookFormat.NTFY, URL + "2")
    listed = notifier.list()
    assert [(w.id, w.name, w.format) for w in listed] == [
        (first, "One", WebhookFormat.JSON),
        (second, "Two", WebhookFormat.NTFY),
    ]
    assert listed[0].last_status is None and listed[0].last_sent_at is None
    assert notifier.delete(first) is True
    assert notifier.delete(first) is False
    assert [w.id for w in notifier.list()] == [second]
    assert not notifier._store.has("webhook", str(first))


@pytest.mark.parametrize(
    ("ev", "expected"),
    [
        (
            event(EventKind.RUN_FAILED, recipe="imports/a", schedule="nightly", outcome=Outcome.DENIED, exit_code=3),
            "imports/a: denied (exit 3) in schedule nightly",
        ),
        (
            event(EventKind.RUN_FAILED, recipe="imports/a", schedule="nightly", outcome=Outcome.TIMED_OUT),
            "imports/a: timed out (no exit code) in schedule nightly",
        ),
        (
            event(EventKind.RUN_FAILED, recipe="imports/a", schedule="nightly"),
            "imports/a: failed (no exit code) in schedule nightly",
        ),
        (
            event(EventKind.SCHEDULE_MISSED, schedule="nightly", detail="host was down"),
            "Schedule nightly: host was down",
        ),
        (event(EventKind.TOKEN_EXPIRING, token_name="default", days_left=7), "Token default expires in 7 day(s)"),
        (
            event(EventKind.TOKEN_INVALID, token_name="default"),
            "Token default was refused by PrivacyFence: it is revoked or expired, or the server URL is wrong",
        ),
        (event(EventKind.VENV_FAILED, detail="pip failed"), "The recipe environment could not be built: pip failed"),
        (event(EventKind.GIT_FETCH_FAILING), "Fetching the recipe repository has failed for over 24 hours"),
        (event(EventKind.TEST), "Test notification from PrivacyFence Cockpit"),
    ],
)
def test_message_for(ev, expected):
    assert message_for(ev) == expected


def test_every_event_kind_has_a_message_and_link():
    for kind in EventKind:
        assert message_for(event(kind))
        assert link_for(event(kind), None) is None
        assert link_for(event(kind), PUBLIC)


def test_link_for():
    assert link_for(event(EventKind.RUN_FAILED, run_id=42), PUBLIC) == f"{PUBLIC}/runs/42"
    assert link_for(event(EventKind.SCHEDULE_MISSED), PUBLIC) == f"{PUBLIC}/schedules"
    assert link_for(event(EventKind.TOKEN_EXPIRING), PUBLIC) == f"{PUBLIC}/tokens"
    assert link_for(event(EventKind.TOKEN_INVALID), PUBLIC) == f"{PUBLIC}/tokens"
    assert link_for(event(EventKind.VENV_FAILED), PUBLIC) == f"{PUBLIC}/"
    assert link_for(event(EventKind.GIT_FETCH_FAILING), PUBLIC) == f"{PUBLIC}/code"
    assert link_for(event(EventKind.TEST), PUBLIC) == f"{PUBLIC}/notifications"
    assert link_for(event(EventKind.RUN_FAILED, run_id=42), None) is None


def failed_event():
    return event(
        EventKind.RUN_FAILED,
        recipe="imports/a",
        schedule="nightly",
        outcome=Outcome.FAILED,
        exit_code=2,
        run_id=9,
    )


def test_build_request_json():
    headers, body = build_request(WebhookFormat.JSON, failed_event(), PUBLIC)
    assert headers == {"Content-Type": "application/json"}
    assert json.loads(body) == {
        "source": "privacyfence-cockpit",
        "event": "run_failed",
        "message": "imports/a: failed (exit 2) in schedule nightly",
        "recipe": "imports/a",
        "schedule": "nightly",
        "outcome": "failed",
        "exit_code": 2,
        "time": "2026-10-20T06:15:00Z",
        "url": f"{PUBLIC}/runs/9",
    }
    _, body = build_request(WebhookFormat.JSON, event(), None)
    data = json.loads(body)
    assert data["recipe"] is None and data["outcome"] is None and data["exit_code"] is None
    assert data["url"] is None


def test_build_request_slack():
    headers, body = build_request(WebhookFormat.SLACK, failed_event(), PUBLIC)
    assert headers == {"Content-Type": "application/json"}
    assert json.loads(body) == {
        "text": f"PrivacyFence Cockpit: imports/a: failed (exit 2) in schedule nightly <{PUBLIC}/runs/9|Open>"
    }
    _, body = build_request(WebhookFormat.SLACK, event(), None)
    assert json.loads(body) == {"text": "PrivacyFence Cockpit: Test notification from PrivacyFence Cockpit"}


def test_build_request_ntfy():
    headers, body = build_request(WebhookFormat.NTFY, failed_event(), PUBLIC)
    assert body == b"imports/a: failed (exit 2) in schedule nightly"
    assert headers == {"Title": "PrivacyFence Cockpit", "Tags": "warning", "Click": f"{PUBLIC}/runs/9"}
    headers, _ = build_request(WebhookFormat.NTFY, event(), None)
    assert headers == {"Title": "PrivacyFence Cockpit", "Tags": "white_check_mark"}


async def test_deliver_pending_posts_every_event_to_every_webhook(tmp_path):
    notifier, capture, _ = make(tmp_path)
    notifier.add("One", WebhookFormat.JSON, URL)
    notifier.add("Two", WebhookFormat.NTFY, "http://ntfy.test/topic")
    notifier.emit(failed_event())
    notifier.emit(event())
    assert await notifier.deliver_pending() == 2
    assert [str(r.url) for r in capture.requests] == [URL, "http://ntfy.test/topic"] * 2
    assert json.loads(capture.requests[0].content)["event"] == "run_failed"
    assert capture.requests[3].headers["Tags"] == "white_check_mark"
    assert [w.last_status for w in notifier.list()] == ["HTTP 200", "HTTP 200"]
    assert notifier.list()[0].last_sent_at == "2026-10-20T06:15:00Z"
    assert await notifier.deliver_pending() == 0


async def test_deliver_pending_without_webhooks_drops_events(tmp_path):
    notifier, capture, _ = make(tmp_path)
    notifier.emit(event())
    assert await notifier.deliver_pending() == 1
    assert capture.requests == []


async def test_server_error_and_transport_exception_do_not_raise(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)  # root level; the module must silence httpx2 itself
    notifier, capture, _ = make(tmp_path, Capture(status=500))
    notifier.add("Ops", WebhookFormat.JSON, URL)
    notifier.emit(event())
    await notifier.deliver_pending()
    assert notifier.list()[0].last_status == "HTTP 500"
    assert "webhook Ops: HTTP 500" in caplog.text

    capture.status, capture.exc = 200, httpx2.ConnectError(f"cannot reach {URL}")
    notifier.emit(event())
    await notifier.deliver_pending()
    assert notifier.list()[0].last_status == "error: ConnectError"
    assert "webhook Ops: error: ConnectError" in caplog.text
    assert URL not in caplog.text


async def test_missing_stored_url_is_an_error_status(tmp_path):
    notifier, capture, _ = make(tmp_path)
    webhook_id = notifier.add("Ops", WebhookFormat.JSON, URL)
    notifier._store.delete("webhook", str(webhook_id))
    notifier.emit(event())
    await notifier.deliver_pending()
    assert notifier.list()[0].last_status == "error: LookupError"
    assert capture.requests == []


async def test_send_test_returns_status_text(tmp_path):
    notifier, capture, _ = make(tmp_path)
    webhook_id = notifier.add("Ops", WebhookFormat.SLACK, URL)
    assert await notifier.send_test(webhook_id) == "HTTP 200"
    assert "Test notification" in json.loads(capture.requests[0].content)["text"]
    assert notifier.list()[0].last_status == "HTTP 200"
    with pytest.raises(ValueError, match="unknown webhook"):
        await notifier.send_test(webhook_id + 1)


async def test_emit_from_a_thread_then_deliver(tmp_path):
    notifier, capture, _ = make(tmp_path)
    notifier.add("Ops", WebhookFormat.JSON, URL)
    worker = threading.Thread(target=notifier.emit, args=(failed_event(),))
    worker.start()
    worker.join()
    assert await notifier.deliver_pending() == 1
    assert len(capture.requests) == 1


async def test_run_forever_delivers_until_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr("privacyfence_cockpit.notify.POLL_SECONDS", 0.01)
    notifier, capture, _ = make(tmp_path)
    notifier.add("Ops", WebhookFormat.JSON, URL)
    task = asyncio.create_task(notifier.run_forever())
    notifier.emit(event())
    for _ in range(200):
        if capture.requests:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(capture.requests) == 1
