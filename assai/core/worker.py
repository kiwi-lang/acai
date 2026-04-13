"""Worker Flask app — executes LLM completions and tool calls.

The worker exposes:

* ``POST /llm/complete`` — call the LLM and stream results as SSE.
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
import os
import threading
import time
from typing import TYPE_CHECKING

import requests as http
from flask import Blueprint, Flask, Response, jsonify, request as flask_request
from flask_socketio import SocketIO, emit

from assai.core.agent_store import compress_messages
from assai.core.llm import (
    ContentToken, LLMServer, LLMServerError, ReasoningToken, StreamDone,
    ToolCallDelta, create_llm,
)
from assai.core.tools import ToolRegistry, discover_tools

if TYPE_CHECKING:
    from assai.core.config import AssaiConfig

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Worker blueprint
# ------------------------------------------------------------------

def create_worker_blueprint(
    config: AssaiConfig,
    prefix: str = "/worker",
    extern_llm: bool = False,
) -> tuple[Blueprint, LLMServer, ToolRegistry]:
    """Build the worker Flask blueprint.

    The ``/llm/complete`` endpoint streams results as SSE.

    When *extern_llm* is ``True`` the worker will never start/stop the
    LLM server — it assumes an externally managed instance is running
    at the configured endpoint.

    Returns ``(bp, llm_server, registry)``.
    """
    bp = Blueprint("worker", __name__, url_prefix=prefix)

    provider = config.local_provider() or config.active_provider()
    llm_server = LLMServer(provider, workspace=config.workspace)
    registry = discover_tools()
    from assai.tools.meta import _configure as configure_meta_tools

    configure_meta_tools(registry)

    log.info(
        "worker blueprint created  model=%s  backend=%s  tools=%d  extern_llm=%s",
        provider.model, provider.backend, len(registry.all_tools()), extern_llm,
    )

    # ------------------------------------------------------------------
    # POST /llm/complete  (SSE stream)
    # ------------------------------------------------------------------

    def _sse_event(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    @bp.route("/llm/complete", methods=["POST"])
    def llm_complete():
        body = flask_request.get_json(silent=True) or {}
        messages = body.get("messages", [])
        tools = body.get("tools")
        task_id = body.get("task_id", "")
        provider_override = body.get("provider")
        enable_thinking = body.get("enable_thinking")

        log.info(
            "[%s] llm/complete  messages=%d  tools=%s  provider=%s",
            task_id, len(messages), bool(tools),
            provider_override.get("name") if isinstance(provider_override, dict) else None,
        )

        if isinstance(provider_override, dict) and provider_override.get("endpoint"):
            from assai.core.config import ProviderConfig
            override_cfg = ProviderConfig.from_dict(provider_override)
            llm_cfg = override_cfg
            use_local = False
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
                    _sse_event("error", {"task_id": task_id, "error": str(exc)}),
                    status=503,
                    mimetype="text/event-stream",
                )

        llm = create_llm(llm_cfg)

        stream_kwargs: dict = {}
        if tools:
            stream_kwargs["tools"] = tools
        if enable_thinking is not None:
            stream_kwargs["chat_template_kwargs"] = {"enable_thinking": enable_thinking}

        # FIXME(temporary): Qwen3 thinking control via message prefix/suffix
        # while vLLM template kwargs are unreliable.  Remove once upstream is fixed.
        if True:
            if enable_thinking is not None and messages:
                if enable_thinking:
                    prefix = "<think>\n"
                    suffix = "\nI have to give the solution based on the reasoning directly now."
    
                else:
                    prefix = "</think>\n"
                    suffix = ""
                
                for msg in reversed(messages):
                    if msg.get("role") == "user":
                        content = msg.get("content") or ""
                        msg["content"] = prefix + content + suffix
                        break

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
                        yield _sse_event("done", {"task_id": task_id})
                        log.info("[%s] llm/complete done  tokens=%d", task_id, idx)

            except Exception as exc:
                log.exception("[%s] llm/complete failed", task_id)
                err_msg = str(exc)
                if not extern_llm and llm_server.process is not None and llm_server.process.poll() is not None:
                    err_msg = f"LLM server crashed during inference. {llm_server.read_log(tail=30)}"
                yield _sse_event("error", {"task_id": task_id, "error": err_msg})

        return Response(generate(), mimetype="text/event-stream")

    # ------------------------------------------------------------------
    # POST /worker/switch-model
    # ------------------------------------------------------------------

    @bp.route("/switch-model", methods=["POST"])
    def switch_model():
        """Switch the LLM server to a different provider config.

        Accepts a JSON body with provider fields (backend, model, slug,
        endpoint, api_key, server_port, etc.).  For local backends the
        current server is stopped and restarted with the new config.
        """
        from assai.core.config import ProviderConfig

        data = flask_request.get_json(silent=True) or {}
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
                return jsonify({"error": str(exc)}), 503

        log.info("switch-model: now using %s (%s)", new_prov.slug, new_prov.backend)
        return jsonify({"ok": True, "model": new_prov.model, "slug": new_prov.slug})

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
        active = config.active_provider()
        return jsonify({
            "telemetry": True,
            "tools": [td.qualified_name for td in registry.all_tools()],
            "namespaces": registry.namespaces(),
            "llm_running": llm_server.is_running(),
            "llm_pid": llm_server.pid,
            "llm_model": active.model,
            "llm_backend": active.backend,
            "extern_llm": extern_llm,
            "log_path": llm_server.latest_log_path(),
        })

    return bp, llm_server, registry


# ------------------------------------------------------------------
# Background poller
# ------------------------------------------------------------------

class WorkerPoller:
    """Polls the orchestrator for work via WebSocket.

    Connects to the orchestrator's SocketIO and emits ``work_pop``
    events.  Falls back to HTTP ``GET /work/pop`` if the WebSocket
    connection is unavailable.
    """

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
        self.base_url = self.worker_url.rsplit("/worker", 1)[0]
        self.llm_server = llm_server
        self.registry = registry
        self._stop = threading.Event()
        self._sio = None

        if config.worker.sandbox == "container":
            from assai.core.sandbox import SandboxManager
            self._sandbox = SandboxManager(
                image=config.worker.sandbox_image,
                container_port=config.worker.sandbox_port,
            )
        else:
            self._sandbox = None

        log.info(
            "poller created  orchestrator=%s  worker=%s  poll=%ds  sandbox=%s",
            self.orchestrator_url, self.worker_url,
            self.config.queue.poll_interval,
            config.worker.sandbox,
        )

    def _connect_ws(self):
        """Establish a SocketIO client connection to the orchestrator."""
        if self._sio is not None and self._sio.connected:
            return True
        try:
            import socketio as sio_pkg
            self._sio = sio_pkg.Client(reconnection=True, logger=False, engineio_logger=False)
            ws_url = self.orchestrator_url.rsplit("/", 1)[0]
            self._sio.connect(ws_url, transports=["websocket"])
            log.info("poller connected to orchestrator via WebSocket")
            return True
        except Exception:
            log.debug("WebSocket connection to orchestrator failed, will use HTTP fallback")
            self._sio = None
            return False

    def run(self):
        log.info("poller started")
        self._connect_ws()
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:
                log.exception("poller error")
            time.sleep(self.config.queue.poll_interval)

    def stop(self):
        log.info("poller stopping")
        self._stop.set()
        if self._sandbox is not None:
            try:
                self._sandbox.stop()
            except Exception:
                log.exception("failed to stop sandbox")
        if self._sio is not None:
            try:
                self._sio.disconnect()
            except Exception:
                pass

    def _poll_once(self):
        work = None

        if self._sio is not None and self._sio.connected:
            try:
                work = self._sio.call("work_pop", timeout=10)
            except Exception:
                log.debug("WebSocket work_pop failed, reconnecting")
                self._connect_ws()
                return
        else:
            self._connect_ws()
            if self._sio is None or not self._sio.connected:
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

        if not work or not work.get("task_id"):
            return

        task_id = work["task_id"]
        kind = work.get("kind", "")
        log.info("[%s] popped work  kind=%s", task_id, kind)

        if kind == "llm_complete":
            self._prepare_llm_work(work)
            result, error = self._dispatch_llm(work)
        elif kind == "tool_call":
            result, error = self._dispatch_tool(work)
        else:
            log.warning("[%s] unknown work kind: %s", task_id, kind)
            return

        self._push_result(task_id, kind, result, work, error=error)

    def _prepare_llm_work(self, work: dict) -> None:
        """Set up worktree and compress context before LLM dispatch.

        Mutates *work* in place.
        """
        task_id = work.get("task_id", "")
        agent = work.get("agent", "")

        if agent in ("coder",) and work.get("project_path"):
            wt_path = self._setup_worktree(work)
            if wt_path:
                msgs = work.get("messages", [])
                if msgs and msgs[0].get("role") == "system":
                    addendum = (
                        f"\n\n## Working Directory\n"
                        f"Your worktree is at: ``{wt_path}``\n"
                        f"Use this as ``cwd`` for all code and git tool calls."
                    )
                    msgs[0]["content"] += addendum

        # FIXME: Not sure if this is where this shoudl be
        compressor = work.get("compressor", "")
        messages = work.get("messages", [])
        if compressor and messages:
            provider_info = work.get("provider", {})
            ctx_window = provider_info.get("context_window", 0) if isinstance(provider_info, dict) else 0
            if not ctx_window:
                active = self.config.active_provider()
                ctx_window = active.context_window
            try:
                from assai.core.config import ProviderConfig
                if isinstance(provider_info, dict) and provider_info.get("endpoint"):
                    prov = ProviderConfig.from_dict(provider_info)
                else:
                    prov = self.config.local_provider() or self.config.active_provider()
                llm = create_llm(prov)
                compressed = compress_messages(
                    messages, ctx_window, llm,
                    model=prov.slug or prov.model,
                )
                if len(compressed) < len(messages):
                    work["messages"] = compressed
                    log.info("[%s] compressed context: %d -> %d messages",
                             task_id, len(messages), len(compressed))
            except Exception:
                log.exception("[%s] context compression failed, using full context", task_id)

    def _setup_worktree(self, work: dict) -> str | None:
        """Create or reuse a git worktree for a work task.

        Returns the worktree path, or ``None`` if unavailable.
        """

        # FIXME: Maybe this need to be somewhere else
        # the worker should be the one to do it but I think this might be deserving of its own
        #
        project_path = work.get("project_path", "")
        project_name = work.get("project_name", "")
        task_id = work.get("task_id", "")

        if not project_path or not os.path.isdir(project_path):
            return None

        if not os.path.isdir(os.path.join(project_path, ".git")):
            return project_path

        from assai.tracker.git import GitTracker

        project_git = GitTracker(project_path)
        slug = task_id[:12]
        wt_name = f"{project_name}-{slug}" if project_name else f"work-{slug}"

        for wt in project_git.list_worktrees():
            if wt.path.endswith(wt_name):
                log.info("[%s] reusing worktree %s", task_id, wt.path)
                return wt.path

        try:
            wt_path = project_git.create_worktree(wt_name, base_branch="HEAD")
            log.info("[%s] created worktree %s", task_id, wt_path)
            return wt_path
        except Exception as exc:
            log.warning("[%s] worktree creation failed: %s", task_id, exc)
            return project_path

    def _dispatch_llm(self, work: dict) -> tuple[str | dict, str | None]:
        """Consume the worker's SSE stream and relay to the orchestrator
        via a single streaming POST (NDJSON body, chunked transfer)."""
        task_id = work.get("task_id", "")
        conversation = work.get("conversation", "")
        n_msgs = len(work.get("messages", []))
        log.info("[%s] dispatching llm_complete  messages=%d", task_id, n_msgs)

        try:
            payload: dict = {
                "messages": work.get("messages", []),
                "tools": work.get("tools"),
                "task_id": task_id,
            }
            if work.get("provider"):
                payload["provider"] = work["provider"]
            if work.get("enable_thinking") is not None:
                payload["enable_thinking"] = work["enable_thinking"]

            resp = http.post(
                f"{self.worker_url}/llm/complete",
                json=payload,
                stream=True,
                timeout=600,
            )
            if resp.status_code >= 400:
                text = resp.text[:500] if hasattr(resp, "text") else ""
                log.error("[%s] llm_complete returned %d: %s", task_id, resp.status_code, text)
                return "", f"LLM returned HTTP {resp.status_code}: {text[:200]}"

            accumulated_text = ""
            accumulated_reasoning = ""
            error_msg = None

            def _event_generator():
                """Yield NDJSON lines from the worker's SSE stream."""
                nonlocal accumulated_text, accumulated_reasoning, error_msg
                event_type = ""

                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                        continue
                    if not line.startswith("data: "):
                        continue

                    try:
                        event_data = json.loads(line[6:])
                    except (json.JSONDecodeError, ValueError):
                        continue

                    if event_type == "token":
                        accumulated_text += event_data.get("token", "")
                    elif event_type == "reasoning":
                        accumulated_reasoning += event_data.get("token", "")
                    elif event_type == "error":
                        error_msg = event_data.get("error", "unknown error")

                    ndjson_line = json.dumps({
                        "task_id": task_id,
                        "conversation": conversation,
                        "event_type": event_type,
                        "data": event_data,
                    }) + "\n"
                    yield ndjson_line.encode("utf-8")

            relay_resp = http.post(
                f"{self.orchestrator_url}/stream/push",
                data=_event_generator(),
                headers={"Content-Type": "application/x-ndjson"},
                timeout=600,
            )
            if relay_resp.status_code >= 400:
                log.warning("[%s] orchestrator stream/push returned %d",
                            task_id, relay_resp.status_code)

            log.info("[%s] llm_complete finished  chars=%d", task_id, len(accumulated_text))

            if error_msg:
                return "", error_msg
            result_val = accumulated_text
            if accumulated_reasoning:
                result_val = {"text": accumulated_text, "reasoning": accumulated_reasoning}
            return result_val, None

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

        from assai.core.sandbox import is_sandboxed
        if self._sandbox is not None and is_sandboxed(tool_name):
            return self._dispatch_tool_sandbox(work, tool_name, args)

        return self._dispatch_tool_local(work, tool_name, args)

    def _dispatch_tool_sandbox(self, work: dict, tool_name: str, args: dict) -> tuple[str, str | None]:
        task_id = work.get("task_id", "")

        if not self._sandbox.running:
            project_path = args.get("cwd") or self.config.workspace
            self._sandbox.start(project_path, session_id=task_id[:12])

        tools_url = f"{self._sandbox.endpoint}/tools/call"
        log.info("[%s] routing %s to sandbox %s", task_id, tool_name, tools_url)

        try:
            resp = http.post(
                tools_url,
                json={"tool": tool_name, "args": args},
                timeout=self.config.worker.timeout,
            )
            try:
                body = resp.json()
            except Exception:
                log.error(
                    "[%s] tool_call %s returned non-JSON (status=%d): %s",
                    task_id, tool_name, resp.status_code, resp.text[:500],
                )
                return json.dumps({"error": f"tool returned non-JSON (HTTP {resp.status_code})"}), \
                       f"tool {tool_name} returned non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}"
            if resp.status_code >= 400:
                error = body.get("error", f"HTTP {resp.status_code}")
                log.error("[%s] tool_call %s failed: %s", task_id, tool_name, error)
                return json.dumps({"error": error}), error
            result = body.get("result", "")
            log.info("[%s] tool_call finished  tool=%s  chars=%d", task_id, tool_name, len(result))
            return result, None
        except Exception as exc:
            log.exception("[%s] tool dispatch failed  tool=%s", task_id, tool_name)
            return json.dumps({"error": str(exc)}), str(exc)

    def _dispatch_tool_local(self, work: dict, tool_name: str, args: dict) -> tuple[str, str | None]:
        task_id = work.get("task_id", "")

        from assai.core.context import WorkerContext, OrchestratorClient, set_context, reset_context

        client = OrchestratorClient(self.orchestrator_url)
        ctx = WorkerContext(
            task_id=task_id,
            kind=work.get("kind", ""),
            project=work.get("project", ""),
            conversation=work.get("conversation", ""),
            agent=work.get("agent", ""),
            client=client,
        )
        token = set_context(ctx)
        try:
            result = self.registry.call(tool_name, args)
            log.info("[%s] tool_call finished  tool=%s  chars=%d", task_id, tool_name, len(result))
            return result, None
        except KeyError:
            error = f"unknown tool: {tool_name}"
            log.error("[%s] %s", task_id, error)
            return json.dumps({"error": error}), error
        except Exception as exc:
            log.exception("[%s] tool dispatch failed  tool=%s", task_id, tool_name)
            return json.dumps({"error": str(exc)}), str(exc)
        finally:
            reset_context(token)

    def _push_result(self, task_id: str, kind: str, result: str | dict, work: dict,
                     error: str | None = None):
        if isinstance(result, dict) and "text" in result and "reasoning" in result:
            result_text = result["text"]
            reasoning_text = result["reasoning"]
        else:
            result_text = result if isinstance(result, str) else ""
            reasoning_text = ""
        log.info("[%s] pushing result  kind=%s  chars=%d  has_tool_calls=%s  error=%s",
                 task_id, kind, len(result_text), isinstance(result, dict), bool(error))
        try:
            payload: dict = {
                "result": result_text,
                "kind": kind,
                "conversation": work.get("conversation", ""),
                "tool": work.get("tool", ""),
                "raw": result,
            }
            if reasoning_text:
                payload["reasoning"] = reasoning_text
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
        config, extern_llm=extern_llm,
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
    """Register telemetry SocketIO handlers.

    Initializes the system monitor eagerly in a background thread so
    the first ``request_telemetry`` from the frontend gets an instant
    response instead of blocking on hardware probing.
    """
    _observer = None

    def _init_observer():
        nonlocal _observer
        try:
            from assai.core.system_monitor import throttled_monitor
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
