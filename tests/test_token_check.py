from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse

from privacyfence_cockpit.crypto import SecretDecryptError
from privacyfence_cockpit.db import Database
from privacyfence_cockpit.effective import Effective
from privacyfence_cockpit.events import EventKind, ListSink
from privacyfence_cockpit.secrets_store import SecretStore
from privacyfence_cockpit.token_check import (
    NOT_SCOPED_ERROR,
    REFUSED_ERROR,
    STATUS_REASON,
    UNDECRYPTABLE_ERROR,
    TokenCheck,
    TokenChecker,
    TokenState,
    classify,
    fetch_status,
)
from privacyfence_cockpit.tokens import TokenResolver

URL = "http://127.0.0.1/mcp"
TOKEN = "pfst_" + "x" * 30


@asynccontextmanager
async def serving(status):
    server = MCPServer("fake-privacyfence")
    reasons = []

    @server.tool()
    def privacyfence_status(reason: str) -> dict[str, Any]:
        reasons.append(reason)
        return status

    app = server.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    async with app.router.lifespan_context(app):
        yield httpx2.ASGITransport(app=app), reasons


async def test_fetch_status_returns_the_tool_result():
    status = {"token": {"label": "nightly", "days_left": 40, "expiring_soon": False}}
    async with serving(status) as (transport, reasons):
        assert await fetch_status(URL, TOKEN, transport=transport) == status
    assert reasons == [STATUS_REASON]


@pytest.mark.parametrize(
    ("token", "state", "days_left"),
    [
        ({"label": "a", "expires_at": "2026-12-01T00:00:00Z", "days_left": 40, "expiring_soon": False}, "ok", 40),
        ({"label": "a", "days_left": 5, "expiring_soon": True}, "expiring", 5),
        ({"label": "a", "days_left": 0, "expiring_soon": True}, "expired", 0),
        ({"label": 3, "days_left": True}, "ok", None),
    ],
)
async def test_classify_over_mcp(token, state, days_left):
    async with serving({"token": token}) as (transport, _):
        status = await fetch_status(URL, TOKEN, transport=transport)
    check = classify(status)
    assert check.state == state
    assert check.days_left == days_left


def test_classify_without_a_token_key_is_not_scoped():
    assert classify({"connectors": []}) == TokenCheck(TokenState.NOT_SCOPED, error=NOT_SCOPED_ERROR)
    assert classify({"token": "pfst"}).state == TokenState.NOT_SCOPED


def test_classify_keeps_label_and_expiry():
    check = classify({"token": {"label": "n", "expires_at": "2026-12-01", "days_left": 9, "expiring_soon": True}})
    assert (check.label, check.expires_at) == ("n", "2026-12-01")


async def test_a_401_is_a_refusal_not_a_group():
    from mcp.shared.exceptions import MCPError

    async def app(scope, receive, send):
        await PlainTextResponse("no", status_code=401)(scope, receive, send)

    with pytest.raises(MCPError):
        await fetch_status(URL, TOKEN, transport=httpx2.ASGITransport(app=app))


