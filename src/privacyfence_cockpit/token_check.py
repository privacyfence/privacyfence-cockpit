"""Token checks: ask PrivacyFence for the token's status over MCP and raise expiry notices."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

from privacyfence_cockpit.db import Database
from privacyfence_cockpit.effective import Effective
from privacyfence_cockpit.events import Event, EventKind, EventSink
from privacyfence_cockpit.models import iso
from privacyfence_cockpit.secrets_store import SecretStore
from privacyfence_cockpit.tokens import TokenResolver

STATUS_REASON = "privacyfence-cockpit token check"
THRESHOLDS = (1, 7, 14)
REFUSED_ERROR = "refused by PrivacyFence: the token is revoked or expired, or the server URL is wrong"
NOT_SCOPED_ERROR = "not a scoped token; v1 supports PrivacyFence scoped tokens only"


class TokenState(StrEnum):
    OK = "ok"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    REFUSED = "refused"
    NOT_SCOPED = "not_scoped"
    UNREACHABLE = "unreachable"
    NO_SERVER = "no_server"


@dataclass(frozen=True)
class TokenCheck:
    state: TokenState
    label: str | None = None
    expires_at: str | None = None
    days_left: int | None = None
    error: str | None = None


def _first_leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


async def _call_status(server_url: str, http_client: httpx2.AsyncClient) -> dict[str, Any]:
    async with streamable_http_client(server_url, http_client=http_client) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            result = await session.call_tool("privacyfence_status", {"reason": STATUS_REASON})
    if result.is_error:
        raise MCPError(-32000, "privacyfence_status returned an error")
    structured = result.structured_content
    if isinstance(structured, dict):
        return structured
    for block in result.content:
        parsed = json.loads(block.text) if isinstance(block, TextContent) else None
        if isinstance(parsed, dict):
            return parsed
    return {}


async def fetch_status(
    server_url: str,
    token: str,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        transport=transport,
        timeout=httpx2.Timeout(timeout),
    ) as http_client:
        try:
            return await _call_status(server_url, http_client)
        except BaseExceptionGroup as group:
            raise _first_leaf(group) from None


def classify(status: dict[str, Any]) -> TokenCheck:
    token = status.get("token")
    if not isinstance(token, dict):
        return TokenCheck(TokenState.NOT_SCOPED, error=NOT_SCOPED_ERROR)
    days_left = token.get("days_left")
    if not isinstance(days_left, int) or isinstance(days_left, bool):
        days_left = None
    label = token.get("label")
    expires_at = token.get("expires_at")
    if days_left == 0:
        state = TokenState.EXPIRED
    elif token.get("expiring_soon"):
        state = TokenState.EXPIRING
    else:
        state = TokenState.OK
    return TokenCheck(
        state,
        label=label if isinstance(label, str) else None,
        expires_at=expires_at if isinstance(expires_at, str) else None,
        days_left=days_left,
    )


FetchStatus = Callable[..., Awaitable[dict[str, Any]]]


class TokenChecker:
    def __init__(
        self,
        db: Database,
        resolver: TokenResolver,
        store: SecretStore,
        effective: Callable[[], Effective],
        events: EventSink,
        clock: Callable[[], datetime],
        fetch: FetchStatus = fetch_status,
    ) -> None:
        self._db = db
        self._resolver = resolver
        self._store = store
        self._effective = effective
        self._events = events
        self._clock = clock
        self._fetch = fetch

    async def check_one(self, name: str) -> TokenCheck:
        server_url = self._effective().server_url
        if not server_url:
            result = TokenCheck(TokenState.NO_SERVER)
        else:
            result = await self._query(name, server_url)
        self._record(name, result)
        return result

    async def _query(self, name: str, server_url: str) -> TokenCheck:
        token = self._resolver.resolve_name(name)
        if token is None:
            return TokenCheck(TokenState.REFUSED, error=REFUSED_ERROR)
        try:
            status = await self._fetch(server_url, token)
        except MCPError:
            return TokenCheck(TokenState.REFUSED, error=REFUSED_ERROR)
        except (httpx2.HTTPError, OSError):
            return TokenCheck(TokenState.UNREACHABLE, error=f"cannot reach {server_url}")
        return classify(status)

    async def check_all(self) -> dict[str, TokenCheck]:
        results = {name: await self.check_one(name) for name in self._store.names("token")}
        self._db.set_setting("tokens.last_check_at", iso(self._clock()))
        return results

    def forget(self, name: str) -> None:
        self._db.execute("DELETE FROM token_status WHERE name = ?", (name,))
        pattern = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        self._db.execute("DELETE FROM notices_sent WHERE key LIKE ? ESCAPE '\\'", (f"token:{pattern}:%",))

    def _record(self, name: str, result: TokenCheck) -> None:
        now = self._clock()
        self._db.execute(
            "INSERT INTO token_status (name, checked_at, state, label, expires_at, days_left, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET checked_at = excluded.checked_at, "
            "state = excluded.state, label = excluded.label, expires_at = excluded.expires_at, "
            "days_left = excluded.days_left, error = excluded.error",
            (name, iso(now), result.state.value, result.label, result.expires_at, result.days_left, result.error),
        )
        invalid_key = f"token:{name}:invalid"
        if result.state in (TokenState.REFUSED, TokenState.EXPIRED):
            if self._mark(invalid_key, now):
                self._events.emit(
                    Event(
                        kind=EventKind.TOKEN_INVALID,
                        time=now,
                        token_name=name,
                        days_left=result.days_left,
                        detail=result.error,
                    )
                )
        elif result.state in (TokenState.OK, TokenState.EXPIRING):
            self._db.execute("DELETE FROM notices_sent WHERE key = ?", (invalid_key,))
            self._notify_expiring(name, result, now)

    def _notify_expiring(self, name: str, result: TokenCheck, now: datetime) -> None:
        if result.days_left is None:
            return
        threshold = next((t for t in THRESHOLDS if result.days_left <= t), None)
        if threshold is None or not self._mark(f"token:{name}:{threshold}", now):
            return
        self._events.emit(
            Event(
                kind=EventKind.TOKEN_EXPIRING,
                time=now,
                token_name=name,
                days_left=result.days_left,
                detail=result.label,
            )
        )

    def _mark(self, key: str, now: datetime) -> bool:
        """Record a notice; True when it was not sent before."""
        cursor = self._db.execute("INSERT OR IGNORE INTO notices_sent (key, sent_at) VALUES (?, ?)", (key, iso(now)))
        return cursor.rowcount == 1
