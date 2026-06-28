"""Worker app — executes LLM completions and tool calls.

The worker exposes:

* ``POST /llm/complete`` — call the LLM and stream results as SSE.
* ``GET /worker/logs`` — read latest vLLM server log.
* Tool registry at ``/tools`` (``GET /tools/list``, ``POST /tools/call``).
* ``GET /worker/status`` — capabilities + LLM server status.
* ``GET /worker/sandbox/status`` — check sandbox state.
* Telemetry via SocketIO (``request_telemetry`` → ``telemetry``).

When a tool call's context carries ``"uses_sandbox": true``, the
worker starts a sandbox lazily (using the system-wide
``SandboxConfig``) and proxies tools annotated with
``sandbox=True`` to the sandbox's ``acai mcp`` endpoint.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import TYPE_CHECKING

import requests as http
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response, StreamingResponse

from acai.orchestrator.compat import SocketIO, emit

from acai.provider import (
    ContentToken, LLMServer, LLMServerError, ReasoningToken, StreamDone,
    ToolCallDelta, create_llm,
)
from acai.orchestrator.tools import ToolRegistry, discover_tools

if TYPE_CHECKING:
    from acai.orchestrator.config import AcaiConfig

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Helper: read JSON body
# ------------------------------------------------------------------

async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


# ------------------------------------------------------------------
# Worker router
# ------------------------------------------------------------------

def create_worker_router(
    config: AcaiConfig,
    prefix: str = "/worker",
    extern_llm: bool = False,
) -> tuple[APIRouter, LLMServer, ToolRegistry, "SandboxProxy"]:
    """Build the worker APIRouter.

    The ``/llm/complete`` endpoint streams results as SSE.

    When *extern_llm* is ``True`` the worker will never start/stop the
    LLM server — it assumes an externally managed instance is running
    at the configured endpoint.

    Returns ``(router, llm_server, registry, sandbox_proxy)``.
    """
    from acai.worker.sandbox_proxy import SandboxProxy

    router = APIRouter(prefix=prefix, tags=["worker"])

    provider = config.active_provider()
    local_prov = config.local_provider()
    llm_server = LLMServer(local_prov or provider, workspace=config.workspace)
    registry = discover_tools()
    sandbox_proxy = SandboxProxy(config.sandbox, sandbox_predicate=registry.is_sandboxed)

    from acai.tools.meta import _configure as configure_meta_tools

    configure_meta_tools(registry)

    import os
    from acai.orchestrator.skill_store import SkillStore
    from acai.tools.skills import _configure as configure_skills

    skills_dir = os.path.join(config.workspace, "skills")
    skill_store = SkillStore(skills_dir)
    skill_store.register_all(registry)
    configure_skills(skill_store)

    from acai.tools.ci import _configure as configure_ci
    configure_ci(config.ci)

    log.info(
        "worker router created  model=%s  backend=%s  tools=%d  extern_llm=%s",
        provider.model, provider.backend, len(registry.all_tools()), extern_llm,
    )

    # ------------------------------------------------------------------
    # POST /llm/complete  (SSE stream)
    # ------------------------------------------------------------------

    def _sse_event(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    @router.post("/llm/complete")
    async def llm_complete(request: Request):
        body = await _json_body(request)
        messages = body.get("messages", [])
        tools = body.get("tools")
        task_id = body.get("task_id", "")
        provider_override = body.get("provider")
        enable_thinking = body.get("enable_thinking")
        response_format = body.get("response_format")

        log.info(
            "[%s] llm/complete  messages=%d  tools=%s  provider=%s",
            task_id, len(messages), bool(tools),
            provider_override.get("name") if isinstance(provider_override, dict) else None,
        )

        # Pre-dispatch size guard — reject obviously oversized requests
        # before they hit vLLM and potentially OOM the GPU
        try:
            from acai.utils.tokens import fits_context, estimate_payload_tokens
            ctx_window = provider.context_window or 128000
            max_out = provider.max_tokens or 4096
            fits, est_tokens, avail = fits_context(body, ctx_window, max_out)
            if not fits:
                log.error(
                    "[%s] REJECTED: payload ~%d tokens exceeds budget %d "
                    "(context_window=%d, max_output=%d)",
                    task_id, est_tokens, avail, ctx_window, max_out,
                )
                return Response(
                    content=_sse_event("error", {
                        "task_id": task_id,
                        "error": (
                            f"Request too large: ~{est_tokens} input tokens "
                            f"exceeds available budget of {avail} tokens "
                            f"(context_window={ctx_window}, reserved_output={max_out}). "
                            f"The conversation needs to be compressed or truncated."
                        ),
                    }),
                    status_code=413,
                    media_type="text/event-stream",
                )
            log.debug("[%s] token budget OK: ~%d / %d", task_id, est_tokens, avail)
        except Exception:
            log.debug("[%s] token budget check skipped", task_id, exc_info=True)

        if isinstance(provider_override, dict) and provider_override.get("name"):
            resolved = config.get_provider(provider_override["name"])
            if resolved:
                model_slug_override = provider_override.get("model", "")
                if model_slug_override and resolved.get_model(model_slug_override):
                    target_model = resolved.get_model(model_slug_override)
                    resolved.models = [target_model] + [
                        m for m in resolved.models if m.slug != model_slug_override
                    ]
                llm_cfg = resolved
                use_local = False
            else:
                log.warning("[%s] provider %r not found, using local",
                            task_id, provider_override["name"])
                llm_cfg = provider
                use_local = True
        else:
            llm_cfg = provider
            use_local = True

        if use_local and not extern_llm and not llm_server.is_running() and llm_server.managed:
            log.info("[%s] starting LLM server (not running)", task_id)
            try:
                llm_server.start()
            except LLMServerError as exc:
                log.error("[%s] LLM server failed to start: %s", task_id, exc)
                return Response(
                    content=_sse_event("error", {"task_id": task_id, "error": str(exc)}),
                    status_code=503,
                    media_type="text/event-stream",
                )

        llm = create_llm(llm_cfg)

        stream_kwargs: dict = {}
        if tools:
            stream_kwargs["tools"] = tools
        if enable_thinking is not None:
            stream_kwargs["enable_thinking"] = enable_thinking
        if response_format:
            stream_kwargs["response_format"] = response_format

        def generate():
            idx = 0
            try:
                for event in llm.stream(messages, **stream_kwargs):
                    if isinstance(event, ReasoningToken):
                        yield _sse_event("reasoning", {
                            "task_id": task_id,
                            "token": event.text,
                            "index": idx,
                        })
                        idx += 1
                    elif isinstance(event, ContentToken):
                        yield _sse_event("token", {
                            "task_id": task_id,
                            "token": event.text,
                            "index": idx,
                        })
                        idx += 1
                    elif isinstance(event, ToolCallDelta):
                        yield _sse_event("tool_call_delta", {
                            "task_id": task_id,
                            "index": event.index,
                            "id": event.id,
                            "name": event.name,
                            "arguments": event.arguments,
                        })
                    elif isinstance(event, StreamDone):
                        yield _sse_event("done", {
                            "task_id": task_id,
                            "output_tokens": idx,
                        })
                        log.info("[%s] llm/complete done  tokens=%d", task_id, idx)

            except Exception as exc:
                log.exception("[%s] llm/complete failed", task_id)
                err_msg = str(exc)
                resp = getattr(exc, "response", None)
                if resp is not None:
                    try:
                        detail = resp.json()
                    except Exception:
                        detail = resp.text[:2000] if hasattr(resp, "text") else ""
                    if detail:
                        err_msg = f"{err_msg} — {detail}"
                if not extern_llm and llm_server.process is not None and llm_server.process.poll() is not None:
                    err_msg = f"LLM server crashed during inference. {llm_server.read_log(tail=30)}"
                yield _sse_event("error", {"task_id": task_id, "error": err_msg})

        return StreamingResponse(generate(), media_type="text/event-stream")

    # ------------------------------------------------------------------
    # POST /worker/switch-model
    # ------------------------------------------------------------------

    @router.post("/switch-model")
    async def switch_model(request: Request):
        from acai.provider import ProviderConfig

        data = await _json_body(request)
        new_prov = ProviderConfig.from_dict(data)

        if not extern_llm and llm_server.is_running():
            log.info("switch-model: stopping current LLM server")
            llm_server.stop()

        llm_server.config = new_prov
        config.set_active(new_prov.name)

        if not extern_llm and llm_server.managed:
            log.info("switch-model: starting LLM server for %s", new_prov.model)
            try:
                llm_server.start()
            except LLMServerError as exc:
                return JSONResponse({"error": str(exc)}, status_code=503)

        log.info("switch-model: now using %s (%s)", new_prov.slug, new_prov.backend)
        return {"ok": True, "model": new_prov.model, "slug": new_prov.slug}

    # ------------------------------------------------------------------
    # GET /worker/logs
    # ------------------------------------------------------------------

    @router.get("/logs")
    def worker_logs(request: Request):
        tail = int(request.query_params.get("tail", "200"))
        content = llm_server.read_log(tail=tail)
        log_path = llm_server.latest_log_path() or "(none)"
        return {"path": log_path, "content": content}

    # ------------------------------------------------------------------
    # GET /worker/status
    # ------------------------------------------------------------------

    @router.get("/status")
    def worker_status():
        active = config.active_provider()
        ctx_window = active.context_window or 128000
        # Try to get the real context window from vLLM
        detected = _detect_context_window(active)
        if detected and detected != ctx_window:
            ctx_window = detected
        return {
            "telemetry": True,
            "tools": [td.qualified_name for td in registry.all_tools()],
            "namespaces": registry.namespaces(),
            "llm_running": llm_server.is_running(),
            "llm_pid": llm_server.pid,
            "llm_model": active.model,
            "llm_backend": active.backend,
            "extern_llm": extern_llm,
            "log_path": llm_server.latest_log_path(),
            "context_window": ctx_window,
            "max_tokens": active.max_tokens or 4096,
        }

    # ------------------------------------------------------------------
    # GET /worker/sandbox/status
    # ------------------------------------------------------------------

    @router.get("/sandbox/status")
    def sandbox_status():
        return {
            "running": sandbox_proxy.running,
            "endpoint": sandbox_proxy.endpoint,
        }

    return router, llm_server, registry, sandbox_proxy


def _detect_context_window(provider_cfg) -> int | None:
    """Query the vLLM/OpenAI-compatible server for the real max_model_len.

    Returns the detected context window or None if unavailable.
    Uses the /v1/models endpoint which vLLM populates with max_model_len.
    """
    try:
        endpoint = provider_cfg.endpoint.rstrip("/")
        resp = http.get(f"{endpoint}/v1/models", timeout=3)
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", [])
        if not data:
            return None
        # vLLM reports max_model_len in the model info
        model_info = data[0]
        max_model_len = model_info.get("max_model_len")
        if max_model_len and isinstance(max_model_len, int) and max_model_len > 0:
            return max_model_len
    except Exception:
        pass
    return None


# Keep the old name as an alias for backward compat with CLI imports
create_worker_blueprint = create_worker_router


# ------------------------------------------------------------------
# Orchestrator registration + health
# ------------------------------------------------------------------

def register_with_orchestrator(
    orchestrator_url: str,
    worker_url: str,
    *,
    capabilities: dict | None = None,
    retry_interval: float = 5.0,
    max_retries: int = 12,
) -> str:
    """POST to ``/workers/register`` on the orchestrator.

    Retries on connection errors so the worker can start before the
    orchestrator is fully up.  Returns the ``worker_id`` assigned by
    the orchestrator.
    """
    url = f"{orchestrator_url.rstrip('/')}/workers/register"
    payload = {
        "url": worker_url,
        "capabilities": capabilities or {},
    }

    for attempt in range(1, max_retries + 1):
        try:
            resp = http.post(url, json=payload, timeout=10)
            if resp.status_code < 300:
                data = resp.json()
                worker_id = data.get("worker_id", "")
                log.info(
                    "registered with orchestrator  id=%s  url=%s",
                    worker_id, orchestrator_url,
                )
                return worker_id
            log.warning(
                "registration returned %d (attempt %d/%d)",
                resp.status_code, attempt, max_retries,
            )
        except http.ConnectionError:
            log.debug(
                "orchestrator not reachable (attempt %d/%d), retrying in %.0fs",
                attempt, max_retries, retry_interval,
            )
        except Exception:
            log.exception("registration error (attempt %d/%d)", attempt, max_retries)

        time.sleep(retry_interval)

    log.error("failed to register with orchestrator after %d attempts", max_retries)
    return ""


class HealthReporter:
    """Background thread that sends periodic telemetry to the orchestrator.

    Connects via WebSocket (SocketIO) and emits ``worker_heartbeat``
    events containing system metrics.
    """

    def __init__(
        self,
        orchestrator_url: str,
        worker_id: str,
        interval: float = 10.0,
    ):
        self.orchestrator_url = orchestrator_url.rstrip("/")
        self.worker_id = worker_id
        self.interval = interval
        self._stop = threading.Event()
        self._sio = None
        self._observer = None

    def run(self) -> None:
        self._init_observer()
        self._connect()

        while not self._stop.is_set():
            self._send_heartbeat()
            self._stop.wait(self.interval)

        if self._sio is not None:
            try:
                self._sio.disconnect()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()

    def _init_observer(self) -> None:
        try:
            from acai.worker.system_monitor import throttled_monitor
            self._observer = throttled_monitor()
        except Exception:
            log.debug("system_monitor not available for health reporter")

    def _connect(self) -> None:
        try:
            import socketio as sio_pkg
            self._sio = sio_pkg.Client(
                reconnection=True, logger=False, engineio_logger=False,
            )
            ws_url = self.orchestrator_url.rsplit("/", 1)[0]
            self._sio.connect(ws_url, transports=["websocket"])
            log.info("health reporter connected via WebSocket")
        except Exception:
            log.debug("WebSocket connection failed, heartbeat via HTTP")
            self._sio = None

    def _send_heartbeat(self) -> None:
        telemetry = {}
        if self._observer is not None:
            try:
                telemetry = self._observer()
            except Exception:
                pass

        payload = {"worker_id": self.worker_id, "telemetry": telemetry}

        if self._sio is not None and self._sio.connected:
            try:
                self._sio.emit("worker_heartbeat", payload)
                return
            except Exception:
                log.debug("WebSocket heartbeat failed, falling back to HTTP")

        try:
            http.post(
                f"{self.orchestrator_url}/workers/heartbeat",
                json=payload,
                timeout=5,
            )
        except Exception:
            log.debug("HTTP heartbeat failed")


# ------------------------------------------------------------------
# Full worker app factory
# ------------------------------------------------------------------

def create_worker_app(config: AcaiConfig, socketio: SocketIO | None = None,
                      extern_llm: bool = False):
    """Create a standalone worker app.

    Returns ``(app, socketio, llm_server)``.

    On startup the worker registers itself with the orchestrator and
    launches a health-reporter background thread.
    """
    app = FastAPI()

    if socketio is None:
        socketio = SocketIO(app, cors_allowed_origins="*")
    else:
        socketio.init_app(app)

    router, llm_server, registry, sandbox_proxy = create_worker_router(
        config, extern_llm=extern_llm,
    )
    tool_router = registry.router(url_prefix="/tools", sandbox_proxy=sandbox_proxy)
    app.include_router(router)
    app.include_router(tool_router)

    _setup_telemetry(socketio)

    worker_url = f"http://127.0.0.1:{config.worker.port}/worker"
    orchestrator_url = config.worker.orchestrator_url

    active = config.active_provider()
    capabilities = {
        "tools": [td.qualified_name for td in registry.all_tools()],
        "namespaces": registry.namespaces(),
        "model": active.model,
        "backend": active.backend,
    }

    def _register_and_report():
        worker_id = register_with_orchestrator(
            orchestrator_url, worker_url,
            capabilities=capabilities,
        )
        if not worker_id:
            return
        reporter = HealthReporter(orchestrator_url, worker_id)
        reporter.run()

    threading.Thread(
        target=_register_and_report, daemon=True, name="worker-health",
    ).start()

    log.info("worker app created  port=%d  extern_llm=%s", config.worker.port, extern_llm)
    return app, socketio, llm_server


# ------------------------------------------------------------------
# Telemetry
# ------------------------------------------------------------------

def _setup_telemetry(socketio: SocketIO):
    _observer = None

    def _init_observer():
        nonlocal _observer
        try:
            from acai.worker.system_monitor import throttled_monitor
            _observer = throttled_monitor()
            log.info("system monitor initialized")
        except Exception:
            log.debug("system_monitor not available", exc_info=True)

    threading.Thread(target=_init_observer, daemon=True, name="telemetry-init").start()

    @socketio.on("request_telemetry")
    def handle_request_telemetry():
        if _observer is None:
            emit("telemetry_error", {"error": "not available yet"})
            return

        try:
            data = _observer()
            emit("telemetry", data)
        except Exception as exc:
            emit("telemetry_error", {"error": str(exc)})
