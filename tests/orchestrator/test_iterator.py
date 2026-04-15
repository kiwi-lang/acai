"""Tests for assai.orchestrator.iterator — sync and async SSE iterators."""

from __future__ import annotations

import json
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse

from assai.orchestrator.iterator import (
    AsyncSSEIterator,
    SSEIterator,
    ServerSentEvent,
    _parse_sse_lines,
)

# ======================================================================
# Tiny SSE server used as a test fixture
# ======================================================================

_app = FastAPI()


def _sse_payload(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@_app.post("/sse")
async def sse_post(request: Request):
    body = await request.json()
    n = body.get("n", 3)

    async def generate():
        for i in range(n):
            yield _sse_payload("token", {"token": f"tok{i}"})
        yield _sse_payload("done", {"status": "ok"})

    return StreamingResponse(generate(), media_type="text/event-stream")


@_app.get("/sse")
async def sse_get(request: Request):
    n = int(request.query_params.get("n", "3"))

    async def generate():
        for i in range(n):
            yield _sse_payload("token", {"token": f"tok{i}"})
        yield _sse_payload("done", {"status": "ok"})

    return StreamingResponse(generate(), media_type="text/event-stream")


@_app.post("/sse/error")
async def sse_error():
    return StreamingResponse(content="nope", status_code=500)


@_app.post("/sse/echo-headers")
async def sse_echo_headers(request: Request):
    interesting = {k: v for k, v in request.headers.items() if k.startswith("x-")}

    async def generate():
        yield _sse_payload("headers", interesting)

    return StreamingResponse(generate(), media_type="text/event-stream")


@_app.post("/sse/multi-data")
async def sse_multi_data(request: Request):
    """Emit an event whose data spans multiple ``data:`` lines."""
    async def generate():
        yield "event: multi\ndata: line1\ndata: line2\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@_app.post("/sse/with-id")
async def sse_with_id(request: Request):
    async def generate():
        yield "id: 42\nevent: ping\ndata: {}\n\n"
        yield "id: 43\nretry: 5000\nevent: pong\ndata: {}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ======================================================================
# Fixture: start the server once per test session
# ======================================================================

_PORT = 18932


@pytest.fixture(scope="session")
def sse_server():
    cfg = uvicorn.Config(_app, host="127.0.0.1", port=_PORT, log_level="error")
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("SSE test server failed to start")

    yield f"http://127.0.0.1:{_PORT}"
    server.should_exit = True
    thread.join(timeout=3)


# ======================================================================
# Unit tests for ServerSentEvent and parsing
# ======================================================================


class TestServerSentEvent:
    def test_defaults(self):
        e = ServerSentEvent()
        assert e.event == ""
        assert e.data == ""
        assert e.id == ""
        assert e.retry is None

    def test_json(self):
        e = ServerSentEvent(data='{"a": 1}')
        assert e.json() == {"a": 1}

    def test_json_raises_on_bad_data(self):
        e = ServerSentEvent(data="not json")
        with pytest.raises(Exception):
            e.json()

    def test_parse_basic(self):
        lines = ["event: token", "data: {\"tok\": 1}"]
        e = _parse_sse_lines(lines)
        assert e.event == "token"
        assert e.json() == {"tok": 1}
        assert e.id == ""
        assert e.retry is None

    def test_parse_multi_data(self):
        lines = ["event: multi", "data: line1", "data: line2"]
        e = _parse_sse_lines(lines)
        assert e.data == "line1\nline2"

    def test_parse_id_and_retry(self):
        lines = ["id: 99", "retry: 3000", "event: ping", "data: {}"]
        e = _parse_sse_lines(lines)
        assert e.id == "99"
        assert e.retry == 3000
        assert e.event == "ping"

    def test_parse_bad_retry_ignored(self):
        lines = ["retry: not_a_number"]
        e = _parse_sse_lines(lines)
        assert e.retry is None


# ======================================================================
# Sync tests
# ======================================================================


class TestSSEIterator:
    def test_basic_stream(self, sse_server):
        events = list(SSEIterator(f"{sse_server}/sse", json={"n": 3}))

        assert all(isinstance(e, ServerSentEvent) for e in events)
        assert len(events) == 4  # 3 tokens + done
        assert events[0].event == "token"
        assert events[0].json() == {"token": "tok0"}
        assert events[-1].event == "done"
        assert events[-1].json() == {"status": "ok"}

    def test_get_method(self, sse_server):
        events = list(SSEIterator(f"{sse_server}/sse", method="GET", params={"n": "2"}))
        assert len(events) == 3
        assert [e.event for e in events] == ["token", "token", "done"]

    def test_custom_headers(self, sse_server):
        events = list(SSEIterator(
            f"{sse_server}/sse/echo-headers",
            headers={"X-Custom": "hello"},
            json={},
        ))
        assert len(events) == 1
        assert events[0].event == "headers"
        assert events[0].json()["x-custom"] == "hello"

    def test_http_error_raises(self, sse_server):
        with pytest.raises(Exception):
            list(SSEIterator(f"{sse_server}/sse/error", json={}))

    def test_empty_stream(self, sse_server):
        events = list(SSEIterator(f"{sse_server}/sse", json={"n": 0}))
        assert len(events) == 1
        assert events[0].event == "done"

    def test_close_is_idempotent(self, sse_server):
        it = SSEIterator(f"{sse_server}/sse", json={"n": 1})
        list(it)
        it.close()
        it.close()

    def test_multi_data_lines(self, sse_server):
        events = list(SSEIterator(f"{sse_server}/sse/multi-data", json={}))
        assert len(events) == 1
        assert events[0].data == "line1\nline2"

    def test_id_and_retry(self, sse_server):
        events = list(SSEIterator(f"{sse_server}/sse/with-id", json={}))
        assert events[0].id == "42"
        assert events[0].event == "ping"
        assert events[0].retry is None
        assert events[1].id == "43"
        assert events[1].retry == 5000


# ======================================================================
# Async tests
# ======================================================================


class TestAsyncSSEIterator:
    @pytest.mark.asyncio
    async def test_basic_stream(self, sse_server):
        events = [e async for e in AsyncSSEIterator(f"{sse_server}/sse", json={"n": 3})]

        assert all(isinstance(e, ServerSentEvent) for e in events)
        assert len(events) == 4
        assert events[0].event == "token"
        assert events[0].json() == {"token": "tok0"}
        assert events[-1].event == "done"

    @pytest.mark.asyncio
    async def test_get_method(self, sse_server):
        events = [
            e async for e in AsyncSSEIterator(
                f"{sse_server}/sse", method="GET", params={"n": "2"}
            )
        ]
        assert len(events) == 3
        assert [e.event for e in events] == ["token", "token", "done"]

    @pytest.mark.asyncio
    async def test_custom_headers(self, sse_server):
        events = [
            e async for e in AsyncSSEIterator(
                f"{sse_server}/sse/echo-headers",
                headers={"X-Custom": "world"},
                json={},
            )
        ]
        assert len(events) == 1
        assert events[0].json()["x-custom"] == "world"

    @pytest.mark.asyncio
    async def test_http_error_raises(self, sse_server):
        with pytest.raises(Exception):
            async for _ in AsyncSSEIterator(f"{sse_server}/sse/error", json={}):
                pass

    @pytest.mark.asyncio
    async def test_empty_stream(self, sse_server):
        events = [e async for e in AsyncSSEIterator(f"{sse_server}/sse", json={"n": 0})]
        assert len(events) == 1
        assert events[0].event == "done"

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self, sse_server):
        it = AsyncSSEIterator(f"{sse_server}/sse", json={"n": 1})
        async for _ in it:
            pass
        await it.close()
        await it.close()

    @pytest.mark.asyncio
    async def test_multi_data_lines(self, sse_server):
        events = [e async for e in AsyncSSEIterator(f"{sse_server}/sse/multi-data", json={})]
        assert len(events) == 1
        assert events[0].data == "line1\nline2"

    @pytest.mark.asyncio
    async def test_id_and_retry(self, sse_server):
        events = [e async for e in AsyncSSEIterator(f"{sse_server}/sse/with-id", json={})]
        assert events[0].id == "42"
        assert events[1].retry == 5000
