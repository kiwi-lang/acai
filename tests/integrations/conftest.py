"""Shared fixtures for integration tests that require a running vLLM instance.

Environment variables:
    VLLM_ENDPOINT : str — base URL of the vLLM server (default: http://localhost:5103)
    VLLM_MODEL    : str — model slug to use (auto-detected from /v1/models if empty)
    VLLM_API_KEY  : str — optional API key

These tests are skipped automatically when the vLLM instance is unreachable.
Run with::

    python -m pytest tests/integrations/ -v -s
"""

from __future__ import annotations

import os
import time

import pytest
import requests

VLLM_ENDPOINT = os.environ.get("VLLM_ENDPOINT", "http://localhost:5103")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "")
VLLM_API_KEY = os.environ.get("VLLM_API_KEY", "")


def _vllm_available() -> bool:
    """Return True if the vLLM instance is reachable."""
    try:
        r = requests.get(f"{VLLM_ENDPOINT}/v1/models", timeout=5)
        return r.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False


def _detect_model() -> str:
    """Auto-detect the first available model from the vLLM instance."""
    if VLLM_MODEL:
        return VLLM_MODEL
    try:
        r = requests.get(f"{VLLM_ENDPOINT}/v1/models", timeout=5)
        data = r.json().get("data", [])
        if data:
            return data[0]["id"]
    except Exception:
        pass
    return "default"


requires_vllm = pytest.mark.skipif(
    not _vllm_available(),
    reason=f"vLLM instance not available at {VLLM_ENDPOINT}",
)


@pytest.fixture(scope="session")
def vllm_endpoint():
    """Base URL of the running vLLM instance."""
    return VLLM_ENDPOINT


@pytest.fixture(scope="session")
def vllm_model():
    """Model slug served by the vLLM instance."""
    return _detect_model()


@pytest.fixture(scope="session")
def vllm_api_key():
    return VLLM_API_KEY


@pytest.fixture(scope="session")
def provider_config(vllm_endpoint, vllm_model, vllm_api_key):
    """A ProviderConfig wired to the live vLLM instance."""
    from acai.provider.config import ModelConfig, ProviderConfig
    return ProviderConfig(
        name="test-vllm",
        backend="vllm",
        endpoint=vllm_endpoint,
        api_key=vllm_api_key,
        max_tokens=2048,
        context_window=32768,
        temperature=0.1,
        models=[ModelConfig(name=vllm_model, slug=vllm_model)],
    )


@pytest.fixture(scope="session")
def llm(provider_config):
    """A VLLMAdapter connected to the live instance."""
    from acai.provider.registry import create_llm
    return create_llm(provider_config)


@pytest.fixture()
def workspace(tmp_path):
    """Create a temporary workspace with required directory structure."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "store").mkdir()
    (ws / "knowledge").mkdir()
    (ws / "skills").mkdir()
    (ws / "agents").mkdir()
    (ws / "workflows").mkdir()
    (ws / "projects").mkdir()
    return str(ws)


@pytest.fixture()
def acai_config(workspace, provider_config):
    """An AcaiConfig pointed at the temp workspace with the live provider."""
    from acai.orchestrator.config import AcaiConfig
    return AcaiConfig(
        workspace=workspace,
        providers=[provider_config],
    )


@pytest.fixture(scope="session")
def worker_app(provider_config):
    """Create a FastAPI app with the worker routes (in-process, no server start).

    Uses the live vLLM instance as an external LLM (extern_llm=True).
    """
    from fastapi import FastAPI
    from acai.orchestrator.config import AcaiConfig

    cfg = AcaiConfig(workspace="/tmp/acai-integration-test", providers=[provider_config])

    from acai.worker.app import create_worker_router
    app = FastAPI()
    router, _llm_server, _registry, _sandbox = create_worker_router(
        cfg, prefix="", extern_llm=True,
    )
    app.include_router(router)
    return app


@pytest.fixture(scope="session")
def worker_url(worker_app):
    """Start the worker app on a random port and return its URL.

    Uses uvicorn in a background thread for the duration of the test.
    """
    if not _vllm_available():
        pytest.skip("vLLM not available")

    import socket
    import threading
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(worker_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to start
    import time
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            import requests as _r
            resp = _r.get(f"http://127.0.0.1:{port}/worker/status", timeout=1)
            if resp.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)

    url = f"http://127.0.0.1:{port}"
    yield url

    server.should_exit = True
    thread.join(timeout=3)
