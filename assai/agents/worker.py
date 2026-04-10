"""Worker Flask app — executes LLM completions and tool calls.

The worker exposes:

* ``POST /llm/complete`` — call the LLM (streaming, emits chunks via SocketIO).
* ``GET /worker/logs`` — read latest vLLM server log.
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
from flask import Blueprint, Flask, Response, jsonify, request as flask_request
from flask_socketio import SocketIO

from assai.agents.llm import LLMServer, LLMServerError, create_llm
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
    extern_llm: bool = False,
) -> tuple[Blueprint, LLMServer, ToolRegistry]:
    """Build the worker Flask blueprint.

    When *extern_llm* is ``True`` the worker will never start/stop the
    LLM server — it assumes an externally managed instance is running
    at the configured endpoint.

    Returns ``(bp, llm_server, registry)``.
    """
    bp = Blueprint("worker", __name__, url_prefix=prefix)

    llm_server = LLMServer(config.llm, workspace=config.workspace)
    registry = ToolRegistry()
    registry.merge(builtin_registry)

    log.info(
        "worker blueprint created  model=%s  backend=%s  tools=%d  extern_llm=%s",
        config.llm.model, config.llm.backend, len(registry.all_tools()), extern_llm,
    )

    # ------------------------------------------------------------------
    # POST /llm/complete
    # ------------------------------------------------------------------

    @bp.route("/llm/complete", methods=["POST"])
    def llm_complete():
        body = flask_request.get_json(silent=True) or {}
        messages = body.get("messages", [])
        tools = body.get("tools")
        task_id = body.get("task_id", "")

        log.info(
            "[%s] llm/complete  messages=%d  tools=%s",
            task_id, len(messages), bool(tools),
        )

        if not extern_llm and not llm_server.is_running() and llm_server.managed:
            log.info("[%s] starting LLM server (not running)", task_id)
            try:
                llm_server.start()
            except LLMServerError as exc:
                log.error("[%s] LLM server failed to start: %s", task_id, exc)
                _emit_error(socketio, task_id, str(exc))
                return jsonify({"error": str(exc)}), 503

        llm = create_llm(config.llm)

        try:
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
            log.info(
                "[%s] llm/complete done  tokens=%d  chars=%d",
                task_id, idx, len(full_text),
            )
            if socketio is not None:
                socketio.emit("stream_end", {"task_id": task_id})

            return jsonify({"result": full_text})

        except Exception as exc:
            log.exception("[%s] llm/complete failed", task_id)
            err_msg = str(exc)
            if not extern_llm and llm_server.process is not None and llm_server.process.poll() is not None:
                err_msg = f"LLM server crashed during inference. {llm_server.read_log(tail=30)}"
            _emit_error(socketio, task_id, err_msg)
            return jsonify({"error": err_msg}), 502

    # ------------------------------------------------------------------
    # GET /worker/logs
    # ------------------------------------------------------------------

    @bp.route("/logs", methods=["GET"])
    def worker_logs():
        tail = flask_request.args.get("tail", 200, type=int)
        content = llm_server.read_log(tail=tail)
        log_path = llm_server.latest_log_path() or "(none)"
        return Response(
            json.dumps({"path": log_path, "content": content}, ensure_ascii=False),
            mimetype="application/json",
        )

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
            "extern_llm": extern_llm,
            "log_path": llm_server.latest_log_path(),
        })

    return bp, llm_server, registry


def _emit_error(socketio: SocketIO | None, task_id: str, message: str):
    """Emit an error event so the UI can display it to the user."""
    if socketio is not None:
        socketio.emit("stream_error", {
            "task_id": task_id,
            "error": message,
        })
        socketio.emit("stream_end", {"task_id": task_id})


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
        log.info(
            "poller created  orchestrator=%s  worker=%s  poll=%ds",
            self.orchestrator_url, self.worker_url,
            self.config.queue.poll_interval,
        )

    def run(self):
        log.info("poller started")
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:
                log.exception("poller error")
            time.sleep(self.config.queue.poll_interval)

    def stop(self):
        log.info("poller stopping")
        self._stop.set()

    def _poll_once(self):
        try:
            resp = http.get(f"{self.orchestrator_url}/work/pop", timeout=10)
        except http.ConnectionError:
            log.debug("orchestrator unreachable, will retry")
            return

        if resp.status_code == 204:
            log.debug("no work available")
            return
        if resp.status_code != 200:
            log.warning("work/pop returned %d", resp.status_code)
            return

        work = resp.json()
        task_id = work.get("task_id", "")
        kind = work.get("kind", "")
        log.info("[%s] popped work  kind=%s", task_id, kind)

        if kind == "llm_complete":
            result, error = self._dispatch_llm(work)
        elif kind == "tool_call":
            result, error = self._dispatch_tool(work)
        else:
            log.warning("[%s] unknown work kind: %s", task_id, kind)
            return

        self._push_result(task_id, kind, result, work, error=error)

    def _dispatch_llm(self, work: dict) -> tuple[str, str | None]:
        task_id = work.get("task_id", "")
        n_msgs = len(work.get("messages", []))
        log.info("[%s] dispatching llm_complete  messages=%d", task_id, n_msgs)

        try:
            resp = http.post(
                f"{self.worker_url}/llm/complete",
                json={
                    "messages": work.get("messages", []),
                    "tools": work.get("tools"),
                    "task_id": task_id,
                },
                timeout=600,
            )
            body = resp.json()
            if resp.status_code >= 400:
                error = body.get("error", f"HTTP {resp.status_code}")
                log.error("[%s] llm_complete failed: %s", task_id, error)
                return "", error

            result = body.get("result", "")
            log.info("[%s] llm_complete finished  chars=%d", task_id, len(result))
            return result, None
        except Exception as exc:
            log.exception("[%s] LLM dispatch failed", task_id)
            return "", f"LLM dispatch error: {exc}"

    def _dispatch_tool(self, work: dict) -> tuple[str, str | None]:
        task_id = work.get("task_id", "")
        tool_name = work.get("tool", "")
        args = work.get("args", {})
        log.info("[%s] dispatching tool_call  tool=%s  args=%s", task_id, tool_name, list(args.keys()))

        td = self.registry.get(tool_name)
        if td is not None and td.gpu and self.llm_server.is_running():
            log.info("[%s] stopping LLM server for GPU tool %s", task_id, tool_name)
            self.llm_server.stop()

        try:
            resp = http.post(
                f"{self.worker_url}/tools/call",
                json={"tool": tool_name, "args": args},
                timeout=self.config.worker.timeout,
            )
            result = resp.json().get("result", "")
            log.info("[%s] tool_call finished  tool=%s  chars=%d", task_id, tool_name, len(result))
            return result, None
        except Exception as exc:
            log.exception("[%s] tool dispatch failed  tool=%s", task_id, tool_name)
            return json.dumps({"error": str(exc)}), str(exc)

    def _push_result(self, task_id: str, kind: str, result: str, work: dict,
                     error: str | None = None):
        log.info("[%s] pushing result  kind=%s  chars=%d  error=%s",
                 task_id, kind, len(result), bool(error))
        try:
            payload: dict = {
                "result": result,
                "kind": kind,
                "project": work.get("project", "_default"),
                "raw": result,
            }
            if error:
                payload["error"] = error

            resp = http.post(
                f"{self.orchestrator_url}/work/result/{task_id}",
                json=payload,
                timeout=30,
            )
            log.info("[%s] result pushed  status=%d", task_id, resp.status_code)
        except Exception:
            log.exception("[%s] failed to push result", task_id)


# ------------------------------------------------------------------
# Full worker app factory
# ------------------------------------------------------------------

def create_worker_app(config: AssaiConfig, socketio: SocketIO | None = None,
                      extern_llm: bool = False):
    """Create a standalone worker Flask app.

    Returns ``(app, socketio, poller, llm_server)``.
    """
    app = Flask(__name__)

    if socketio is None:
        socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    else:
        socketio.init_app(app)

    bp, llm_server, registry = create_worker_blueprint(
        config, socketio, extern_llm=extern_llm,
    )
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

    log.info("worker app created  port=%d  extern_llm=%s", config.worker.port, extern_llm)
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
