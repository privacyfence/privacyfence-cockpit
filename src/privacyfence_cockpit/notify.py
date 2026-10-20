"""Webhook notifications: generic JSON, Slack and ntfy."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import httpx2

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.events import Event, EventKind
from privacyfence_cockpit.models import OUTCOME_LABELS, iso, utc_now
from privacyfence_cockpit.secrets_store import SecretStore

log = logging.getLogger(__name__)
# httpx2 logs each request URL at INFO, and the webhook URL is a secret.
logging.getLogger("httpx2").setLevel(logging.WARNING)

SOURCE = "privacyfence-cockpit"
TITLE = "PrivacyFence Cockpit"
DELIVERY_TIMEOUT_SECONDS = 10
POLL_SECONDS = 2
MAX_NAME_LENGTH = 60


class WebhookFormat(StrEnum):
    JSON = "json"
    SLACK = "slack"
    NTFY = "ntfy"


@dataclass(frozen=True)
class Webhook:
    id: int
    name: str
    format: WebhookFormat
    last_status: str | None
    last_sent_at: str | None


def message_for(event: Event) -> str:
    match event.kind:
        case EventKind.RUN_FAILED:
            label = OUTCOME_LABELS[event.outcome] if event.outcome else "failed"
            code = "no exit code" if event.exit_code is None else f"exit {event.exit_code}"
            return f"{event.recipe}: {label} ({code}) in schedule {event.schedule}"
        case EventKind.SCHEDULE_MISSED:
            return f"Schedule {event.schedule}: {event.detail}"
        case EventKind.TOKEN_EXPIRING:
            return f"Token {event.token_name} expires in {event.days_left} day(s)"
        case EventKind.TOKEN_INVALID:
            return (
                f"Token {event.token_name} was refused by PrivacyFence: "
                "it is revoked or expired, or the server URL is wrong"
            )
        case EventKind.VENV_FAILED:
            return f"The recipe environment could not be built: {event.detail}"
        case EventKind.GIT_FETCH_FAILING:
            return "Fetching the recipe repository has failed for over 24 hours"
        case _:
            return "Test notification from PrivacyFence Cockpit"


def link_for(event: Event, public_url: str | None) -> str | None:
    if public_url is None:
        return None
    match event.kind:
        case EventKind.RUN_FAILED:
            path = f"/runs/{event.run_id}"
        case EventKind.SCHEDULE_MISSED:
            path = "/schedules"
        case EventKind.TOKEN_EXPIRING | EventKind.TOKEN_INVALID:
            path = "/tokens"
        case EventKind.VENV_FAILED:
            path = "/"
        case EventKind.GIT_FETCH_FAILING:
            path = "/code"
        case _:
            path = "/notifications"
    return f"{public_url}{path}"


def build_request(
    fmt: WebhookFormat, event: Event, public_url: str | None
) -> tuple[dict[str, str], bytes]:
    message = message_for(event)
    link = link_for(event, public_url)
    if fmt is WebhookFormat.JSON:
        body = {
            "source": SOURCE,
            "event": event.kind.value,
            "message": message,
            "recipe": event.recipe,
            "schedule": event.schedule,
            "outcome": event.outcome.value if event.outcome else None,
            "exit_code": event.exit_code,
            "time": iso(event.time),
            "url": link,
        }
        return {"Content-Type": "application/json"}, json.dumps(body).encode()
    if fmt is WebhookFormat.SLACK:
        text = f"{TITLE}: {message}" + (f" <{link}|Open>" if link else "")
        return {"Content-Type": "application/json"}, json.dumps({"text": text}).encode()
    headers = {
        "Title": TITLE,
        "Tags": "white_check_mark" if event.kind is EventKind.TEST else "warning",
    }
    if link:
        headers["Click"] = link
    return headers, message.encode()


class Notifier:
    def __init__(
        self,
        db: Database,
        store: SecretStore,
        public_url: str | None,
        transport: httpx2.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._store = store
        self._public_url = public_url
        self._transport = transport
        self._clock = clock
        self._queue: queue.SimpleQueue[Event] = queue.SimpleQueue()

    def add(self, name: str, fmt: WebhookFormat, url: str) -> int:
        name = name.strip()
        if not 1 <= len(name) <= MAX_NAME_LENGTH:
            raise ValueError(f"The webhook name must be 1 to {MAX_NAME_LENGTH} characters")
        url = url.strip()
        if not url.startswith(("https://", "http://")):
            raise ValueError("The webhook URL must start with https:// or http://")
        cursor = self._db.execute(
            "INSERT INTO webhooks (name, format, created_at) VALUES (?, ?, ?)",
            (name, WebhookFormat(fmt).value, iso(self._clock())),
        )
        webhook_id = int(cursor.lastrowid or 0)
        try:
            self._store.set("webhook", str(webhook_id), url)
        except BaseException:
            self._db.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
            raise
        return webhook_id

    def delete(self, webhook_id: int) -> bool:
        cursor = self._db.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
        self._store.delete("webhook", str(webhook_id))
        return cursor.rowcount > 0

    def list(self) -> list[Webhook]:
        rows = self._db.query(
            "SELECT id, name, format, last_status, last_sent_at FROM webhooks ORDER BY id"
        )
        return [
            Webhook(
                row["id"],
                row["name"],
                WebhookFormat(row["format"]),
                row["last_status"],
                row["last_sent_at"],
            )
            for row in rows
        ]

    def emit(self, event: Event) -> None:
        self._queue.put(event)

    async def deliver_pending(self) -> int:
        events: list[Event] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if events:
            webhooks = self.list()
            if webhooks:
                async with self._client() as client:
                    for event in events:
                        for webhook in webhooks:
                            await self._send(client, webhook, event)
        return len(events)

    async def send_test(self, webhook_id: int) -> str:
        for webhook in self.list():
            if webhook.id == webhook_id:
                event = Event(EventKind.TEST, self._clock())
                async with self._client() as client:
                    return await self._send(client, webhook, event)
        raise ValueError("unknown webhook")

    async def run_forever(self) -> None:
        while True:
            await self.deliver_pending()
            await asyncio.sleep(POLL_SECONDS)

    def _client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(timeout=DELIVERY_TIMEOUT_SECONDS, transport=self._transport)

    async def _send(self, client: httpx2.AsyncClient, webhook: Webhook, event: Event) -> str:
        headers, body = build_request(webhook.format, event, self._public_url)
        try:
            url = self._store.get("webhook", str(webhook.id))
            if url is None:
                raise LookupError("no stored URL")
            response = await client.post(url, headers=headers, content=body)
            status = f"HTTP {response.status_code}"
        except Exception as exc:  # delivery must never raise; the type name carries no URL
            status = f"error: {type(exc).__name__}"
        self._db.execute(
            "UPDATE webhooks SET last_status = ?, last_sent_at = ? WHERE id = ?",
            (status, iso(self._clock()), webhook.id),
        )
        if not status.startswith("HTTP 2"):
            log.warning("webhook %s: %s", webhook.name, status)
        return status
