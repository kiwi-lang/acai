"""Worker Flask app — executes LLM completions and tool calls.

The worker exposes:

* ``POST /llm/complete`` — call the LLM (streaming, emits chunks via SocketIO).
* Tool registry blueprint at ``/tools`` (``GET /tools/list``, ``POST /tools/call``).
* ``GET /worker/status`` — capabilities + LLM server status.
* Telemetry via SocketIO (``request_telemetry`` → ``telemetry``).

A background thread polls the orchestrator for work, dispatches to
its own HTTP endpoints, and pushes results back.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import TYPE_CHECKING

import requests as http
from flask import Blueprint, Flask, jsonify, request as flask_request
from flask_socketio import SocketIO

from assai.agents.llm import LLMServer, OpenAICompatibleLLM, create_llm
from assai.tools.builtins import registry as builtin_registry
from assai.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from assai.core.config import AssaiConfig

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Worker blueprint
# ------------------------------------------------------------------

def create_worker_blueprint(
    config: AssaiConfig,
    socketio: SocketIO | None = None,
    prefix: str = "/worker",
) -> tuple[Blueprint, LLMServer, ToolRegistry]:
    """Build the worker Flask blueprint.

    Returns ``(bp, llm_server, registry)``.
    """
    bp = Blueprint("worker", __name__, url_prefix=prefix)

    llm_server = LLMServer(config.llm)
    registry = ToolRegistry()
    registry.merge(builtin_registry)

    # ------------------------------------------------------------------
    # POST /llm/complete
    # ------------------------------------------------------------------

    @bp.route("/llm/complete", methods=["POST"])
    def llm_complete():
        body = flask_request.get_json(silent=True) or {}
        messages = body.get("messages", [])
        tools = body.get("tools")
        task_id = body.get("task_id", "")

        if not llm_server.is_running() and llm_server.managed:
            llm_server.start()

        llm = create_llm(config.llm)

        accumulated = []
        idx = 0
        for token in llm.stream(messages, tools=tools):
            accumulated.append(token)
            if socketio is not None:
                socketio.emit("chunk", {
                    "task_id": task_id,
                    "token": token,
                    "index": idx,
                })
            idx += 1

        full_text = "".join(accumulated)
        if socketio is not None:
            socketio.emit("stream_end", {"task_id": task_id})

        return jsonify({"result": full_text})

    # ------------------------------------------------------------------
    # GET /worker/status
    # ------------------------------------------------------------------

    @bp.route("/status", methods=["GET"])
    def worker_status():
        return jsonify({
            "telemetry": True,
            "tools": [td.qualified_name for td in registry.all_tools()],
            "namespaces": registry.namespaces(),
            "llm_running": llm_server.is_running(),
            "llm_pid": llm_server.pid,
            "llm_model": config.llm.model,
            "llm_backend": config.llm.backend,
        })

    return bp, llm_server, registry


# ------------------------------------------------------------------
# Background poller
# ------------------------------------------------------------------

class WorkerPoller:
    """Polls the orchestrator for work and dispatches to local endpoints."""

    def __init__(
        self,
        config: AssaiConfig,
        orchestrator_url: str,
        worker_url: str,
        llm_server: LLMServer,
        registry: ToolRegistry,
    ):
        self.config = config
        self.orchestrator_url = orchestrator_url.rstrip("/")
        self.worker_url = worker_url.rstrip("/")
        self.llm_server = llm_server
        self.registry = registry
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:
                log.exception("poller error")
            time.sleep(self.config.queue.poll_interval)

    def stop(self):
        self._stop.set()

    def _poll_once(self):
        try:
            resp = http.get(f"{self.orchestrator_url}/work/pop", timeout=10)
        except http.ConnectionError:
            return

        if resp.status_code == 204:
            return
        if resp.status_code != 200:
            log.warning("work/pop returned %d", resp.status_code)
            return

        work = resp.json()
        task_id = work.get("task_id", "")
        kind = work.get("kind", "")

        if kind == "llm_complete":
            result = self._dispatch_llm(work)
        elif kind == "tool_call":
            result = self._dispatch_tool(work)
        else:
            log.warning("unknown work kind: %s", kind)
            return

        self._push_result(task_id, kind, result, work)

    def _dispatch_llm(self, work: dict) -> str:
        if not self.llm_server.is_running() and self.llm_server.managed:
            self.llm_server.start()

        try:
            resp = http.post(
                f"{self.worker_url}/llm/complete",
                json={
                    "messages": work.get("messages", []),
                    "tools": work.get("tools"),
                    "task_id": work.get("task_id", ""),
                },
                timeout=600,
            )
            return resp.json().get("result", "")
        except Exception as exc:
            log.exception("LLM dispatch failed")
            return f"Error: {exc}"

    def _dispatch_tool(self, work: dict) -> str:
        tool_name = work.get("tool", "")
        args = work.get("args", {})

        td = self.registry.get(tool_name)
        if td is not None and td.gpu and self.llm_server.is_running():
            self.llm_server.stop()

        try:
            resp = http.post(
                f"{self.worker_url}/tools/call",
                json={"tool": tool_name, "args": args},
                timeout=self.config.worker.timeout,
            )
            return resp.json().get("result", "")
        except Exception as exc:
            log.exception("tool dispatch failed")
            return json.dumps({"error": str(exc)})

    def _push_result(self, task_id: str, kind: str, result: str, work: dict):
        try:
            http.post(
                f"{self.orchestrator_url}/work/result/{task_id}",
                json={
                    "result": result,
                    "kind": kind,
                    "project": work.get("project", "_default"),
                    "raw": result,
                },
                timeout=30,
            )
        except Exception:
            log.exception("failed to push result for %s", task_id)


# ------------------------------------------------------------------
# Full worker app factory
# ------------------------------------------------------------------

def create_worker_app(config: AssaiConfig, socketio: SocketIO | None = None):
    """Create a standalone worker Flask app.

    Returns ``(app, socketio, poller, llm_server)``.
    """
    app = Flask(__name__)

    if socketio is None:
        socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    else:
        socketio.init_app(app)

    bp, llm_server, registry = create_worker_blueprint(config, socketio)
    tool_bp = registry.blueprint(url_prefix="/tools")
    app.register_blueprint(bp)
    app.register_blueprint(tool_bp)

    _setup_telemetry(socketio)

    worker_url = f"http://127.0.0.1:{config.worker.port}/worker"
    poller = WorkerPoller(
        config=config,
        orchestrator_url=config.worker.orchestrator_url,
        worker_url=worker_url,
        llm_server=llm_server,
        registry=registry,
    )

    return app, socketio, poller, llm_server


# ------------------------------------------------------------------
# Telemetry
# ------------------------------------------------------------------

def _setup_telemetry(socketio: SocketIO):
    """Register telemetry SocketIO handlers."""
    _observer = None

    @socketio.on("request_telemetry")
    def handle_request_telemetry():
        nonlocal _observer
        if _observer is None:
            try:
                from assai.tools.system_monitor import system_monitor
                _observer = system_monitor()
            except Exception:
                log.debug("system_monitor not available")
                socketio.emit("telemetry_error", {"error": "not available"})
                return

        try:
            data = _observer()
            socketio.emit("telemetry", data)
        except Exception as exc:
            socketio.emit("telemetry_error", {"error": str(exc)})