async def test_a_connect_error_surfaces_as_http_error():
    class Broken(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx2.ConnectError("boom", request=request)

    with pytest.raises(httpx2.HTTPError):
        await fetch_status(URL, TOKEN, transport=Broken())


async def test_a_tool_error_is_raised():
    from mcp.shared.exceptions import MCPError

    server = MCPServer("fake")

    @server.tool()
    def privacyfence_status(reason: str) -> dict:
        raise RuntimeError("nope")

    app = server.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    async with app.router.lifespan_context(app):
        with pytest.raises(MCPError):
            await fetch_status(URL, TOKEN, transport=httpx2.ASGITransport(app=app))


async def test_text_content_fallback(monkeypatch):
    server = MCPServer("fake")

    @server.tool(structured_output=False)
    def privacyfence_status(reason: str) -> str:
        return '{"token": {"days_left": 3, "expiring_soon": true}}'

    app = server.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    async with app.router.lifespan_context(app):
        status = await fetch_status(URL, TOKEN, transport=httpx2.ASGITransport(app=app))
    assert classify(status).state == TokenState.EXPIRING


async def test_non_object_text_content_is_empty():
    server = MCPServer("fake")

    @server.tool(structured_output=False)
    def privacyfence_status(reason: str) -> str:
        return "[1]"

    app = server.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    async with app.router.lifespan_context(app):
        assert await fetch_status(URL, TOKEN, transport=httpx2.ASGITransport(app=app)) == {}


class Harness:
    def __init__(self, tmp_path, server_url=URL):
        self.db = Database(tmp_path / "c.db")
        self.db.migrate()
        self.store = SecretStore(tmp_path / "secrets", b"k" * 32)
        self.events = ListSink()
        self.now = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
        self.server_url = server_url
        self.queue = []
        self.fetched = []
        self.checker = TokenChecker(
            self.db,
            TokenResolver(self.store, None),
            self.store,
            self.effective,
            self.events,
            lambda: self.now,
            self.fetch,
        )

    def effective(self):
        return Effective(self.server_url, "x", "UTC", "default", 30, 90, 15, False)

    async def fetch(self, server_url, token):
        self.fetched.append((server_url, token))
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def status(self, days_left, expiring=True):
        return {"token": {"label": "n", "days_left": days_left, "expiring_soon": expiring}}

    def kinds(self):
        return [e.kind for e in self.events.events]

    def keys(self):
        return sorted(r["key"] for r in self.db.query("SELECT key FROM notices_sent"))


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    harness.store.set("token", "default", TOKEN)
    return harness


async def test_no_server(tmp_path):
    harness = Harness(tmp_path, server_url=None)
    harness.store.set("token", "default", TOKEN)
    assert (await harness.checker.check_one("default")).state == TokenState.NO_SERVER
    assert harness.fetched == []
    assert harness.db.query_one("SELECT state FROM token_status")["state"] == "no_server"


async def test_refused_twice_emits_one_invalid_and_ok_clears_it(h):
    from mcp.shared.exceptions import MCPError

    h.queue = [MCPError(-1, "401"), MCPError(-1, "401"), h.status(60, expiring=False)]
    first = await h.checker.check_one("default")
    assert first == TokenCheck(TokenState.REFUSED, error=REFUSED_ERROR)
    await h.checker.check_one("default")
    assert h.kinds() == [EventKind.TOKEN_INVALID]
    assert h.events.events[0].token_name == "default"
    assert h.keys() == ["token:default:invalid"]
    assert (await h.checker.check_one("default")).state == TokenState.OK
    assert h.keys() == []


async def test_expired_is_invalid(h):
    h.queue = [h.status(0)]
    assert (await h.checker.check_one("default")).state == TokenState.EXPIRED
    assert h.kinds() == [EventKind.TOKEN_INVALID]


async def test_unreachable_stores_no_exception_text(h):
    h.queue = [httpx2.ConnectError("Bearer " + TOKEN), OSError("secret")]
    first = await h.checker.check_one("default")
    assert first == TokenCheck(TokenState.UNREACHABLE, error=f"cannot reach {URL}")
    assert (await h.checker.check_one("default")).state == TokenState.UNREACHABLE
    assert TOKEN not in str(h.db.query("SELECT * FROM token_status")[0]["error"])
    assert h.kinds() == []


async def test_not_scoped(h):
    h.queue = [{"connectors": []}]
    assert (await h.checker.check_one("default")).state == TokenState.NOT_SCOPED


async def test_missing_token_value_is_refused(h):
    assert (await h.checker.check_one("gone")).state == TokenState.REFUSED
    assert h.fetched == []


async def test_expiry_notices_per_threshold(h):
    h.queue = [h.status(13), h.status(13), h.status(6), h.status(6), h.status(1)]
    await h.checker.check_one("default")
    assert [(e.kind, e.days_left) for e in h.events.events] == [(EventKind.TOKEN_EXPIRING, 13)]
    assert h.keys() == ["token:default:14"]
    await h.checker.check_one("default")
    assert len(h.events.events) == 1
    await h.checker.check_one("default")
    assert h.keys() == ["token:default:14", "token:default:7"]
    assert h.events.events[-1].days_left == 6
    await h.checker.check_one("default")
    assert len(h.events.events) == 2
    await h.checker.check_one("default")
    assert h.keys() == ["token:default:1", "token:default:14", "token:default:7"]


async def test_far_expiry_and_unknown_days_emit_nothing(h):
    h.queue = [h.status(30, expiring=False), {"token": {"label": "n"}}]
    await h.checker.check_one("default")
    await h.checker.check_one("default")
    assert h.events.events == []


async def test_forget_deletes_row_and_notices(h):
    h.store.set("token", "a_b", TOKEN)
    h.queue = [h.status(13), h.status(13)]
    await h.checker.check_one("default")
    await h.checker.check_one("a_b")
    h.db.execute("INSERT INTO notices_sent (key, sent_at) VALUES ('token:axb:14', 'x')")
    h.checker.forget("a_b")
    assert h.keys() == ["token:axb:14", "token:default:14"]
    assert [r["name"] for r in h.db.query("SELECT name FROM token_status")] == ["default"]


async def test_check_all_sets_last_check(h):
    h.store.set("token", "other", TOKEN)
    h.queue = [h.status(60, expiring=False), h.status(60, expiring=False)]
    results = await h.checker.check_all()
    assert sorted(results) == ["default", "other"]
    assert h.db.get_setting("tokens.last_check_at") == "2026-10-20T06:00:00Z"
    h.now += timedelta(hours=1)
    h.queue = []
    h.store.delete("token", "default")
    h.store.delete("token", "other")
    await h.checker.check_all()
    assert h.db.get_setting("tokens.last_check_at") == "2026-10-20T07:00:00Z"


async def test_text_that_is_not_json_is_not_scoped():
    server = MCPServer("fake")

    @server.tool(structured_output=False)
    def privacyfence_status(reason: str) -> str:
        return "service unavailable"

    app = server.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    async with app.router.lifespan_context(app):
        status = await fetch_status(URL, TOKEN, transport=httpx2.ASGITransport(app=app))
    assert classify(status).state == TokenState.NOT_SCOPED


async def test_check_all_survives_an_undecryptable_token(h):
    h.store.set("token", "bad", TOKEN)
    h.store.set("token", "default", TOKEN)
    h.store._file("token", "bad").write_bytes(b"garbage")
    h.queue = [h.status(60, expiring=False)]
    with pytest.raises(SecretDecryptError):
        await h.checker.check_one("bad")
    results = await h.checker.check_all()
    assert results["bad"].state == TokenState.REFUSED
    assert results["bad"].error == UNDECRYPTABLE_ERROR
    assert TOKEN not in (results["bad"].error or "")
    assert results["default"].state == TokenState.OK
    assert h.db.get_setting("tokens.last_check_at") == "2026-10-20T06:00:00Z"
