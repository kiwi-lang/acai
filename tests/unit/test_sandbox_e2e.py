"""End-to-end unit tests for the Worker → LLM → Sandboxed Tool Call flow.

Tests the full request chain without real containers:

1. A FastAPI **tool router** is wired with a :class:`SandboxProxy`.
2. The proxy's sandbox is a mock that points at a local **mock MCP
   server** (a second FastAPI app mimicking ``acai mcp``).
3. Test HTTP requests hit the tool router, which decides whether to
   proxy to the mock MCP or execute in-process.

This validates:
- SandboxProxy integration with the tool router
- Proxy decision logic (sandbox=True tools go to sandbox, others don't)
- SSE relay from sandbox MCP back to the caller
- Lazy sandbox startup on first qualifying call
- Error propagation when the sandbox MCP returns errors
- Health endpoint of the mock MCP
- ``/tools/list`` on the mock MCP
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest
import requests
import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse

from acai.orchestrator.config import SandboxConfig
from acai.orchestrator.tools import ToolRegistry, tool
from acai.worker.sandbox.base import Sandbox
from acai.worker.sandbox_proxy import SandboxProxy


# ======================================================================
# Mock MCP server (simulates ``acai mcp`` inside a container)
# ======================================================================

_mcp_app = FastAPI()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@_mcp_app.get("/health")
def mcp_health():
    return {"ok": True, "tools": 3}


@_mcp_app.get("/tools/list")
def mcp_list_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "shell.run",
                "description": "Execute a shell command",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "filesystem.read_file",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git.status",
                "description": "Git status",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ]


@_mcp_app.post("/tools/call")
async def mcp_call_tool(request: Request):
    body = await request.json()
    tool_name = body.get("tool", "")
    args = body.get("args", {})
    namespace = tool_name.rsplit(".", 1)[0] if "." in tool_name else ""

    async def generate():
        if namespace == "shell":
            cmd = args.get("command", "")
            result = json.dumps({
                "stdout": f"mock output of: {cmd}\n",
                "stderr": "",
                "returncode": 0,
            })
            yield _sse("result", {"tool": tool_name, "result": result})
            yield _sse("done", {})

        elif namespace == "filesystem":
            path = args.get("path", "")
            yield _sse("result", {"tool": tool_name, "result": f"contents of {path}"})
            yield _sse("done", {})

        elif tool_name == "fail.tool":
            yield _sse("error", {"tool": tool_name, "error": "tool execution failed inside sandbox"})
            yield _sse("done", {})

        else:
            yield _sse("error", {"tool": tool_name, "error": f"unknown tool: {tool_name}"})
            yield _sse("done", {})

    return StreamingResponse(generate(), media_type="text/event-stream")


# ======================================================================
# Mock Sandbox (points at the mock MCP server)
# ======================================================================

class MockSandbox(Sandbox):
    """In-process sandbox that delegates to the mock MCP server."""

    def __init__(self, mcp_url: str):
        self._mcp_url = mcp_url
        self._running = False
        self._started_with: dict = {}

    def start(self, project_path, sandbox_config=None, session_id="default", agent_name=""):
        self._running = True
        self._started_with = {
            "project_path": project_path,
            "session_id": session_id,
            "agent_name": agent_name,
        }

    def stop(self):
        self._running = False

    @property
    def running(self):
        return self._running

    @property
    def endpoint(self):
        return self._mcp_url


# ======================================================================
# Test tools (registered in-process on the worker side)
# ======================================================================

@tool(permissions=("execute",), sandbox=True)
def sandboxed_run(command: str) -> str:
    """A tool that should be proxied to the sandbox."""
    return json.dumps({"stdout": "should not see this — should be proxied", "returncode": 0})


@tool(permissions=("read",), sandbox=False)
def local_read(path: str) -> str:
    """A tool that runs in-process, never proxied."""
    return f"local content of {path}"


# ======================================================================
# Fixtures
# ======================================================================

_MCP_PORT = 19876


@pytest.fixture(scope="module")
def mcp_server_url():
    """Start the mock MCP server and return its URL."""
    cfg = uvicorn.Config(_mcp_app, host="127.0.0.1", port=_MCP_PORT, log_level="error")
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Mock MCP server failed to start")

    yield f"http://127.0.0.1:{_MCP_PORT}"
    server.should_exit = True
    thread.join(timeout=3)


@pytest.fixture()
def registry():
    """ToolRegistry with test tools registered."""
    reg = ToolRegistry()
    reg.register(sandboxed_run, "shell")
    reg.register(local_read, "filesystem")
    return reg


@pytest.fixture()
def sandbox_proxy(mcp_server_url, registry):
    """SandboxProxy wired to the mock MCP server."""
    cfg = SandboxConfig(type="podman")
    proxy = SandboxProxy(default_config=cfg, sandbox_predicate=registry.is_sandboxed)
    mock_sb = MockSandbox(mcp_server_url)
    proxy._sandbox = mock_sb
    proxy._active_project = os.path.abspath("/workspace")
    return proxy


_WORKER_PORT = 19877


@pytest.fixture(scope="module")
def worker_url(mcp_server_url):
    """Start a worker-like tool router with sandbox proxy and return its URL."""
    reg = ToolRegistry()
    reg.register(sandboxed_run, "shell")
    reg.register(local_read, "filesystem")

    cfg = SandboxConfig(type="podman")
    proxy = SandboxProxy(default_config=cfg, sandbox_predicate=reg.is_sandboxed)
    mock_sb = MockSandbox(mcp_server_url)
    default_project = proxy._resolve_project_path({})
    mock_sb.start(default_project, session_id="test")
    proxy._sandbox = mock_sb
    proxy._active_project = default_project

    app = FastAPI()
    tool_router = reg.router(url_prefix="/tools", sandbox_proxy=proxy)
    app.include_router(tool_router)

    @app.get("/health")
    def health():
        return {"ok": True}

    ucfg = uvicorn.Config(app, host="127.0.0.1", port=_WORKER_PORT, log_level="error")
    server = uvicorn.Server(ucfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Worker tool server failed to start")

    yield f"http://127.0.0.1:{_WORKER_PORT}"
    server.should_exit = True
    thread.join(timeout=3)


def _parse_sse(response) -> list[tuple[str, dict]]:
    """Parse SSE events from a streaming response."""
    events = []
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("data: "):
            data = json.loads(line[6:])
            events.append(data)
        elif line.startswith("event: "):
            pass
    return events


def _parse_sse_typed(response) -> list[tuple[str, dict]]:
    """Parse SSE events with their event type."""
    events = []
    current_event = None
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            events.append((current_event or "message", data))
    return events


# ======================================================================
# Mock MCP server tests
# ======================================================================


class TestMockMcpServer:
    """Verify the mock MCP server itself works correctly."""

    def test_health(self, mcp_server_url):
        resp = requests.get(f"{mcp_server_url}/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_tools_list(self, mcp_server_url):
        resp = requests.get(f"{mcp_server_url}/tools/list", timeout=5)
        assert resp.status_code == 200
        tools = resp.json()
        names = [t["function"]["name"] for t in tools]
        assert "shell.run" in names
        assert "filesystem.read_file" in names
        assert "git.status" in names

    def test_shell_run(self, mcp_server_url):
        resp = requests.post(
            f"{mcp_server_url}/tools/call",
            json={"tool": "shell.run", "args": {"command": "echo hello"}},
            stream=True, timeout=10,
        )
        assert resp.status_code == 200
        events = _parse_sse_typed(resp)

        result_events = [e for e in events if e[0] == "result"]
        assert len(result_events) >= 1
        result_data = json.loads(result_events[0][1]["result"])
        assert "echo hello" in result_data["stdout"]
        assert result_data["returncode"] == 0

    def test_file_read(self, mcp_server_url):
        resp = requests.post(
            f"{mcp_server_url}/tools/call",
            json={"tool": "filesystem.read_file", "args": {"path": "/etc/hosts"}},
            stream=True, timeout=10,
        )
        events = _parse_sse_typed(resp)
        result_events = [e for e in events if e[0] == "result"]
        assert "contents of /etc/hosts" in result_events[0][1]["result"]

    def test_unknown_tool(self, mcp_server_url):
        resp = requests.post(
            f"{mcp_server_url}/tools/call",
            json={"tool": "nonexistent.tool", "args": {}},
            stream=True, timeout=10,
        )
        events = _parse_sse_typed(resp)
        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) >= 1
        assert "unknown tool" in error_events[0][1]["error"]

    def test_tool_error(self, mcp_server_url):
        resp = requests.post(
            f"{mcp_server_url}/tools/call",
            json={"tool": "fail.tool", "args": {}},
            stream=True, timeout=10,
        )
        events = _parse_sse_typed(resp)
        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) >= 1
        assert "failed inside sandbox" in error_events[0][1]["error"]


# ======================================================================
# Worker → Sandbox proxy integration
# ======================================================================


class TestWorkerSandboxedToolCall:
    """Full Worker → SandboxProxy → MCP chain via real HTTP."""

    def test_sandboxed_tool_proxied_to_mcp(self, worker_url):
        """A tool with sandbox=True should be proxied to the MCP server."""
        resp = requests.post(
            f"{worker_url}/tools/call",
            json={
                "tool": "shell.sandboxed_run",
                "args": {"command": "ls -la"},
                "context": {"uses_sandbox": True},
            },
            stream=True, timeout=10,
        )
        assert resp.status_code == 200
        events = _parse_sse_typed(resp)

        result_events = [e for e in events if e[0] == "result"]
        assert len(result_events) >= 1

        result_data = result_events[0][1]
        inner = json.loads(result_data["result"])
        assert "ls -la" in inner["stdout"]
        assert inner["returncode"] == 0

    def test_local_tool_runs_in_process(self, worker_url):
        """A tool with sandbox=False should run in the worker process."""
        resp = requests.post(
            f"{worker_url}/tools/call",
            json={
                "tool": "filesystem.local_read",
                "args": {"path": "/tmp/test.txt"},
                "context": {"uses_sandbox": True},
            },
            stream=True, timeout=10,
        )
        assert resp.status_code == 200
        events = _parse_sse_typed(resp)

        result_events = [e for e in events if e[0] == "result"]
        assert len(result_events) >= 1
        assert "local content of /tmp/test.txt" in result_events[0][1]["result"]

    def test_sandboxed_tool_proxied_even_without_ctx_when_running(self, worker_url):
        """Once the sandbox is running, sandbox=True tools are always proxied."""
        resp = requests.post(
            f"{worker_url}/tools/call",
            json={
                "tool": "shell.sandboxed_run",
                "args": {"command": "echo hi"},
            },
            stream=True, timeout=10,
        )
        assert resp.status_code == 200
        events = _parse_sse_typed(resp)

        result_events = [e for e in events if e[0] == "result"]
        assert len(result_events) >= 1
        inner = json.loads(result_events[0][1]["result"])
        assert "echo hi" in inner["stdout"]

    def test_unknown_tool_returns_404(self, worker_url):
        resp = requests.post(
            f"{worker_url}/tools/call",
            json={"tool": "nope.does_not_exist", "args": {}},
            timeout=10,
        )
        assert resp.status_code == 404

    def test_sse_done_event_present(self, worker_url):
        """Every successful call should end with a 'done' SSE event."""
        resp = requests.post(
            f"{worker_url}/tools/call",
            json={
                "tool": "shell.sandboxed_run",
                "args": {"command": "pwd"},
                "context": {"uses_sandbox": True},
            },
            stream=True, timeout=10,
        )
        events = _parse_sse_typed(resp)
        event_types = [e[0] for e in events]
        assert "done" in event_types

    def test_sse_content_type(self, worker_url):
        """Response should be text/event-stream."""
        resp = requests.post(
            f"{worker_url}/tools/call",
            json={
                "tool": "filesystem.local_read",
                "args": {"path": "x"},
            },
            stream=True, timeout=10,
        )
        assert "text/event-stream" in resp.headers.get("content-type", "")


# ======================================================================
# Worker without pre-started sandbox
# ======================================================================


_WORKER_COLD_PORT = 19878


@pytest.fixture(scope="module")
def worker_cold_url(mcp_server_url):
    """Worker with sandbox configured but NOT pre-started."""
    reg = ToolRegistry()
    reg.register(sandboxed_run, "shell")
    reg.register(local_read, "filesystem")

    cfg = SandboxConfig(type="podman")
    proxy = SandboxProxy(default_config=cfg, sandbox_predicate=reg.is_sandboxed)

    app = FastAPI()
    tool_router = reg.router(url_prefix="/tools", sandbox_proxy=proxy)
    app.include_router(tool_router)

    ucfg = uvicorn.Config(app, host="127.0.0.1", port=_WORKER_COLD_PORT, log_level="error")
    server = uvicorn.Server(ucfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Cold worker failed to start")

    yield f"http://127.0.0.1:{_WORKER_COLD_PORT}"
    server.should_exit = True
    thread.join(timeout=3)


class TestWorkerColdSandbox:
    """Tests with sandbox configured but not running."""

    def test_sandboxed_tool_runs_locally_without_uses_sandbox(self, worker_cold_url):
        """When sandbox is not running and no uses_sandbox, tool runs in-process."""
        resp = requests.post(
            f"{worker_cold_url}/tools/call",
            json={
                "tool": "shell.sandboxed_run",
                "args": {"command": "echo hi"},
            },
            stream=True, timeout=10,
        )
        assert resp.status_code == 200
        events = _parse_sse_typed(resp)

        result_events = [e for e in events if e[0] == "result"]
        assert len(result_events) >= 1
        assert "should not see this" in result_events[0][1]["result"]

    def test_non_sandboxed_tool_always_local(self, worker_cold_url):
        """Non-sandbox tools always run in-process."""
        resp = requests.post(
            f"{worker_cold_url}/tools/call",
            json={
                "tool": "filesystem.local_read",
                "args": {"path": "/etc/hosts"},
                "context": {"uses_sandbox": True},
            },
            stream=True, timeout=10,
        )
        assert resp.status_code == 200
        events = _parse_sse_typed(resp)

        result_events = [e for e in events if e[0] == "result"]
        assert len(result_events) >= 1
        assert "local content of /etc/hosts" in result_events[0][1]["result"]


# ======================================================================
# SandboxProxy decision logic (unit-level, no HTTP)
# ======================================================================


class TestProxyDecisionWithRegistry:
    """Verify should_proxy with a real ToolRegistry."""

    def test_sandboxed_tool_with_running_sandbox(self, sandbox_proxy):
        sandbox_proxy._sandbox.start("/workspace")
        assert sandbox_proxy.should_proxy(
            "shell.sandboxed_run", {"uses_sandbox": True}
        ) is True

    def test_non_sandboxed_tool(self, sandbox_proxy):
        sandbox_proxy._sandbox.start("/workspace")
        assert sandbox_proxy.should_proxy(
            "filesystem.local_read", {"uses_sandbox": True}
        ) is False

    def test_sandboxed_tool_no_context(self, sandbox_proxy):
        sandbox_proxy._sandbox._running = False
        assert sandbox_proxy.should_proxy("shell.sandboxed_run", {}) is False

    def test_sandboxed_tool_running_sandbox_no_ctx(self, sandbox_proxy):
        sandbox_proxy._sandbox.start("/workspace")
        assert sandbox_proxy.should_proxy("shell.sandboxed_run", {}) is True


# ======================================================================
# MockSandbox lifecycle
# ======================================================================


class TestMockSandboxLifecycle:
    def test_start_stop(self, mcp_server_url):
        sb = MockSandbox(mcp_server_url)
        assert sb.running is False

        sb.start("/workspace", session_id="s1", agent_name="coder")
        assert sb.running is True
        assert sb.endpoint == mcp_server_url
        assert sb._started_with["session_id"] == "s1"
        assert sb._started_with["agent_name"] == "coder"

        sb.stop()
        assert sb.running is False


# ======================================================================
# Lazy startup integration
# ======================================================================


class TestLazyStartup:
    """Verify that the SandboxProxy starts the sandbox lazily."""

    def test_proxy_starts_sandbox_on_first_call(self, mcp_server_url):
        reg = ToolRegistry()
        reg.register(sandboxed_run, "shell")

        cfg = SandboxConfig(type="podman")
        proxy = SandboxProxy(default_config=cfg, sandbox_predicate=reg.is_sandboxed)

        mock_sb = MockSandbox(mcp_server_url)
        assert mock_sb.running is False

        from unittest.mock import patch
        with patch("acai.worker.sandbox.create_sandbox", return_value=mock_sb):
            proxy._ensure_started({
                "conversation": "conv-123",
                "agent_name": "coder",
            })

        assert mock_sb.running is True
        assert mock_sb._started_with["session_id"] == "conv-123"
        assert mock_sb._started_with["agent_name"] == "coder"

    def test_proxy_skips_restart_when_running(self, mcp_server_url):
        reg = ToolRegistry()
        reg.register(sandboxed_run, "shell")

        cfg = SandboxConfig(type="podman")
        proxy = SandboxProxy(default_config=cfg, sandbox_predicate=reg.is_sandboxed)

        project = os.path.abspath("/workspace")
        mock_sb = MockSandbox(mcp_server_url)
        mock_sb.start(project)
        proxy._sandbox = mock_sb
        proxy._active_project = project

        start_count = 0
        original_start = mock_sb.start
        def counting_start(*a, **kw):
            nonlocal start_count
            start_count += 1
            original_start(*a, **kw)
        mock_sb.start = counting_start

        proxy._ensure_started({"conversation": "c1", "project_path": project})
        assert start_count == 0


# ======================================================================
# Project-scoped sandbox recycling
# ======================================================================


class TestProjectScopedSandbox:
    """Verify sandboxes are recycled when the project changes."""

    def test_same_project_keeps_sandbox(self, mcp_server_url):
        """Calling _ensure_started for the same project should not restart."""
        cfg = SandboxConfig(type="podman")
        proxy = SandboxProxy(default_config=cfg, sandbox_predicate=lambda _: True)

        project = os.path.abspath("/projects/alpha")
        mock_sb = MockSandbox(mcp_server_url)
        mock_sb.start(project)
        proxy._sandbox = mock_sb
        proxy._active_project = project

        proxy._ensure_started({"project_path": "/projects/alpha"})
        assert proxy._sandbox is mock_sb
        assert proxy.active_project == project
        assert mock_sb.running is True

    def test_different_project_recycles_sandbox(self, mcp_server_url):
        """Switching to a different project must stop old, start new sandbox."""
        cfg = SandboxConfig(type="podman")
        proxy = SandboxProxy(default_config=cfg, sandbox_predicate=lambda _: True)

        old_project = os.path.abspath("/projects/alpha")
        mock_sb = MockSandbox(mcp_server_url)
        mock_sb.start(old_project)
        proxy._sandbox = mock_sb
        proxy._active_project = old_project

        new_sb = MockSandbox(mcp_server_url)
        from unittest.mock import patch
        with patch("acai.worker.sandbox.create_sandbox", return_value=new_sb):
            proxy._ensure_started({
                "project_path": "/projects/beta",
                "conversation": "conv-2",
            })

        assert mock_sb.running is False
        assert proxy._sandbox is new_sb
        assert new_sb.running is True
        assert proxy.active_project == os.path.abspath("/projects/beta")
        assert new_sb._started_with["project_path"] == os.path.abspath("/projects/beta")

    def test_stop_clears_active_project(self, mcp_server_url):
        cfg = SandboxConfig(type="podman")
        proxy = SandboxProxy(default_config=cfg, sandbox_predicate=lambda _: True)
        mock_sb = MockSandbox(mcp_server_url)
        mock_sb.start("/workspace")
        proxy._sandbox = mock_sb
        proxy._active_project = os.path.abspath("/workspace")

        proxy.stop()
        assert proxy.active_project is None
        assert proxy.running is False

    def test_active_project_property(self, mcp_server_url):
        cfg = SandboxConfig(type="podman")
        proxy = SandboxProxy(default_config=cfg, sandbox_predicate=lambda _: True)
        assert proxy.active_project is None

        mock_sb = MockSandbox(mcp_server_url)
        from unittest.mock import patch
        with patch("acai.worker.sandbox.create_sandbox", return_value=mock_sb):
            proxy._ensure_started({
                "project_path": "/projects/gamma",
                "conversation": "c1",
            })
        assert proxy.active_project == os.path.abspath("/projects/gamma")

    @pytest.mark.asyncio
    async def test_proxy_call_recycles_on_project_switch(self, mcp_server_url):
        """proxy_call with a different project should recycle the sandbox."""
        cfg = SandboxConfig(type="podman")
        proxy = SandboxProxy(default_config=cfg, sandbox_predicate=lambda _: True)

        old_sb = MockSandbox(mcp_server_url)
        old_sb.start("/projects/old")
        proxy._sandbox = old_sb
        proxy._active_project = os.path.abspath("/projects/old")

        new_sb = MockSandbox(mcp_server_url)
        from unittest.mock import patch
        with patch("acai.worker.sandbox.create_sandbox", return_value=new_sb):
            resp = await proxy.proxy_call(
                "shell.run",
                {"command": "pwd"},
                {"uses_sandbox": True, "project_path": "/projects/new"},
            )

        assert old_sb.running is False
        assert proxy.active_project == os.path.abspath("/projects/new")

        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        body = "".join(chunks)
        assert "result" in body


# ======================================================================
# Full async proxy_call (unit, no HTTP server)
# ======================================================================


class TestProxyCallAsync:
    def _wired_proxy(self, mcp_server_url):
        """Helper: proxy with MockSandbox already running for /workspace."""
        cfg = SandboxConfig(type="podman")
        proxy = SandboxProxy(default_config=cfg, sandbox_predicate=lambda _: True)
        mock_sb = MockSandbox(mcp_server_url)
        mock_sb.start("/workspace")
        proxy._sandbox = mock_sb
        proxy._active_project = os.path.abspath("/workspace")
        return proxy

    @pytest.mark.asyncio
    async def test_proxy_call_relays_mcp_response(self, mcp_server_url):
        proxy = self._wired_proxy(mcp_server_url)

        resp = await proxy.proxy_call(
            "shell.run",
            {"command": "whoami"},
            {"uses_sandbox": True, "project_path": "/workspace"},
        )

        assert resp.media_type == "text/event-stream"

        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        body = "".join(chunks)

        assert "result" in body
        assert "whoami" in body

    @pytest.mark.asyncio
    async def test_proxy_call_relays_error(self, mcp_server_url):
        proxy = self._wired_proxy(mcp_server_url)

        resp = await proxy.proxy_call(
            "fail.tool", {},
            {"uses_sandbox": True, "project_path": "/workspace"},
        )

        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        body = "".join(chunks)

        assert "error" in body
        assert "failed inside sandbox" in body

    @pytest.mark.asyncio
    async def test_proxy_call_context_forwarded(self, mcp_server_url):
        proxy = self._wired_proxy(mcp_server_url)

        ctx = {"uses_sandbox": True, "conversation": "c1", "project_path": "/workspace"}
        resp = await proxy.proxy_call("shell.run", {"command": "ls"}, ctx)

        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        body = "".join(chunks)

        assert "result" in body
