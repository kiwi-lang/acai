"""Unit tests for acai/worker/app.py."""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acai.provider import (
    ContentToken,
    LLMServerError,
    ReasoningToken,
    StreamDone,
    ToolCallDelta,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_provider(name="test-provider", model="llama-3", backend="vllm"):
    prov = MagicMock()
    prov.name = name
    prov.model = model
    prov.backend = backend
    prov.slug = f"{name}/{model}"
    prov.managed = False
    prov.models = []
    prov.get_model.return_value = None
    return prov


def _make_config(tmp_path, provider=None):
    config = MagicMock()
    provider = provider or _make_provider()
    config.active_provider.return_value = provider
    config.local_provider.return_value = None
    config.get_provider.return_value = None
    config.workspace = str(tmp_path)
    config.sandbox = MagicMock()
    config.worker = MagicMock()
    config.worker.port = 5051
    config.worker.orchestrator_url = "http://localhost:5050/agent"
    config.ci = MagicMock()
    return config


@pytest.fixture
def provider():
    return _make_provider()


@pytest.fixture
def config(tmp_path, provider):
    return _make_config(tmp_path, provider)


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    tool_def = MagicMock()
    tool_def.qualified_name = "filesystem.read_file"
    tool_def.sandbox = False
    registry.all_tools.return_value = [tool_def]
    registry.namespaces.return_value = ["filesystem"]
    registry.is_sandboxed.return_value = False
    registry.router.return_value = MagicMock()
    return registry


@pytest.fixture
def mock_llm_server():
    server = MagicMock()
    server.is_running.return_value = True
    server.pid = 12345
    server.managed = True
    server.process = None
    server.read_log.return_value = "log content"
    server.latest_log_path.return_value = "/tmp/llm.log"
    return server


@pytest.fixture
def mock_sandbox_proxy():
    proxy = MagicMock()
    proxy.running = False
    proxy.endpoint = None
    return proxy


def _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy, extra_patches=None):
    """Context manager that patches all worker router dependencies."""
    from contextlib import ExitStack
    patches = [
        patch("acai.worker.app.discover_tools", return_value=mock_registry),
        patch("acai.worker.app.LLMServer", return_value=mock_llm_server),
        patch("acai.worker.sandbox_proxy.SandboxProxy", return_value=mock_sandbox_proxy),
        patch("acai.tools.meta._configure"),
        patch("acai.orchestrator.skill_store.SkillStore"),
        patch("acai.tools.skills._configure"),
        patch("acai.tools.ci._configure"),
    ]
    if extra_patches:
        patches.extend(extra_patches)

    stack = ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


@pytest.fixture
def client(config, mock_registry, mock_llm_server, mock_sandbox_proxy):
    """Create a TestClient with the worker router, fully mocked."""
    from acai.worker.app import create_worker_router

    with _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy):
        router, llm_server, registry, sandbox_proxy = create_worker_router(config)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def llm_mock():
    """A mock LLM instance returned by create_llm."""
    return MagicMock()


@pytest.fixture
def streaming_client(config, mock_registry, mock_llm_server, mock_sandbox_proxy, llm_mock):
    """Client with a controllable LLM mock for stream testing.

    The create_llm patch must persist through request handling since the
    endpoint calls create_llm at request time, not at router creation time.
    """
    from acai.worker.app import create_worker_router

    extra = [patch("acai.worker.app.create_llm", return_value=llm_mock)]
    stack = _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy, extra)
    stack.__enter__()
    router, llm_server, registry, sandbox_proxy = create_worker_router(config)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    yield client
    stack.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Tests: GET /worker/status
# ---------------------------------------------------------------------------


class TestWorkerStatus:
    def test_status_returns_fields(self, client, provider, mock_llm_server):
        resp = client.get("/worker/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_running"] is True
        assert data["llm_pid"] == 12345
        assert data["tools"] == ["filesystem.read_file"]
        assert data["namespaces"] == ["filesystem"]
        assert data["log_path"] == "/tmp/llm.log"
        assert "telemetry" in data

    def test_status_when_llm_not_running(self, config, mock_registry, mock_sandbox_proxy):
        from acai.worker.app import create_worker_router

        llm_server = MagicMock()
        llm_server.is_running.return_value = False
        llm_server.pid = None
        llm_server.latest_log_path.return_value = None

        with _patch_worker_deps(mock_registry, llm_server, mock_sandbox_proxy):
            router, _, _, _ = create_worker_router(config)

        app = FastAPI()
        app.include_router(router)
        cl = TestClient(app)

        resp = cl.get("/worker/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_running"] is False
        assert data["llm_pid"] is None
        assert data["log_path"] is None


# ---------------------------------------------------------------------------
# Tests: GET /worker/sandbox/status
# ---------------------------------------------------------------------------


