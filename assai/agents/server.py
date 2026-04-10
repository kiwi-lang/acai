"""Orchestrator HTTP server — owns the work queue and project state.

The orchestrator is a Flask + SocketIO app that:

* Accepts user conversation messages (async — queues work, returns task_id).
* Serves ``GET /work/pop`` so workers can pull prepared work.
* Accepts ``POST /work/result/<task_id>`` to receive completed results.
* Relays streaming chunks from the worker to UI clients via WebSocket.
* Manages projects, specs, git worktrees, and the task CRUD API.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from flask import Blueprint, Flask, jsonify, request
from flask_socketio import SocketIO

from assai.agents.converse import ConverseAgent
from assai.agents.llm import create_llm
from assai.agents.scribe import ScribeAgent
from assai.chat import ChatStore
from assai.config import AssaiConfig, load_config
from assai.events import EventBus
from assai.projects import Project, ProjectStore, scaffold, clone
from assai.queue.work import TaskStatus, WorkQueue
from assai.tracker.git import GitTracker

log = logging.getLogger(__name__)

CONVERSE_SYSTEM = (
    "You are ASSAI, an AI agent helping the user plan and build software projects. "
    "Answer questions, suggest plans, and create tasks when asked.\n\n{spec_section}"
)


# ------------------------------------------------------------------
# Orchestrator — watches completed items and chains tool calls
# ------------------------------------------------------------------

class Orchestrator:
    """Background thread that chains work items.

    Watches for completed ``llm_complete`` items whose results contain
    tool calls, creates ``tool_call`` items for each, and schedules a
    follow-up ``llm_complete`` once all tool results are in.
    """

    def __init__(self, config: AssaiConfig, queue: WorkQueue,
                 tasks_dir: str | None = None):
        self.config = config
        self.queue = queue
        self.tasks_dir = tasks_dir or config.worker.tasks_dir

    def run(self):
        while True:
            self._poll()
            time.sleep(self.config.queue.poll_interval)

    def _poll(self):
        completed = self.queue.list(status=TaskStatus.COMPLETED)
        for task in completed:
            if task.kind != "llm_complete":
                continue
            if not task.result_path:
                continue
            self._maybe_chain(task)

    def _maybe_chain(self, task):
        try:
            with open(task.result_path) as f:
                raw = f.read()
        except OSError:
            return

        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return

        if not isinstance(result, dict):
            return
        tool_calls = result.get("tool_calls")
        if not tool_calls or not isinstance(tool_calls, list):
            return

        existing = self.queue.list()
        chained_ids = {
            t.id for t in existing
            if t.depends_on and task.id in t.depends_on
        }
        if chained_ids:
            return

        tool_task_ids = []
        for call in tool_calls:
            fn = call.get("function", {})
            tool_name = fn.get("name", "unknown")
            try:
                tool_args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                tool_args = {}

            payload = {
                "tool": tool_name,
                "args": tool_args,
                "call_id": call.get("id", ""),
            }
            payload_path = self._write_payload(task.id, call.get("id", ""), payload)

            tool_task = self.queue.push(
                title=f"tool: {tool_name}",
                kind="tool_call",
                gpu=0,
                priority=task.priority,
                spec_path=payload_path,
                depends_on=None,
            )
            self.queue.update(tool_task.id, status=TaskStatus.READY)
            tool_task_ids.append(tool_task.id)

        if tool_task_ids:
            try:
                with open(task.spec_path) as f:
                    original_messages = json.load(f)
            except (OSError, json.JSONDecodeError):
                original_messages = []

            followup_messages = list(original_messages)
            followup_messages.append(result)
            for call, tid in zip(tool_calls, tool_task_ids):
                followup_messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": f"{{{{result:{tid}}}}}",
                })

            followup_path = self._write_payload(task.id, "followup", followup_messages)
            followup = self.queue.push(
                title=f"followup: {task.title}",
                kind="llm_complete",
                priority=task.priority,
                spec_path=followup_path,
                depends_on=tool_task_ids,
            )
            self.queue.update(followup.id, status=TaskStatus.READY)
            self.queue.update(task.id, status="chained")

    def _write_payload(self, parent_id: str, suffix: str, payload) -> str:
        task_dir = os.path.join(self.tasks_dir, parent_id)
        os.makedirs(task_dir, exist_ok=True)
        path = os.path.join(task_dir, f"payload_{suffix}.json")
        with open(path, "w") as f:
            json.dump(payload, f)
        return path


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _task_json(task):
    return {
        "id":           task.id,
        "kind":         task.kind,
        "gpu":          task.gpu,
        "title":        task.title,
        "description":  task.description,
        "status":       task.status,
        "priority":     task.priority,
        "spec_path":    task.spec_path,
        "context_path": task.context_path,
        "result_path":  task.result_path,
        "worktree":     task.worktree,
        "retries":      task.retries,
        "max_retries":  task.max_retries,
        "created_at":   str(task.created_at) if task.created_at else "",
        "updated_at":   str(task.updated_at) if task.updated_at else "",
        "assigned_to":  task.assigned_to,
        "depends_on":   task.depends_on,
        "error_log":    task.error_log,
    }


# ------------------------------------------------------------------
# Blueprint factory
# ------------------------------------------------------------------

def create_blueprint(config: AssaiConfig | None = None,
                     prefix: str = "/agent"):
    """Build the orchestrator Flask Blueprint.

    Returns ``(bp, queue, events, chat, config)`` so the caller can
    compose with SocketIO and worker blueprints.
    """
    if config is None:
        config = AssaiConfig()

    bp = Blueprint("agent", __name__, url_prefix=prefix)

    events = EventBus()
    queue  = WorkQueue(config.queue.url)
    git    = GitTracker(config.git.repo_path, config.git.worktree_dir)

    projects_dir = os.path.join(config.workspace, "projects")
    projects = ProjectStore(projects_dir)
    chat = ChatStore(projects_dir)

    # Start orchestrator chaining loop in background
    orc = Orchestrator(config, queue)
    threading.Thread(target=orc.run, daemon=True, name="orchestrator").start()

    # ==================================================================
    # Conversation (async — queues work, returns task_id)
    # ==================================================================

    @bp.route("/converse", methods=["POST"])
    def agent_converse():
        data = request.get_json(silent=True) or {}
        message = data.get("message", "")
        project = data.get("project", "_default")
        if not message:
            return jsonify({"error": "message is required"}), 400

        chat.append(project, {"role": "user", "content": message})

        chat_path = os.path.join(projects_dir, project, "chat.json")
        task = queue.push(
            title=f"converse: {message[:60]}",
            kind="llm_complete",
            spec_path=chat_path,
        )
        queue.update(task.id, status=TaskStatus.READY)

        return jsonify({"task_id": task.id, "project": project}), 202

    @bp.route("/history", methods=["GET"])
    def agent_history():
        project = request.args.get("project", "_default")
        return jsonify({"messages": chat.read(project)})

    @bp.route("/history", methods=["DELETE"])
    def agent_history_clear():
        project = request.args.get("project", "_default")
        chat.clear(project)
        return jsonify({"cleared": True})

    # ==================================================================
    # Work endpoints (worker pulls work / pushes results)
    # ==================================================================

    @bp.route("/work/pop", methods=["GET"])
    def work_pop():
        """Worker calls this to get the next prepared work item."""
        task = queue.pop(status=TaskStatus.READY)
        if task is None:
            return "", 204

        queue.update(task.id, status=TaskStatus.IN_PROGRESS)

        if task.kind == "llm_complete" and task.spec_path.endswith("chat.json"):
            messages = _hydrate_conversation(config, task.spec_path)
            return jsonify({
                "task_id": task.id,
                "kind": task.kind,
                "messages": messages,
            })

        payload = {}
        if task.spec_path and os.path.isfile(task.spec_path):
            with open(task.spec_path) as f:
                try:
                    payload = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    payload = {}

        return jsonify({
            "task_id": task.id,
            "kind": task.kind,
            **payload,
        })

    @bp.route("/work/result/<task_id>", methods=["POST"])
    def work_result(task_id):
        """Worker pushes a completed result back."""
        data = request.get_json(silent=True) or {}
        result_text = data.get("result", "")
        error = data.get("error")
        project = data.get("project", "_default")
        kind = data.get("kind", "")

        task = queue.get(task_id)
        if task is None:
            return jsonify({"error": "task not found"}), 404

        result_dir = os.path.join(config.worker.tasks_dir, task_id)
        os.makedirs(result_dir, exist_ok=True)
        result_path = os.path.join(result_dir, "result.json")
        with open(result_path, "w") as f:
            json.dump(data.get("raw", result_text), f)

        if error:
            queue.update(
                task_id, status=TaskStatus.FAILED,
                result_path=result_path, error_log=error,
            )
            chat.append(project, {
                "role": "assistant",
                "content": f"[Error] {error}",
            })
        else:
            queue.update(task_id, status=TaskStatus.COMPLETED, result_path=result_path)
            if kind == "llm_complete" and result_text:
                chat.append(project, {"role": "assistant", "content": result_text})

        return jsonify({"ok": True})

    # ==================================================================
    # Task queue CRUD
    # ==================================================================

    @bp.route("/tasks", methods=["GET"])
    def list_tasks():
        status = request.args.get("status")
        tasks = queue.list(status=status)
        return jsonify([_task_json(t) for t in tasks])

    @bp.route("/tasks", methods=["POST"])
    def create_task():
        data = request.get_json(silent=True) or {}
        title = data.get("title", "")
        if not title:
            return jsonify({"error": "title is required"}), 400

        task = queue.push(
            title=title,
            description=data.get("description", ""),
            priority=data.get("priority", 0),
            depends_on=data.get("depends_on"),
            max_retries=data.get("max_retries", config.worker.max_retries),
            spec_path=data.get("spec_path", ""),
            kind=data.get("kind", "llm_complete"),
            gpu=data.get("gpu", 0),
        )
        return jsonify(_task_json(task)), 201

    @bp.route("/tasks/<task_id>", methods=["GET"])
    def get_task(task_id):
        task = queue.get(task_id)
        if task is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(_task_json(task))

    @bp.route("/tasks/<task_id>", methods=["PATCH"])
    def update_task(task_id):
        data = request.get_json(silent=True) or {}
        allowed = {
            "title", "description", "status", "priority",
            "spec_path", "assigned_to", "depends_on", "max_retries",
            "kind", "gpu",
        }
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "no updatable fields provided"}), 400

        queue.update(task_id, **fields)
        task = queue.get(task_id)
        if task is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(_task_json(task))

    # ==================================================================
    # Specs
    # ==================================================================

    @bp.route("/specs", methods=["GET"])
    def list_specs():
        specs_dir = config.scribe.specs_dir
        if not os.path.isdir(specs_dir):
            return jsonify([])
        names = sorted(
            n for n in os.listdir(specs_dir)
            if os.path.isfile(os.path.join(specs_dir, n))
        )
        return jsonify(names)

    @bp.route("/specs/<name>", methods=["GET"])
    def get_spec(name):
        path = os.path.join(config.scribe.specs_dir, name)
        if not os.path.isfile(path):
            return jsonify({"error": "not found"}), 404
        with open(path) as f:
            return jsonify({"name": name, "content": f.read()})

    # ==================================================================
    # Git worktrees
    # ==================================================================

    @bp.route("/worktrees", methods=["GET"])
    def list_worktrees():
        wts = git.list_worktrees()
        return jsonify([
            {"path": w.path, "branch": w.branch, "head": w.head}
            for w in wts
        ])

    # ==================================================================
    # Status
    # ==================================================================

    @bp.route("/status", methods=["GET"])
    def agent_status():
        counts = {}
        for s in _STATUS_KINDS:
            counts[s] = len(queue.list(status=s))

        return jsonify({
            "queue": counts,
            "events": len(events.history),
            "llm_backend": config.llm.backend,
            "llm_endpoint": config.llm.endpoint,
        })

    # ==================================================================
    # Events log
    # ==================================================================

    @bp.route("/events", methods=["GET"])
    def list_events():
        limit = request.args.get("limit", 50, type=int)
        recent = events.history[-limit:]
        return jsonify([
            {
                "kind": e.kind.value,
                "source": e.source,
                "data": e.data,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in recent
        ])

    # ==================================================================
    # Projects
    # ==================================================================

    def _project_json(p: Project) -> dict:
        from dataclasses import asdict
        return asdict(p)

    @bp.route("/projects", methods=["GET"])
    def list_projects():
        return jsonify([_project_json(p) for p in projects.list()])

    @bp.route("/projects", methods=["POST"])
    def create_project():
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400

        slug = name.replace(" ", "-").lower()

        proj = Project(
            name=slug,
            language=data.get("language", "python"),
            source=data.get("source", "new"),
            template=data.get("template", "default"),
            repo_url=data.get("repo_url", ""),
            provider=data.get("provider", ""),
            python_version=data.get("python_version", "3.12"),
            venv_path=data.get("venv_path", ".venv"),
            path=os.path.join(config.git.worktree_dir, slug),
        )

        try:
            if proj.source == "clone" and proj.repo_url:
                clone(proj)
            else:
                scaffold(proj)
            projects.save(proj)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        return jsonify(_project_json(proj)), 201

    @bp.route("/projects/<name>", methods=["GET"])
    def get_project(name):
        proj = projects.get(name)
        if proj is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(_project_json(proj))

    @bp.route("/projects/<name>", methods=["DELETE"])
    def delete_project(name):
        projects.delete(name)
        return jsonify({"deleted": True})

    return bp, queue, events, chat, config


# ------------------------------------------------------------------
# Context hydration
# ------------------------------------------------------------------

def _hydrate_conversation(config: AssaiConfig, chat_path: str) -> list[dict]:
    """Read the chat file and prepend the system prompt + spec."""
    try:
        with open(chat_path) as f:
            messages = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        messages = []

    spec = ""
    spec_path = os.path.join(config.scribe.specs_dir, "spec.md")
    if os.path.isfile(spec_path):
        with open(spec_path) as f:
            spec = f.read()

    spec_section = (
        f"Project specification:\n\n{spec}" if spec
        else "(No specification written yet.)"
    )
    system = CONVERSE_SYSTEM.format(spec_section=spec_section)

    return [{"role": "system", "content": system}] + messages


# ------------------------------------------------------------------
# SocketIO setup
# ------------------------------------------------------------------

_STATUS_KINDS = (
    TaskStatus.PENDING, TaskStatus.CURATING, TaskStatus.READY,
    TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED, TaskStatus.FAILED,
    TaskStatus.REVIEW,
)


def setup_socketio(socketio: SocketIO, config: AssaiConfig,
                   queue: WorkQueue, events: EventBus):
    """Wire SocketIO event handlers and start the background emitter."""

    @socketio.on("connect")
    def handle_connect():
        log.debug("WS client connected")
        socketio.emit("capabilities", {"telemetry": True})

    @socketio.on("disconnect")
    def handle_disconnect():
        log.debug("WS client disconnected")

    @socketio.on("chunk")
    def handle_chunk(data):
        socketio.emit("chunk", data)

    def _emit_loop():
        while True:
            socketio.sleep(2)
            try:
                tasks = queue.list()
                socketio.emit("tasks", [_task_json(t) for t in tasks])

                counts = {s: len(queue.list(status=s)) for s in _STATUS_KINDS}
                socketio.emit("status", {
                    "queue": counts,
                    "events": len(events.history),
                    "llm_backend": config.llm.backend,
                    "llm_endpoint": config.llm.endpoint,
                })

                recent = events.history[-100:]
                socketio.emit("events", [
                    {
                        "kind": e.kind.value,
                        "source": e.source,
                        "data": e.data,
                        "timestamp": e.timestamp.isoformat(),
                    }
                    for e in recent
                ])
            except Exception:
                log.exception("emitter error")

    socketio.start_background_task(_emit_loop)


# ------------------------------------------------------------------
# Convenience wrapper
# ------------------------------------------------------------------

def routes(app, config: AssaiConfig | None = None, prefix: str = "/agent"):
    """Register orchestrator routes and SocketIO on an existing Flask app.

    Returns ``(app, socketio, queue, events, chat, config)`` so callers
    can compose with worker blueprints (uber mode).
    """
    bp, queue, events, chat, resolved_config = create_blueprint(config, prefix)
    app.register_blueprint(bp)

    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    setup_socketio(socketio, resolved_config, queue, events)

    return app, socketio, queue, events, chat, resolved_config
