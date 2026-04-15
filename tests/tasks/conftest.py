"""Shared fixtures for assai/tasks tests.

Provides a lightweight mock worker (FastAPI + uvicorn in a thread),
in-memory ChatStore, AgentStore with a default template, and a real
LoadBalancer wired to the mock worker.
"""

from __future__ import annotations

import json
import os
import threading
import time
import tempfile

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from assai.orchestrator.agent_store import AgentDef, AgentStore
from assai.orchestrator.chat import ChatStore
from assai.orchestrator.config import AssaiConfig
from assai.orchestrator.load_balancer import LoadBalancer
from assai.orchestrator.stream import StreamTracker

# ======================================================================
# Mock worker application
# ======================================================================

_worker_app = FastAPI()


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _detect_mode(body: dict) -> tuple[str, str]:
    """Derive the mock response mode from the last user message content.

    Convention:
    - Message containing "[tool]" → return tool_call deltas (only on the
      first call — if tool results already exist in the messages, fall
      through to normal tokens so the follow-up loop terminates).
    - Message containing "[error:" → return an error event with the text
      after the colon.
    - Otherwise → return 3 word tokens.
    """
    messages = body.get("messages", [])

    has_tool_results = any(m.get("role") == "tool" for m in messages)

    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content", "")
            break

    if "[tool]" in last_user and not has_tool_results:
        return "with-tools", ""
    if "[error:" in last_user:
        error_text = last_user.split("[error:", 1)[1].split("]", 1)[0]
        return "tokens", error_text
    return "tokens", ""


@_worker_app.post("/llm/complete")
async def llm_complete(request: Request):
    body = await request.json()
    task_id = body.get("task_id", "test")

    mode_override = body.get("mode", "")
    error_override = body.get("inject_error", "")
    n = body.get("n_tokens", 3)

    if mode_override or error_override:
        mode, error = mode_override or "tokens", error_override
    else:
        mode, error = _detect_mode(body)

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


@_worker_app.post("/tools/call")
async def tool_call(request: Request):
    body = await request.json()
    tool = body.get("tool", "")
    if tool == "fail":
        return JSONResponse({"error": "tool not found"}, status_code=404)
    return {"result": f"result of {tool}"}


# ======================================================================
# Fixtures
# ======================================================================

_PORT = 18977


@pytest.fixture(scope="session")
def worker_base_url():
    """Start the mock worker server and yield its base URL."""
    cfg = uvicorn.Config(_worker_app, host="127.0.0.1", port=_PORT, log_level="error")
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Mock worker server failed to start")

    yield f"http://127.0.0.1:{_PORT}"
    server.should_exit = True
    thread.join(timeout=3)


@pytest.fixture()
def load_balancer(worker_base_url):
    """LoadBalancer with the mock worker pre-registered."""
    lb = LoadBalancer(heartbeat_timeout=9999)
    lb.start()
    lb.register(worker_base_url, capabilities={})
    return lb


@pytest.fixture()
def chat_store(tmp_path):
    """ChatStore backed by a temporary directory."""
    return ChatStore(str(tmp_path))


@pytest.fixture()
def agent_store(tmp_path):
    """AgentStore with a writable workspace dir.

    The builtin agents ship with the package, so ``get("default")``
    and ``get("thinker")`` work out of the box.  The workspace dir
    is a scratch space for any test that needs to write custom agents.
    """
    ws_agents = str(tmp_path / "agents")
    os.makedirs(ws_agents, exist_ok=True)
    return AgentStore(ws_agents)


@pytest.fixture()
def assai_config(tmp_path):
    """Minimal AssaiConfig pointing at the tmp workspace."""
    return AssaiConfig(workspace=str(tmp_path))


@pytest.fixture()
def stream_tracker():
    return StreamTracker()


@pytest.fixture()
def graph_deps(agent_store, chat_store, assai_config, stream_tracker):
    """Common keyword arguments for TaskGraph constructors."""
    return dict(
        agent_store=agent_store,
        chat=chat_store,
        config=assai_config,
        tracker=stream_tracker,
        projects=None,
        tool_registry=None,
    )
