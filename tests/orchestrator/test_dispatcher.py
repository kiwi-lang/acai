"""Tests for assai.orchestrator.dispatcher — SSE-based work dispatch."""

from __future__ import annotations

import json
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse
from fastapi.responses import JSONResponse

from assai.orchestrator.dispatcher import dispatch_llm, dispatch_tool
from assai.orchestrator.stream import StreamTracker

# ======================================================================
# Tiny worker server used as a test fixture
# ======================================================================

_app = FastAPI()


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@_app.post("/worker/llm/complete")
async def llm_complete(request: Request):
    body = await request.json()
    task_id = body.get("task_id", "test")
    n = body.get("n_tokens", 3)
    error = body.get("inject_error", "")
    mode = body.get("mode", "tokens")

    async def generate():
        if mode == "with-tools":
            yield _sse_event("token", {"task_id": task_id, "token": "calling tool", "index": 0})
            yield _sse_event("tool_call_delta", {
                "task_id": task_id, "index": 0,
                "id": "call_1", "name": "shell.run", "arguments": '{"cm',
            })
            yield _sse_event("tool_call_delta", {
                "task_id": task_id, "index": 0,
                "id": None, "name": None, "arguments": 'd": "ls"}',
            })
            yield _sse_event("done", {"task_id": task_id})
            return

        for i in range(n):
            yield _sse_event("token", {"task_id": task_id, "token": f"word{i} ", "index": i})
        if error:
            yield _sse_event("error", {"task_id": task_id, "error": error})
        else:
            yield _sse_event("done", {"task_id": task_id})

    return StreamingResponse(generate(), media_type="text/event-stream")


@_app.post("/tools/call")
async def tool_call(request: Request):
    body = await request.json()
    tool = body.get("tool", "")

    async def generate():
        if tool == "fail":
            yield _sse_event("error", {"tool": tool, "error": "tool not found"})
            return
        yield _sse_event("result", {"tool": tool, "result": f"result of {tool}"})
        yield _sse_event("done", {})

    return StreamingResponse(generate(), media_type="text/event-stream")


# ======================================================================
# Fixture: start the server once per test session
# ======================================================================

_PORT = 18933


@pytest.fixture(scope="session")
def worker_server():
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
        raise RuntimeError("Worker test server failed to start")

    yield f"http://127.0.0.1:{_PORT}/worker"
    server.should_exit = True
    thread.join(timeout=3)


# ======================================================================
# dispatch_llm tests
# ======================================================================


class TestDispatchLLM:
    @pytest.mark.asyncio
    async def test_basic_stream(self, worker_server):
        result = await dispatch_llm(
            worker_server,
            {"task_id": "t1", "messages": [], "n_tokens": 3},
        )
        assert result.error is None
        assert result.text == "word0 word1 word2 "
        assert result.tool_calls == []

    @pytest.mark.asyncio
    async def test_empty_stream(self, worker_server):
        result = await dispatch_llm(
            worker_server,
            {"task_id": "t2", "messages": [], "n_tokens": 0},
        )
        assert result.error is None
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_error_event(self, worker_server):
        result = await dispatch_llm(
            worker_server,
            {"task_id": "t3", "messages": [], "n_tokens": 1, "inject_error": "LLM crashed"},
        )
        assert result.error == "LLM crashed"

    @pytest.mark.asyncio
    async def test_tracker_receives_tokens(self, worker_server):
        tracker = StreamTracker()
        q = tracker.subscribe("stream-1")

        result = await dispatch_llm(
            worker_server,
            {"task_id": "t4", "messages": [], "n_tokens": 2},
            stream_id="stream-1",
            tracker=tracker,
        )
        assert result.error is None
        assert result.text == "word0 word1 "

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        token_events = [e for e in events if e["event_type"] == "token"]
        assert len(token_events) == 2

    @pytest.mark.asyncio
    async def test_connection_error(self):
        result = await dispatch_llm(
            "http://127.0.0.1:1/worker",
            {"task_id": "t5", "messages": []},
        )
        assert result.error is not None
        assert "error" in result.error.lower()

    @pytest.mark.asyncio
    async def test_tool_call_deltas(self, worker_server):
        result = await dispatch_llm(
            worker_server,
            {"task_id": "t6", "messages": [], "mode": "with-tools"},
        )
        assert result.error is None
        assert result.text == "calling tool"
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc["function"]["name"] == "shell.run"
        assert json.loads(tc["function"]["arguments"]) == {"cmd": "ls"}


# ======================================================================
# dispatch_tool tests
# ======================================================================


class TestDispatchTool:
    @pytest.mark.asyncio
    async def test_tool_success(self, worker_server):
        base = worker_server.rsplit("/worker", 1)[0]
        result = await dispatch_tool(base, "echo", {"msg": "hi"})
        assert result.error is None
        assert result.text == "result of echo"

    @pytest.mark.asyncio
    async def test_tool_failure(self, worker_server):
        base = worker_server.rsplit("/worker", 1)[0]
        result = await dispatch_tool(base, "fail", {})
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_tool_connection_error(self):
        result = await dispatch_tool("http://127.0.0.1:1", "echo", {})
        assert result.error is not None