class TestSandboxStatus:
    def test_sandbox_not_running(self, client):
        resp = client.get("/worker/sandbox/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["endpoint"] is None

    def test_sandbox_running(self, config, mock_registry, mock_llm_server):
        from acai.worker.app import create_worker_router

        proxy = MagicMock()
        proxy.running = True
        proxy.endpoint = "http://sandbox:8080"

        with _patch_worker_deps(mock_registry, mock_llm_server, proxy):
            router, _, _, _ = create_worker_router(config)

        app = FastAPI()
        app.include_router(router)
        cl = TestClient(app)

        resp = cl.get("/worker/sandbox/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["endpoint"] == "http://sandbox:8080"


# ---------------------------------------------------------------------------
# Tests: GET /worker/logs
# ---------------------------------------------------------------------------


class TestWorkerLogs:
    def test_logs_default_tail(self, client, mock_llm_server):
        resp = client.get("/worker/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "log content"
        assert data["path"] == "/tmp/llm.log"
        mock_llm_server.read_log.assert_called_with(tail=200)

    def test_logs_custom_tail(self, client, mock_llm_server):
        resp = client.get("/worker/logs?tail=50")
        assert resp.status_code == 200
        mock_llm_server.read_log.assert_called_with(tail=50)

    def test_logs_no_log_path(self, config, mock_registry, mock_sandbox_proxy):
        from acai.worker.app import create_worker_router

        llm_server = MagicMock()
        llm_server.read_log.return_value = ""
        llm_server.latest_log_path.return_value = None
        llm_server.is_running.return_value = False
        llm_server.pid = None

        with _patch_worker_deps(mock_registry, llm_server, mock_sandbox_proxy):
            router, _, _, _ = create_worker_router(config)

        app = FastAPI()
        app.include_router(router)
        cl = TestClient(app)

        resp = cl.get("/worker/logs")
        assert resp.status_code == 200
        assert resp.json()["path"] == "(none)"


# ---------------------------------------------------------------------------
# Tests: POST /worker/llm/complete (SSE streaming)
# ---------------------------------------------------------------------------


class TestLLMComplete:
    def test_stream_content_tokens(self, streaming_client, llm_mock):
        llm_mock.stream.return_value = [
            ContentToken(text="Hello"),
            ContentToken(text=" world"),
            StreamDone(),
        ]

        resp = streaming_client.post("/worker/llm/complete", json={
            "messages": [{"role": "user", "content": "Hi"}],
            "task_id": "t1",
        })
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        lines = resp.text.strip().split("\n")
        events = _parse_sse(resp.text)

        assert len(events) == 3
        assert events[0]["event"] == "token"
        assert events[0]["data"]["token"] == "Hello"
        assert events[0]["data"]["index"] == 0
        assert events[1]["event"] == "token"
        assert events[1]["data"]["token"] == " world"
        assert events[1]["data"]["index"] == 1
        assert events[2]["event"] == "done"
        assert events[2]["data"]["output_tokens"] == 2

    def test_stream_reasoning_tokens(self, streaming_client, llm_mock):
        llm_mock.stream.return_value = [
            ReasoningToken(text="thinking..."),
            ContentToken(text="answer"),
            StreamDone(),
        ]

        resp = streaming_client.post("/worker/llm/complete", json={
            "messages": [{"role": "user", "content": "Think"}],
            "task_id": "t2",
        })
        assert resp.status_code == 200
        events = _parse_sse(resp.text)

        assert events[0]["event"] == "reasoning"
        assert events[0]["data"]["token"] == "thinking..."
        assert events[1]["event"] == "token"
        assert events[1]["data"]["token"] == "answer"

    def test_stream_tool_call_deltas(self, streaming_client, llm_mock):
        llm_mock.stream.return_value = [
            ToolCallDelta(index=0, id="call_1", name="read_file", arguments='{"path":'),
            ToolCallDelta(index=0, id="call_1", name=None, arguments='"/tmp/x"}'),
            StreamDone(),
        ]

        resp = streaming_client.post("/worker/llm/complete", json={
            "messages": [{"role": "user", "content": "read file"}],
            "task_id": "t3",
        })
        assert resp.status_code == 200
        events = _parse_sse(resp.text)

        tc_events = [e for e in events if e["event"] == "tool_call_delta"]
        assert len(tc_events) == 2
        assert tc_events[0]["data"]["name"] == "read_file"
        assert tc_events[0]["data"]["id"] == "call_1"
        assert tc_events[1]["data"]["arguments"] == '"/tmp/x"}'

    def test_stream_error_during_inference(self, streaming_client, llm_mock):
        llm_mock.stream.side_effect = RuntimeError("connection reset")

        resp = streaming_client.post("/worker/llm/complete", json={
            "messages": [{"role": "user", "content": "Hi"}],
            "task_id": "t4",
        })
        assert resp.status_code == 200
        events = _parse_sse(resp.text)

        assert any(e["event"] == "error" for e in events)
        err_event = next(e for e in events if e["event"] == "error")
        assert "connection reset" in err_event["data"]["error"]

    def test_stream_error_with_response_body(self, streaming_client, llm_mock):
        exc = RuntimeError("API error")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"detail": "rate limited"}
        exc.response = mock_resp
        llm_mock.stream.side_effect = exc

        resp = streaming_client.post("/worker/llm/complete", json={
            "messages": [{"role": "user", "content": "Hi"}],
            "task_id": "t5",
        })
        events = _parse_sse(resp.text)
        err_event = next(e for e in events if e["event"] == "error")
        assert "rate limited" in err_event["data"]["error"]

    def test_empty_messages(self, streaming_client, llm_mock):
        llm_mock.stream.return_value = [StreamDone()]

        resp = streaming_client.post("/worker/llm/complete", json={
            "task_id": "t6",
        })
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert events[-1]["event"] == "done"
        assert events[-1]["data"]["output_tokens"] == 0

    def test_llm_server_start_failure(self, config, mock_registry, mock_sandbox_proxy):
        """When LLM server fails to start, return 503 with error SSE."""
        from acai.worker.app import create_worker_router

        llm_server = MagicMock()
        llm_server.is_running.return_value = False
        llm_server.managed = True
        llm_server.start.side_effect = LLMServerError("GPU OOM")
        llm_server.process = None

        config.local_provider.return_value = _make_provider()

        with _patch_worker_deps(mock_registry, llm_server, mock_sandbox_proxy):
            router, _, _, _ = create_worker_router(config, extern_llm=False)

        app = FastAPI()
        app.include_router(router)
        cl = TestClient(app)

        resp = cl.post("/worker/llm/complete", json={
            "messages": [{"role": "user", "content": "Hi"}],
            "task_id": "t7",
        })
        assert resp.status_code == 503
        assert "GPU OOM" in resp.text

    def test_provider_override(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy):
        """When a provider override is given, it uses the specified provider."""
        from acai.worker.app import create_worker_router

        alt_provider = _make_provider(name="openai", model="gpt-4", backend="api")
        config.get_provider.return_value = alt_provider

        llm_mock_local = MagicMock()
        llm_mock_local.stream.return_value = [
            ContentToken(text="from openai"),
            StreamDone(),
        ]

        extra = [patch("acai.worker.app.create_llm", return_value=llm_mock_local)]
        stack = _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy, extra)
        with stack:
            router, _, _, _ = create_worker_router(config)
            app = FastAPI()
            app.include_router(router)
            cl = TestClient(app)

            resp = cl.post("/worker/llm/complete", json={
                "messages": [{"role": "user", "content": "Hi"}],
                "task_id": "t8",
                "provider": {"name": "openai"},
            })

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert events[0]["data"]["token"] == "from openai"

    def test_provider_override_not_found(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy):
        """Unknown provider override falls back to local provider."""
        from acai.worker.app import create_worker_router

        config.get_provider.return_value = None

        llm_mock_local = MagicMock()
        llm_mock_local.stream.return_value = [StreamDone()]

        extra = [patch("acai.worker.app.create_llm", return_value=llm_mock_local)]
        stack = _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy, extra)
        with stack:
            router, _, _, _ = create_worker_router(config)
            app = FastAPI()
            app.include_router(router)
            cl = TestClient(app)

            resp = cl.post("/worker/llm/complete", json={
                "messages": [{"role": "user", "content": "Hi"}],
                "task_id": "t9",
                "provider": {"name": "nonexistent"},
            })

        assert resp.status_code == 200

    def test_tools_and_thinking_passed_to_llm(self, streaming_client, llm_mock):
        llm_mock.stream.return_value = [StreamDone()]

        resp = streaming_client.post("/worker/llm/complete", json={
            "messages": [{"role": "user", "content": "Hi"}],
            "task_id": "t10",
            "tools": [{"name": "read_file"}],
            "enable_thinking": True,
            "response_format": {"type": "json_object"},
        })
        assert resp.status_code == 200
        call_kwargs = llm_mock.stream.call_args[1]
        assert call_kwargs["tools"] == [{"name": "read_file"}]
        assert call_kwargs["enable_thinking"] is True
        assert call_kwargs["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# Tests: POST /worker/switch-model
# ---------------------------------------------------------------------------


class TestSwitchModel:
    def _make_client_with_llm(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy, extern_llm=False):
        from acai.worker.app import create_worker_router

        with _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy):
            router, _, _, _ = create_worker_router(config, extern_llm=extern_llm)

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_switch_model_success(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy):
        mock_llm_server.is_running.return_value = True

        cl = self._make_client_with_llm(config, mock_registry, mock_llm_server, mock_sandbox_proxy)

        with patch("acai.provider.ProviderConfig.from_dict") as mock_from_dict:
            new_prov = _make_provider(name="new-prov", model="mistral-7b", backend="vllm")
            new_prov.managed = True
            mock_from_dict.return_value = new_prov

            resp = cl.post("/worker/switch-model", json={
                "name": "new-prov", "model": "mistral-7b", "backend": "vllm",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["model"] == "mistral-7b"
        mock_llm_server.stop.assert_called_once()

    def test_switch_model_start_failure(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy):
        mock_llm_server.is_running.return_value = False
        mock_llm_server.start.side_effect = LLMServerError("out of VRAM")

        cl = self._make_client_with_llm(config, mock_registry, mock_llm_server, mock_sandbox_proxy)

        with patch("acai.provider.ProviderConfig.from_dict") as mock_from_dict:
            new_prov = _make_provider(name="new-prov", model="big-model", backend="vllm")
            new_prov.managed = True
            mock_from_dict.return_value = new_prov

            resp = cl.post("/worker/switch-model", json={
                "name": "new-prov", "model": "big-model",
            })

        assert resp.status_code == 503
        assert "out of VRAM" in resp.json()["error"]

    def test_switch_model_extern_llm(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy):
        """When extern_llm=True, don't stop/start the LLM server."""
        cl = self._make_client_with_llm(
            config, mock_registry, mock_llm_server, mock_sandbox_proxy, extern_llm=True,
        )

        with patch("acai.provider.ProviderConfig.from_dict") as mock_from_dict:
            new_prov = _make_provider(name="remote", model="gpt-4", backend="api")
            new_prov.managed = False
            mock_from_dict.return_value = new_prov

            resp = cl.post("/worker/switch-model", json={
                "name": "remote", "model": "gpt-4",
            })

        assert resp.status_code == 200
        mock_llm_server.stop.assert_not_called()
        mock_llm_server.start.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: register_with_orchestrator
# ---------------------------------------------------------------------------


class TestRegisterWithOrchestrator:
    def test_success_on_first_try(self):
        from acai.worker.app import register_with_orchestrator

        with patch("acai.worker.app.http.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"worker_id": "w-123"}
            mock_post.return_value = mock_resp

            wid = register_with_orchestrator(
                "http://localhost:5050/agent", "http://localhost:5051/worker",
                retry_interval=0.01,
            )

        assert wid == "w-123"
        mock_post.assert_called_once()

    def test_retries_on_connection_error(self):
        from acai.worker.app import register_with_orchestrator

        import requests as http_lib

        with patch("acai.worker.app.http.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"worker_id": "w-456"}

            mock_post.side_effect = [
                http_lib.ConnectionError("refused"),
                http_lib.ConnectionError("refused"),
                mock_resp,
            ]

            wid = register_with_orchestrator(
                "http://localhost:5050/agent", "http://localhost:5051/worker",
                retry_interval=0.01, max_retries=5,
            )

        assert wid == "w-456"
        assert mock_post.call_count == 3

    def test_returns_empty_on_exhausted_retries(self):
        from acai.worker.app import register_with_orchestrator

        import requests as http_lib

        with patch("acai.worker.app.http.post") as mock_post:
            mock_post.side_effect = http_lib.ConnectionError("refused")

            wid = register_with_orchestrator(
                "http://localhost:5050/agent", "http://localhost:5051/worker",
                retry_interval=0.01, max_retries=2,
            )

        assert wid == ""
        assert mock_post.call_count == 2

    def test_retries_on_server_error(self):
        from acai.worker.app import register_with_orchestrator

        with patch("acai.worker.app.http.post") as mock_post:
            err_resp = MagicMock()
            err_resp.status_code = 500

            ok_resp = MagicMock()
            ok_resp.status_code = 200
            ok_resp.json.return_value = {"worker_id": "w-789"}

            mock_post.side_effect = [err_resp, ok_resp]

            wid = register_with_orchestrator(
                "http://localhost:5050/agent", "http://localhost:5051/worker",
                retry_interval=0.01, max_retries=3,
            )

        assert wid == "w-789"

    def test_capabilities_sent_in_payload(self):
        from acai.worker.app import register_with_orchestrator

        with patch("acai.worker.app.http.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"worker_id": "w-abc"}
            mock_post.return_value = mock_resp

            caps = {"model": "llama-3", "tools": ["read_file"]}
            register_with_orchestrator(
                "http://localhost:5050/agent", "http://localhost:5051/worker",
                capabilities=caps, retry_interval=0.01,
            )

        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["capabilities"] == caps


# ---------------------------------------------------------------------------
# Tests: HealthReporter
# ---------------------------------------------------------------------------


class TestHealthReporter:
    def test_stop_event_halts_loop(self):
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-1", interval=0.05)

        with patch.object(reporter, "_connect"), \
             patch.object(reporter, "_init_observer"), \
             patch.object(reporter, "_send_heartbeat"):
            t = threading.Thread(target=reporter.run, daemon=True)
            t.start()
            time.sleep(0.1)
            reporter.stop()
            t.join(timeout=1.0)
            assert not t.is_alive()

    def test_heartbeat_via_websocket(self):
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-2", interval=60)
        reporter._sio = MagicMock()
        reporter._sio.connected = True
        reporter._observer = MagicMock(return_value={"gpu_util": 50})

        reporter._send_heartbeat()

        reporter._sio.emit.assert_called_once_with(
            "worker_heartbeat",
            {"worker_id": "w-2", "telemetry": {"gpu_util": 50}},
        )

    def test_heartbeat_falls_back_to_http(self):
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-3", interval=60)
        reporter._sio = MagicMock()
        reporter._sio.connected = False
        reporter._observer = None

        with patch("acai.worker.app.http.post") as mock_post:
            reporter._send_heartbeat()

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[1]["json"]["worker_id"] == "w-3"

    def test_heartbeat_websocket_failure_falls_back(self):
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-4", interval=60)
        reporter._sio = MagicMock()
        reporter._sio.connected = True
        reporter._sio.emit.side_effect = Exception("ws broken")
        reporter._observer = None

        with patch("acai.worker.app.http.post") as mock_post:
            reporter._send_heartbeat()

        mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _json_body helper
# ---------------------------------------------------------------------------


class TestJsonBody:
    @pytest.mark.anyio
    async def test_valid_json(self):
        from acai.worker.app import _json_body
        from unittest.mock import AsyncMock

        request = MagicMock()
        request.json = AsyncMock(return_value={"key": "value"})

        result = await _json_body(request)
        assert result == {"key": "value"}

    @pytest.mark.anyio
    async def test_invalid_json_returns_empty(self):
        from acai.worker.app import _json_body
        from unittest.mock import AsyncMock

        request = MagicMock()
        request.json = AsyncMock(side_effect=ValueError("bad json"))

        result = await _json_body(request)
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: create_worker_app
# ---------------------------------------------------------------------------


class TestCreateWorkerApp:
    def test_creates_app_with_routers(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy):
        from acai.worker.app import create_worker_app

        tool_router = MagicMock()
        mock_registry.router.return_value = tool_router

        extra = [
            patch("acai.worker.app.register_with_orchestrator", return_value="w-test"),
            patch("acai.worker.app.HealthReporter"),
            patch("acai.worker.app._setup_telemetry"),
            patch("acai.worker.app.threading.Thread"),
        ]
        with _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy, extra):
            app, sio, llm = create_worker_app(config, extern_llm=True)

        assert app is not None
        assert llm is mock_llm_server


# ---------------------------------------------------------------------------
# Tests: LLM complete — additional error/edge cases
# ---------------------------------------------------------------------------


class TestLLMCompleteEdgeCases:
    """Cover remaining error paths and edge cases in llm/complete."""

    def test_provider_override_with_model_slug(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy):
        """When provider override includes a model slug, it reorders models list."""
        from acai.worker.app import create_worker_router

        alt_provider = _make_provider(name="openai", model="gpt-4", backend="api")
        target_model = MagicMock()
        target_model.slug = "gpt-4-turbo"
        alt_provider.get_model.return_value = target_model
        alt_provider.models = [MagicMock(slug="gpt-4"), target_model]
        config.get_provider.return_value = alt_provider

        llm_mock_local = MagicMock()
        llm_mock_local.stream.return_value = [
            ContentToken(text="turbo"),
            StreamDone(),
        ]

        extra = [patch("acai.worker.app.create_llm", return_value=llm_mock_local)]
        stack = _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy, extra)
        with stack:
            router, _, _, _ = create_worker_router(config)
            app = FastAPI()
            app.include_router(router)
            cl = TestClient(app)

            resp = cl.post("/worker/llm/complete", json={
                "messages": [{"role": "user", "content": "Hi"}],
                "task_id": "edge-1",
                "provider": {"name": "openai", "model": "gpt-4-turbo"},
            })

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert events[0]["data"]["token"] == "turbo"
        assert alt_provider.models[0] is target_model

    def test_stream_error_response_json_fails_falls_back_to_text(
        self, streaming_client, llm_mock
    ):
        """When error has a response whose .json() raises, fallback to .text."""
        exc = RuntimeError("upstream failure")
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.text = "502 Bad Gateway - upstream server unavailable"
        exc.response = mock_resp
        llm_mock.stream.side_effect = exc

        resp = streaming_client.post("/worker/llm/complete", json={
            "messages": [{"role": "user", "content": "Hi"}],
            "task_id": "edge-2",
        })
        events = _parse_sse(resp.text)
        err_event = next(e for e in events if e["event"] == "error")
        assert "502 Bad Gateway" in err_event["data"]["error"]
        assert "upstream failure" in err_event["data"]["error"]

    def test_stream_error_response_empty_detail(self, streaming_client, llm_mock):
        """When error response .json() returns empty/falsy, only base error used."""
        exc = RuntimeError("timeout")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        exc.response = mock_resp
        llm_mock.stream.side_effect = exc

        resp = streaming_client.post("/worker/llm/complete", json={
            "messages": [{"role": "user", "content": "Hi"}],
            "task_id": "edge-3",
        })
        events = _parse_sse(resp.text)
        err_event = next(e for e in events if e["event"] == "error")
        assert "timeout" in err_event["data"]["error"]
        # Empty detail should not append " — "
        assert "—" not in err_event["data"]["error"]

    def test_stream_error_llm_server_crashed(
        self, config, mock_registry, mock_sandbox_proxy
    ):
        """When LLM server process has died during inference, report crash."""
        from acai.worker.app import create_worker_router

        llm_server = MagicMock()
        llm_server.is_running.return_value = True
        llm_server.managed = True
        process_mock = MagicMock()
        process_mock.poll.return_value = 1  # non-None means process exited
        llm_server.process = process_mock
        llm_server.read_log.return_value = "CUDA error: out of memory\nSegfault"
        llm_server.latest_log_path.return_value = "/tmp/llm.log"
        llm_server.pid = 9999

        llm_mock_local = MagicMock()
        llm_mock_local.stream.side_effect = RuntimeError("connection lost")

        extra = [patch("acai.worker.app.create_llm", return_value=llm_mock_local)]
        stack = _patch_worker_deps(mock_registry, llm_server, mock_sandbox_proxy, extra)
        with stack:
            router, _, _, _ = create_worker_router(config, extern_llm=False)
            app = FastAPI()
            app.include_router(router)
            cl = TestClient(app)

            resp = cl.post("/worker/llm/complete", json={
                "messages": [{"role": "user", "content": "Hi"}],
                "task_id": "edge-4",
            })

        events = _parse_sse(resp.text)
        err_event = next(e for e in events if e["event"] == "error")
        assert "LLM server crashed" in err_event["data"]["error"]
        assert "CUDA error" in err_event["data"]["error"]

    def test_stream_error_no_response_attr(self, streaming_client, llm_mock):
        """Plain exception without .response attribute gives clean error."""
        llm_mock.stream.side_effect = ValueError("invalid prompt format")

        resp = streaming_client.post("/worker/llm/complete", json={
            "messages": [{"role": "user", "content": "Hi"}],
            "task_id": "edge-5",
        })
        events = _parse_sse(resp.text)
        err_event = next(e for e in events if e["event"] == "error")
        assert "invalid prompt format" in err_event["data"]["error"]
        assert err_event["data"]["task_id"] == "edge-5"


# ---------------------------------------------------------------------------
# Tests: register_with_orchestrator — additional error paths
# ---------------------------------------------------------------------------


class TestRegisterEdgeCases:
    def test_generic_exception_retries(self):
        """Non-ConnectionError exceptions are retried and logged."""
        from acai.worker.app import register_with_orchestrator

        with patch("acai.worker.app.http.post") as mock_post:
            ok_resp = MagicMock()
            ok_resp.status_code = 200
            ok_resp.json.return_value = {"worker_id": "w-ok"}

            mock_post.side_effect = [
                OSError("DNS resolution failed"),
                ok_resp,
            ]

            wid = register_with_orchestrator(
                "http://localhost:5050/agent", "http://localhost:5051/worker",
                retry_interval=0.01, max_retries=3,
            )

        assert wid == "w-ok"
        assert mock_post.call_count == 2

    def test_generic_exception_exhausts_retries(self):
        """Repeated generic exceptions exhaust retries and return empty."""
        from acai.worker.app import register_with_orchestrator

        with patch("acai.worker.app.http.post") as mock_post:
            mock_post.side_effect = TimeoutError("timed out")

            wid = register_with_orchestrator(
                "http://localhost:5050/agent", "http://localhost:5051/worker",
                retry_interval=0.01, max_retries=2,
            )

        assert wid == ""
        assert mock_post.call_count == 2

    def test_url_trailing_slash_stripped(self):
        """Trailing slash in orchestrator URL is stripped for proper path join."""
        from acai.worker.app import register_with_orchestrator

        with patch("acai.worker.app.http.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"worker_id": "w-slash"}
            mock_post.return_value = mock_resp

            register_with_orchestrator(
                "http://localhost:5050/agent/", "http://localhost:5051/worker",
                retry_interval=0.01,
            )

        url_called = mock_post.call_args[0][0]
        assert url_called == "http://localhost:5050/agent/workers/register"

    def test_missing_worker_id_in_response(self):
        """If orchestrator returns no worker_id, function returns empty string."""
        from acai.worker.app import register_with_orchestrator

        with patch("acai.worker.app.http.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {}
            mock_post.return_value = mock_resp

            wid = register_with_orchestrator(
                "http://localhost:5050/agent", "http://localhost:5051/worker",
                retry_interval=0.01,
            )

        assert wid == ""


# ---------------------------------------------------------------------------
# Tests: HealthReporter — additional error/edge cases
# ---------------------------------------------------------------------------


class TestHealthReporterEdgeCases:
    def test_init_observer_success(self):
        """_init_observer sets self._observer on success."""
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-5")
        mock_monitor = MagicMock(return_value={"cpu": 10})

        with patch("acai.worker.system_monitor.throttled_monitor", return_value=mock_monitor):
            reporter._init_observer()

        assert reporter._observer is mock_monitor

    def test_init_observer_failure(self):
        """_init_observer handles ImportError gracefully."""
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-6")

        with patch.dict("sys.modules", {"acai.worker.system_monitor": None}):
            reporter._init_observer()

        assert reporter._observer is None

    def test_connect_success(self):
        """_connect establishes websocket connection."""
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-7")

        mock_client = MagicMock()
        mock_sio_pkg = MagicMock()
        mock_sio_pkg.Client.return_value = mock_client

        with patch.dict("sys.modules", {"socketio": mock_sio_pkg}):
            reporter._connect()

        assert reporter._sio is mock_client
        mock_client.connect.assert_called_once_with(
            "http://localhost:5050", transports=["websocket"]
        )

    def test_connect_failure_sets_sio_none(self):
        """_connect sets _sio to None when connection fails."""
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-8")

        mock_sio_pkg = MagicMock()
        mock_sio_pkg.Client.side_effect = Exception("no socketio")

        with patch.dict("sys.modules", {"socketio": mock_sio_pkg}):
            reporter._connect()

        assert reporter._sio is None

    def test_observer_exception_during_heartbeat(self):
        """If observer raises, heartbeat still sends with empty telemetry."""
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-9", interval=60)
        reporter._sio = MagicMock()
        reporter._sio.connected = True
        reporter._observer = MagicMock(side_effect=RuntimeError("GPU query failed"))

        reporter._send_heartbeat()

        reporter._sio.emit.assert_called_once_with(
            "worker_heartbeat",
            {"worker_id": "w-9", "telemetry": {}},
        )

    def test_http_heartbeat_failure(self):
        """HTTP heartbeat failure is handled gracefully (no exception raised)."""
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-10", interval=60)
        reporter._sio = None
        reporter._observer = None

        with patch("acai.worker.app.http.post", side_effect=Exception("network down")):
            reporter._send_heartbeat()

    def test_disconnect_on_stop(self):
        """When run() loop ends, sio.disconnect() is called."""
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-11", interval=0.01)
        reporter._sio = MagicMock()
        reporter._sio.connected = True
        reporter._stop.set()

        with patch.object(reporter, "_init_observer"), \
             patch.object(reporter, "_connect"):
            reporter.run()

        reporter._sio.disconnect.assert_called_once()

    def test_disconnect_exception_on_stop(self):
        """If sio.disconnect() raises, it's silenced."""
        from acai.worker.app import HealthReporter

        reporter = HealthReporter("http://localhost:5050/agent", "w-12", interval=0.01)
        reporter._sio = MagicMock()
        reporter._sio.disconnect.side_effect = Exception("already disconnected")
        reporter._stop.set()

        with patch.object(reporter, "_init_observer"), \
             patch.object(reporter, "_connect"):
            reporter.run()


# ---------------------------------------------------------------------------
# Tests: create_worker_app — additional paths
# ---------------------------------------------------------------------------


class TestCreateWorkerAppEdgeCases:
    def test_existing_socketio_uses_init_app(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy):
        """When socketio is passed in, init_app is called instead of creating new."""
        from acai.worker.app import create_worker_app

        existing_sio = MagicMock()
        mock_registry.router.return_value = MagicMock()

        extra = [
            patch("acai.worker.app.register_with_orchestrator", return_value="w-ext"),
            patch("acai.worker.app.HealthReporter"),
            patch("acai.worker.app._setup_telemetry"),
            patch("acai.worker.app.threading.Thread"),
        ]
        with _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy, extra):
            app, sio, llm = create_worker_app(config, socketio=existing_sio)

        existing_sio.init_app.assert_called_once()
        assert sio is existing_sio

    def test_register_and_report_background_thread(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy):
        """The background registration thread runs and connects health reporter."""
        from acai.worker.app import create_worker_app

        mock_registry.router.return_value = MagicMock()

        extra = [
            patch("acai.worker.app.register_with_orchestrator", return_value="w-bg"),
            patch("acai.worker.app.HealthReporter"),
            patch("acai.worker.app._setup_telemetry"),
            patch("acai.worker.app.threading.Thread"),
        ]
        with _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy, extra):
            with patch("acai.worker.app.threading.Thread") as mock_thread:
                mock_thread_instance = MagicMock()
                mock_thread.return_value = mock_thread_instance
                app, sio, llm = create_worker_app(config)

        mock_thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()

    def test_register_and_report_no_worker_id_skips_reporter(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy):
        """If registration fails (empty worker_id), HealthReporter is not started."""
        from acai.worker.app import create_worker_app

        mock_registry.router.return_value = MagicMock()

        extra = [
            patch("acai.worker.app._setup_telemetry"),
        ]

        with _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy, extra):
            with patch("acai.worker.app.register_with_orchestrator", return_value="") as mock_reg, \
                 patch("acai.worker.app.HealthReporter") as mock_reporter, \
                 patch("acai.worker.app.threading.Thread") as mock_thread:
                # Make thread execute its target immediately
                def run_target(*args, **kwargs):
                    t = MagicMock()
                    target = kwargs.get("target")
                    if target:
                        target()
                    return t
                mock_thread.side_effect = run_target

                app, sio, llm = create_worker_app(config)

        mock_reporter.assert_not_called()

    def test_register_and_report_success_runs_health_reporter(self, config, mock_registry, mock_llm_server, mock_sandbox_proxy):
        """When registration succeeds, HealthReporter is instantiated and run."""
        from acai.worker.app import create_worker_app

        mock_registry.router.return_value = MagicMock()

        extra = [
            patch("acai.worker.app._setup_telemetry"),
        ]

        mock_reporter_instance = MagicMock()

        with _patch_worker_deps(mock_registry, mock_llm_server, mock_sandbox_proxy, extra):
            with patch("acai.worker.app.register_with_orchestrator", return_value="w-success"), \
                 patch("acai.worker.app.HealthReporter", return_value=mock_reporter_instance) as mock_reporter_cls, \
                 patch("acai.worker.app.threading.Thread") as mock_thread:
                def run_target(*args, **kwargs):
                    t = MagicMock()
                    target = kwargs.get("target")
                    if target:
                        target()
                    return t
                mock_thread.side_effect = run_target

                app, sio, llm = create_worker_app(config)

        mock_reporter_cls.assert_called_once()
        mock_reporter_instance.run.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _setup_telemetry
# ---------------------------------------------------------------------------


class TestSetupTelemetry:
    def test_telemetry_not_available_emits_error(self):
        """When observer is None, telemetry_error is emitted."""
        from acai.worker.app import _setup_telemetry

        mock_sio = MagicMock()
        handlers = {}

        def capture_on(event):
            def decorator(fn):
                handlers[event] = fn
                return fn
            return decorator

        mock_sio.on = capture_on

        with patch("acai.worker.app.emit") as mock_emit:
            _setup_telemetry(mock_sio)

            assert "request_telemetry" in handlers
            handlers["request_telemetry"]()

            mock_emit.assert_called_with("telemetry_error", {"error": "not available yet"})

    def test_telemetry_observer_success(self):
        """When observer works, telemetry data is emitted."""
        from acai.worker.app import _setup_telemetry

        mock_sio = MagicMock()
        handlers = {}

        def capture_on(event):
            def decorator(fn):
                handlers[event] = fn
                return fn
            return decorator

        mock_sio.on = capture_on

        mock_monitor = MagicMock(return_value={"gpu_util": 80, "mem_used": 4096})

        with patch("acai.worker.app.emit") as mock_emit, \
             patch("acai.worker.app.threading.Thread") as mock_thread:
            # Capture the _init_observer target and execute it immediately
            def run_init(*args, **kwargs):
                t = MagicMock()
                target = kwargs.get("target")
                if target:
                    target()
                return t
            mock_thread.side_effect = run_init

            with patch("acai.worker.system_monitor.throttled_monitor", return_value=mock_monitor):
                _setup_telemetry(mock_sio)

            assert "request_telemetry" in handlers
            handlers["request_telemetry"]()

            mock_emit.assert_called_with("telemetry", {"gpu_util": 80, "mem_used": 4096})

    def test_telemetry_observer_exception_emits_error(self):
        """When observer raises, telemetry_error is emitted with message."""
        from acai.worker.app import _setup_telemetry

        mock_sio = MagicMock()
        handlers = {}

        def capture_on(event):
            def decorator(fn):
                handlers[event] = fn
                return fn
            return decorator

        mock_sio.on = capture_on

        failing_monitor = MagicMock(side_effect=RuntimeError("GPU driver error"))

        with patch("acai.worker.app.emit") as mock_emit, \
             patch("acai.worker.app.threading.Thread") as mock_thread:
            def run_init(*args, **kwargs):
                t = MagicMock()
                target = kwargs.get("target")
                if target:
                    target()
                return t
            mock_thread.side_effect = run_init

            with patch("acai.worker.system_monitor.throttled_monitor", return_value=failing_monitor):
                _setup_telemetry(mock_sio)

            handlers["request_telemetry"]()
            mock_emit.assert_called_with("telemetry_error", {"error": "GPU driver error"})


# ---------------------------------------------------------------------------
# Tests: switch-model edge cases
# ---------------------------------------------------------------------------


class TestSwitchModelEdgeCases:
    def test_switch_model_not_managed_no_restart(self, config, mock_registry, mock_sandbox_proxy):
        """Non-managed LLM server doesn't trigger server restart."""
        from acai.worker.app import create_worker_router

        llm_server = MagicMock()
        llm_server.is_running.return_value = True
        llm_server.managed = False
        llm_server.pid = 1234
        llm_server.latest_log_path.return_value = "/tmp/llm.log"

        with _patch_worker_deps(mock_registry, llm_server, mock_sandbox_proxy):
            router, _, _, _ = create_worker_router(config, extern_llm=False)

        app = FastAPI()
        app.include_router(router)
        cl = TestClient(app)

        with patch("acai.provider.ProviderConfig.from_dict") as mock_from_dict:
            new_prov = _make_provider(name="api-only", model="gpt-4", backend="api")
            new_prov.managed = False
            mock_from_dict.return_value = new_prov

            resp = cl.post("/worker/switch-model", json={
                "name": "api-only", "model": "gpt-4",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        llm_server.stop.assert_called_once()
        llm_server.start.assert_not_called()


# ---------------------------------------------------------------------------
# SSE parsing helper
# ---------------------------------------------------------------------------


def _parse_sse(text: str) -> list[dict]:
    """Parse SSE text into a list of {event, data} dicts."""
    events = []
    current_event = None
    current_data = None

    for line in text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            current_data = line[6:]
        elif line == "" and current_event is not None:
            try:
                events.append({"event": current_event, "data": json.loads(current_data)})
            except (json.JSONDecodeError, TypeError):
                events.append({"event": current_event, "data": current_data})
            current_event = None
            current_data = None

    # Handle last event if no trailing newline
    if current_event is not None and current_data is not None:
        try:
            events.append({"event": current_event, "data": json.loads(current_data)})
        except (json.JSONDecodeError, TypeError):
            events.append({"event": current_event, "data": current_data})

    return events
