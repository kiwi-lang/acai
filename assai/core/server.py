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

from assai.core.agent_store import AgentDef, AgentStore, hydrate_task, resolve_task
from assai.core.llm import create_llm
from assai.core.stream import StreamTracker
from assai.core.chat import ChatStore
from assai.core.config import (
    AssaiConfig, ProviderConfig, load_config, load_providers, save_providers,
)
from assai.core.projects import Project, ProjectStore, scaffold, clone
from assai.scheduler import ProviderScheduler
from assai.events import EventBus
from assai.queue.work import TaskStatus, WorkQueue
from assai.tools.registry import ToolRegistry
from assai.tracker.git import GitTracker

log = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Orchestrator — watches completed items and chains tool calls
# ------------------------------------------------------------------

class Orchestrator:
    """Background thread that chains work items.

    Watches for completed ``llm_complete`` items whose results contain
    tool calls, creates ``tool_call`` items for each, and schedules a
    follow-up ``llm_complete`` once all tool results are in.

    Also reaps stuck tasks (``in_progress`` longer than the configured
    timeout) and retries them when ``retries < max_retries``.
    """

    def __init__(self, config: AssaiConfig, queue: WorkQueue,
                 tasks_dir: str | None = None,
                 socketio_ref: list | None = None,
                 chat: ChatStore | None = None):
        self.config = config
        self.queue = queue
        self.tasks_dir = tasks_dir or config.worker.tasks_dir
        self._sio_ref = socketio_ref or [None]
        self._chat = chat

    def run(self):
        while True:
            self._poll()
            self._reap_stuck()
            time.sleep(self.config.queue.poll_interval)

    def _poll(self):
        completed = self.queue.list(status=TaskStatus.COMPLETED)
        for task in completed:
            if task.kind != "llm_complete":
                continue
            if not task.result_path:
                continue
            self._maybe_chain(task)

    def _conv_id_from_task(self, task) -> str:
        """Extract conversation id from a task's spec_path."""
        if task.spec_path and task.spec_path.endswith("conversation.json"):
            return os.path.basename(os.path.dirname(task.spec_path))
        return ""

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

        task_project = task.project or ""
        task_root = task.root_task or task.id
        conv_id = self._conv_id_from_task(task)

        tool_task_ids = []
        for call in tool_calls:
            fn = call.get("function", {})
            tool_name = fn.get("name", "unknown")
            try:
                tool_args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                tool_args = {}

            if self._chat and conv_id:
                self._chat.append(conv_id, {
                    "role": "tool_call",
                    "content": json.dumps({"tool": tool_name, "args": tool_args}, ensure_ascii=False),
                    "name": tool_name,
                })

            sio = self._sio_ref[0]
            if sio is not None:
                sio.emit("tool_start", {
                    "conversation": conv_id,
                    "tool_name": tool_name,
                    "args": tool_args,
                })

            payload = {
                "tool": tool_name,
                "args": tool_args,
                "call_id": call.get("id", ""),
                "conversation": conv_id,
            }
            payload_path = self._write_payload(task.id, call.get("id", ""), payload)

            tool_task = self.queue.push(
                title=f"tool: {tool_name}",
                kind="tool_call",
                gpu=0,
                priority=task.priority,
                spec_path=payload_path,
                depends_on=None,
                project=task_project,
                parent_task=task.id,
                root_task=task_root,
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
                project=task_project,
                parent_task=task.id,
                root_task=task_root,
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

    # -- Stuck-task reaper / retry ------------------------------------

    def _reap_stuck(self):
        """Find tasks stuck in ``in_progress`` beyond the timeout and
        either retry them or mark them failed."""
        from datetime import datetime, timezone

        timeout_secs = self.config.queue.task_timeout
        if timeout_secs <= 0:
            return

        now = datetime.now(timezone.utc)
        in_progress = self.queue.list(status=TaskStatus.IN_PROGRESS)

        for task in in_progress:
            if task.started_at is None:
                continue
            started = task.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = (now - started).total_seconds()
            if elapsed < timeout_secs:
                continue

            if task.retries < task.max_retries:
                log.warning(
                    "task %s stuck for %.0fs — requeueing (retry %d/%d)",
                    task.id, elapsed, task.retries + 1, task.max_retries,
                )
                self.queue.update(
                    task.id,
                    status=TaskStatus.READY,
                    retries=task.retries + 1,
                    started_at=None,
                )
            else:
                error_msg = (
                    f"timed out after {elapsed:.0f}s "
                    f"({task.retries}/{task.max_retries} retries exhausted)"
                )
                log.error("task %s failed permanently: %s", task.id, error_msg)
                self.queue.update(
                    task.id,
                    status=TaskStatus.FAILED,
                    error_log=error_msg,
                )
                if self._chat and task.spec_path and task.spec_path.endswith("conversation.json"):
                    conv_dir = os.path.dirname(task.spec_path)
                    conv_id = os.path.basename(conv_dir)
                    self._chat.append(conv_id, {
                        "role": "assistant",
                        "content": f"[Error] {error_msg}",
                    })

            sio = self._sio_ref[0]
            if sio is not None:
                sio.emit("task_timeout", {
                    "task_id": task.id,
                    "retries": task.retries,
                    "max_retries": task.max_retries,
                })


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
        "spec":         task.spec or "",
        "spec_path":    task.spec_path,
        "context_path": task.context_path,
        "result_path":  task.result_path,
        "worktree":     task.worktree,
        "retries":      task.retries,
        "max_retries":  task.max_retries,
        "created_at":   str(task.created_at) if task.created_at else "",
        "updated_at":   str(task.updated_at) if task.updated_at else "",
        "started_at":   str(task.started_at) if task.started_at else "",
        "assigned_to":  task.assigned_to,
        "depends_on":   task.depends_on,
        "error_log":    task.error_log,
        "project":      task.project or "",
        "agent":        task.agent or "",
        "parent_task":  task.parent_task or "",
        "root_task":    task.root_task or "",
    }


def _dump_request(workspace: str, task_id: str,
                  messages: list[dict], agent: str,
                  tools: list[dict] | None = None) -> None:
    """Write the hydrated LLM request to ``workspace/.requests/``."""
    from datetime import datetime, timezone
    dump_dir = os.path.join(workspace, ".requests")
    os.makedirs(dump_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{task_id}.json"
    payload: dict = {
        "task_id": task_id,
        "agent": agent,
        "timestamp": ts,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
    try:
        with open(os.path.join(dump_dir, filename), "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except OSError:
        log.warning("failed to dump rendered request for %s", task_id)


# ------------------------------------------------------------------
# Blueprint factory
# ------------------------------------------------------------------

def create_blueprint(config: AssaiConfig | None = None,
                     prefix: str = "/agent",
                     stream_tracker: StreamTracker | None = None):
    """Build the orchestrator Flask Blueprint.

    Returns ``(bp, queue, events, chat, config, stream_tracker)`` so
    the caller can compose with SocketIO and worker blueprints.
    """
    if config is None:
        config = AssaiConfig()

    tracker = stream_tracker or StreamTracker()

    bp = Blueprint("agent", __name__, url_prefix=prefix)
    _socketio_ref: list[SocketIO | None] = [None]

    events = EventBus()
    queue  = WorkQueue(config.queue.url)
    git    = GitTracker(config.git.repo_path, config.git.worktree_dir)

    projects_dir = os.path.join(config.workspace, "projects")
    projects = ProjectStore(projects_dir)
    chat = ChatStore(config.workspace)
    scheduler = ProviderScheduler(config.providers)

    agents_dir = os.path.join(config.workspace, "agents")
    agent_store = AgentStore(agents_dir)
    agent_store.ensure_default()

    from assai.tools.builtins import registry as _builtin_reg
    from assai.tools.ui import registry as _ui_reg
    tool_registry = ToolRegistry()
    tool_registry.merge(_builtin_reg)
    tool_registry.merge(_ui_reg)

    # Start orchestrator chaining loop in background
    orc = Orchestrator(config, queue, socketio_ref=_socketio_ref, chat=chat)
    threading.Thread(target=orc.run, daemon=True, name="orchestrator").start()

    # ==================================================================
    # Conversations CRUD
    # ==================================================================

    @bp.route("/conversations", methods=["GET"])
    def list_conversations():
        return jsonify(chat.list())

    @bp.route("/conversations", methods=["POST"])
    def create_conversation():
        data = request.get_json(silent=True) or {}
        meta = chat.create(
            title=data.get("title", ""),
            project=data.get("project", ""),
            provider=data.get("provider", "auto"),
            agent=data.get("agent", ""),
        )
        return jsonify(meta.to_dict()), 201

    @bp.route("/conversations/<conv_id>", methods=["PATCH"])
    def update_conversation(conv_id):
        data = request.get_json(silent=True) or {}
        allowed = {"title", "provider", "agent"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "no updatable fields"}), 400
        updated = chat.update_meta(conv_id, **fields)
        if updated is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(updated)

    @bp.route("/conversations/<conv_id>", methods=["GET"])
    def get_conversation(conv_id):
        meta = chat.get_meta(conv_id)
        if meta is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(meta)

    @bp.route("/conversations/<conv_id>", methods=["DELETE"])
    def delete_conversation(conv_id):
        chat.delete(conv_id)
        return jsonify({"deleted": True})

    # ==================================================================
    # Converse (async — queues work, returns task_id)
    # ==================================================================

    @bp.route("/converse", methods=["POST"])
    def agent_converse():
        data = request.get_json(silent=True) or {}
        message = data.get("message", "")
        conversation = data.get("conversation", "")
        project = data.get("project", "")
        parent_task = data.get("parent_task", "")
        provider_name = data.get("provider", "")
        agent_name = data.get("agent", "")
        if not message:
            return jsonify({"error": "message is required"}), 400

        if not conversation:
            meta = chat.create(title=message[:80], project=project,
                               provider=provider_name or "auto",
                               agent=agent_name or "default")
            conversation = meta.id
        else:
            updates: dict = {}
            if provider_name:
                updates["provider"] = provider_name
            if agent_name:
                updates["agent"] = agent_name
            if updates:
                chat.update_meta(conversation, **updates)

        chat.append(conversation, {"role": "user", "content": message})

        root = queue.resolve_root(parent_task) if parent_task else ""
        conv_path = chat._msg_path(conversation)
        task = queue.push(
            title=f"converse: {message[:60]}",
            kind="llm_complete",
            spec_path=conv_path,
            project=project,
            agent=agent_name or "default",
            parent_task=parent_task,
            root_task=root,
        )
        queue.update(task.id, status=TaskStatus.READY)
        tracker.register(task.id, conversation)

        return jsonify({"task_id": task.id, "conversation": conversation}), 202

    @bp.route("/conversations/<conv_id>/context-stats", methods=["GET"])
    def conversation_context_stats(conv_id):
        """Estimate token count for this conversation's context."""
        messages = chat.read(conv_id)
        total_chars = sum(len(m.get("content", "")) for m in messages)
        estimated_tokens = total_chars // 4
        active = scheduler.select("worker") or config.active_provider()
        max_context = config.llm.context_window
        if hasattr(active, "context_window") and active.context_window:
            max_context = active.context_window
        return jsonify({
            "estimated_tokens": estimated_tokens,
            "max_context": max_context,
            "message_count": len(messages),
        })

    @bp.route("/conversations/<conv_id>/inflight", methods=["GET"])
    def conversation_inflight(conv_id):
        """Check whether the conversation has any pending/in-progress tasks."""
        active_statuses = (TaskStatus.PENDING, TaskStatus.READY, TaskStatus.IN_PROGRESS)
        for status in active_statuses:
            tasks = queue.list(status=status)
            for t in tasks:
                if t.spec_path and t.spec_path.endswith("conversation.json"):
                    conv_dir = os.path.dirname(t.spec_path)
                    if os.path.basename(conv_dir) == conv_id:
                        return jsonify({"inflight": True, "task_id": t.id, "status": t.status})
        return jsonify({"inflight": False})

    @bp.route("/history", methods=["GET"])
    def agent_history():
        conversation = request.args.get("conversation", "")
        if not conversation:
            return jsonify({"messages": [], "streaming": None})

        messages = chat.read(conversation)
        streaming = None

        active_task, partial = tracker.get_partial(conversation)
        if active_task is not None:
            streaming = {
                "task_id": active_task,
                "partial": partial,
            }

        return jsonify({"messages": messages, "streaming": streaming})

    @bp.route("/history", methods=["DELETE"])
    def agent_history_clear():
        conversation = request.args.get("conversation", "")
        if conversation:
            chat.clear(conversation)
        return jsonify({"cleared": True})

    # ==================================================================
    # Work endpoints (worker pulls work / pushes results)
    # ==================================================================

    def _resolve_provider_for_task(task, conv_id: str = "") -> dict | None:
        """Look up the provider to use for a task.

        Returns a dict with provider connection info for the worker,
        or ``None`` to use the worker's default LLM config.
        """
        provider_name = ""
        if conv_id:
            meta = chat.get_meta(conv_id)
            if meta:
                provider_name = meta.get("provider", "auto")

        if not provider_name or provider_name == "auto":
            prov = scheduler.select("worker")
        else:
            prov = config.get_provider(provider_name)
            if prov is None:
                prov = scheduler.select("worker")

        if prov is None:
            return None

        active = config.active_provider()
        if prov.name == active.name:
            return None

        return {
            "name": prov.name,
            "backend": prov.backend,
            "model": prov.model,
            "slug": prov.slug,
            "endpoint": prov.endpoint or f"http://127.0.0.1:{prov.server_port}",
            "api_key": prov.api_key,
            "max_tokens": prov.max_tokens,
            "temperature": prov.temperature,
            "server_port": prov.server_port,
        }

    def _do_pop() -> dict | None:
        """Pop and hydrate the next ready work item.

        Returns the prepared work dict, or ``None`` when the queue is
        empty.  Shared by both the HTTP endpoint and the SocketIO handler.
        """
        task = queue.pop(status=TaskStatus.READY)
        if task is None:
            return None

        queue.update(task.id, status=TaskStatus.IN_PROGRESS)

        if task.kind == "tool_call":
            payload: dict = {}
            if task.spec_path and os.path.isfile(task.spec_path):
                with open(task.spec_path) as f:
                    try:
                        payload = json.load(f)
                    except (json.JSONDecodeError, ValueError):
                        payload = {}
            return {"task_id": task.id, "kind": task.kind, **payload}

        resolved = resolve_task(task, config, chat, projects)
        agent_name = resolved["agent"] or "default"
        agent_def = agent_store.get(agent_name) or agent_store.ensure_default()

        tool_defs = None
        tools_desc = ""
        if agent_def.tools:
            tool_defs = tool_registry.mcp_definitions(namespaces=agent_def.tools)
            if tool_defs:
                lines = []
                for td in tool_defs:
                    fn = td.get("function", {})
                    params = fn.get("parameters", {}).get("properties", {})
                    param_strs = [
                        f"  - {k}: {v.get('description', v.get('type', ''))}"
                        for k, v in params.items()
                    ]
                    lines.append(f"- **{fn.get('name', '')}**: {fn.get('description', '')}")
                    lines.extend(param_strs)
                tools_desc = "\n".join(lines)

        messages = hydrate_task(
            agent_def, agent_store, resolved,
            tools_description=tools_desc,
        )

        if config.dump_rendered_request:
            _dump_request(config.workspace, task.id, messages, agent_name,
                          tools=tool_defs)

        result: dict = {
            "task_id": task.id,
            "kind": task.kind,
            "messages": messages,
            "conversation": resolved["conversation"],
        }
        if tool_defs:
            result["tools"] = tool_defs

        conv_id = resolved["conversation"]
        if conv_id:
            prov_info = _resolve_provider_for_task(task, conv_id)
            if prov_info:
                result["provider"] = prov_info
        return result

    @bp.route("/work/pop", methods=["GET"])
    def work_pop():
        """Worker calls this to get the next prepared work item (HTTP fallback)."""
        result = _do_pop()
        if result is None:
            return "", 204
        return jsonify(result)

    @bp.route("/work/result/<task_id>", methods=["POST"])
    def work_result(task_id):
        """Worker pushes a completed result back."""
        data = request.get_json(silent=True) or {}
        result_text = data.get("result", "")
        error = data.get("error")
        conversation = data.get("conversation", "")
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
            if task.retries < task.max_retries:
                log.warning(
                    "task %s failed — requeueing (retry %d/%d): %s",
                    task_id, task.retries + 1, task.max_retries, error,
                )
                queue.update(
                    task_id,
                    status=TaskStatus.READY,
                    result_path=result_path,
                    error_log=error,
                    retries=task.retries + 1,
                    started_at=None,
                )
            else:
                queue.update(
                    task_id, status=TaskStatus.FAILED,
                    result_path=result_path, error_log=error,
                )
                if conversation:
                    chat.append(conversation, {
                        "role": "assistant",
                        "content": f"[Error] {error}",
                    })
        else:
            queue.update(task_id, status=TaskStatus.COMPLETED, result_path=result_path)
            if kind == "llm_complete" and result_text and conversation:
                chat.append(conversation, {"role": "assistant", "content": result_text})
            elif kind == "tool_call" and conversation:
                tool_name = data.get("tool", task.title.replace("tool: ", ""))
                result_preview = result_text[:500] if result_text else ""
                chat.append(conversation, {
                    "role": "tool_result",
                    "content": result_preview,
                    "name": tool_name,
                })
                sio = _socketio_ref[0]
                if sio is not None:
                    sio.emit("tool_end", {
                        "conversation": conversation,
                        "tool_name": tool_name,
                        "result_preview": result_preview[:200],
                    })

        return jsonify({"ok": True})

    # ==================================================================
    # Task queue CRUD
    # ==================================================================

    @bp.route("/tasks", methods=["GET"])
    def list_tasks():
        status = request.args.get("status")
        project = request.args.get("project")
        root_only = request.args.get("root_only", "").lower() in ("1", "true", "yes")
        tasks = queue.list(status=status, project=project, root_only=root_only)
        return jsonify([_task_json(t) for t in tasks])

    @bp.route("/tasks", methods=["POST"])
    def create_task():
        data = request.get_json(silent=True) or {}
        title = data.get("title", "")
        if not title:
            return jsonify({"error": "title is required"}), 400

        parent_id = data.get("parent_task", "")
        root = queue.resolve_root(parent_id) if parent_id else ""

        task = queue.push(
            title=title,
            description=data.get("description", ""),
            priority=data.get("priority", 0),
            depends_on=data.get("depends_on"),
            max_retries=data.get("max_retries", config.worker.max_retries),
            spec=data.get("spec", ""),
            spec_path=data.get("spec_path", ""),
            kind=data.get("kind", "task"),
            gpu=data.get("gpu", 0),
            project=data.get("project", ""),
            agent=data.get("agent", ""),
            parent_task=parent_id,
            root_task=root,
        )
        return jsonify(_task_json(task)), 201

    @bp.route("/tasks/<task_id>", methods=["GET"])
    def get_task(task_id):
        task = queue.get(task_id)
        if task is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(_task_json(task))

    @bp.route("/tasks/<task_id>/tree", methods=["GET"])
    def get_task_tree(task_id):
        """Return the root task and all its descendants."""
        tasks = queue.list_tree(task_id)
        if not tasks:
            return jsonify({"error": "not found"}), 404
        return jsonify([_task_json(t) for t in tasks])

    @bp.route("/tasks/<task_id>", methods=["PATCH"])
    def update_task(task_id):
        data = request.get_json(silent=True) or {}
        allowed = {
            "title", "description", "status", "priority",
            "spec", "spec_path", "assigned_to", "depends_on", "max_retries",
            "kind", "gpu", "project", "agent",
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
    # Providers CRUD
    # ==================================================================

    def _provider_json(p: ProviderConfig, active_name: str = "") -> dict:
        d = p.to_dict()
        d["active"] = (p.name == active_name)
        return d

    @bp.route("/providers", methods=["GET"])
    def list_providers_route():
        active = config.active_provider()
        return jsonify([_provider_json(p, active.name) for p in config.providers])

    @bp.route("/providers", methods=["POST"])
    def create_provider():
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        if config.get_provider(name) is not None:
            return jsonify({"error": f"provider '{name}' already exists"}), 409

        prov = ProviderConfig.from_dict({**data, "name": name})
        config.providers.append(prov)
        save_providers(config.workspace, config.providers)

        active = config.active_provider()
        return jsonify(_provider_json(prov, active.name)), 201

    @bp.route("/providers/<name>", methods=["GET"])
    def get_provider_route(name):
        prov = config.get_provider(name)
        if prov is None:
            return jsonify({"error": "not found"}), 404
        active = config.active_provider()
        return jsonify(_provider_json(prov, active.name))

    @bp.route("/providers/<name>", methods=["PUT"])
    def update_provider(name):
        prov = config.get_provider(name)
        if prov is None:
            return jsonify({"error": "not found"}), 404

        data = request.get_json(silent=True) or {}
        for key in ("backend", "model", "slug", "endpoint", "api_key",
                     "server_port", "server_command", "max_tokens",
                     "temperature", "priority", "roles"):
            if key in data:
                val = data[key]
                if key == "roles" and isinstance(val, str):
                    val = [r.strip() for r in val.split(",") if r.strip()]
                if key in ("server_port", "max_tokens", "priority"):
                    val = int(val)
                if key == "temperature":
                    val = float(val)
                setattr(prov, key, val)

        if prov.model and not prov.slug:
            from assai.core.config import _model_to_slug
            prov.slug = _model_to_slug(prov.model)

        save_providers(config.workspace, config.providers)
        active = config.active_provider()
        return jsonify(_provider_json(prov, active.name))

    @bp.route("/providers/<name>", methods=["DELETE"])
    def delete_provider(name):
        prov = config.get_provider(name)
        if prov is None:
            return jsonify({"error": "not found"}), 404
        config.providers = [p for p in config.providers if p.name != name]
        save_providers(config.workspace, config.providers)
        return jsonify({"deleted": True})

    @bp.route("/providers/<name>/activate", methods=["POST"])
    def activate_provider(name):
        prov = config.get_provider(name)
        if prov is None:
            return jsonify({"error": "not found"}), 404
        config.llm = prov.to_llm_config()
        active = config.active_provider()
        return jsonify(_provider_json(prov, active.name))

    # ==================================================================
    # Status
    # ==================================================================

    @bp.route("/status", methods=["GET"])
    def agent_status():
        counts = {}
        for s in _STATUS_KINDS:
            counts[s] = len(queue.list(status=s))

        active = config.active_provider()
        return jsonify({
            "queue": counts,
            "events": len(events.history),
            "llm_backend": config.llm.backend,
            "llm_endpoint": config.llm.endpoint,
            "active_provider": active.name,
            "providers_count": len(config.providers),
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

    # ==================================================================
    # Agents CRUD
    # ==================================================================

    def _agent_json(a: AgentDef) -> dict:
        return a.to_dict()

    @bp.route("/agents", methods=["GET"])
    def list_agents():
        return jsonify([_agent_json(a) for a in agent_store.list()])

    @bp.route("/agents", methods=["POST"])
    def create_agent():
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        slug = name.replace(" ", "-").lower()
        if agent_store.get(slug) is not None:
            return jsonify({"error": f"agent '{slug}' already exists"}), 409

        agent = AgentDef.from_dict({**data, "name": slug})
        agent_store.scaffold(agent)
        return jsonify(_agent_json(agent)), 201

    @bp.route("/agents/<name>", methods=["GET"])
    def get_agent(name):
        agent = agent_store.get(name)
        if agent is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(_agent_json(agent))

    @bp.route("/agents/<name>", methods=["PUT"])
    def update_agent(name):
        agent = agent_store.get(name)
        if agent is None:
            return jsonify({"error": "not found"}), 404

        data = request.get_json(silent=True) or {}
        updatable = (
            "description", "role", "avatar", "provider", "output_format",
            "model_overrides", "system_template", "context_sources",
            "tools", "sandbox", "max_iterations", "approval_required", "tags",
        )
        for key in updatable:
            if key in data:
                val = data[key]
                if key == "sandbox" and isinstance(val, dict):
                    from assai.core.agent_store import SandboxConfig
                    val = SandboxConfig(**val)
                if key == "max_iterations":
                    val = int(val)
                if key == "approval_required":
                    val = bool(val)
                setattr(agent, key, val)

        agent_store.save(agent)
        return jsonify(_agent_json(agent))

    @bp.route("/agents/<name>", methods=["DELETE"])
    def delete_agent(name):
        agent = agent_store.get(name)
        if agent is None:
            return jsonify({"error": "not found"}), 404
        agent_store.delete(name)
        return jsonify({"deleted": True})

    @bp.route("/agents/<name>/template", methods=["GET"])
    def get_agent_template(name):
        agent = agent_store.get(name)
        if agent is None:
            return jsonify({"error": "not found"}), 404
        content = agent_store.read_template(name)
        return jsonify({"name": name, "content": content})

    @bp.route("/agents/<name>/template", methods=["PUT"])
    def update_agent_template(name):
        agent = agent_store.get(name)
        if agent is None:
            return jsonify({"error": "not found"}), 404
        data = request.get_json(silent=True) or {}
        content = data.get("content", "")
        agent_store.save_template(name, content)
        return jsonify({"name": name, "content": content})

    # ==================================================================
    # Tool namespaces (from the builtin registry)
    # ==================================================================

    @bp.route("/tools/namespaces", methods=["GET"])
    def list_tool_namespaces():
        result = []
        for ns in tool_registry.namespaces():
            tools = tool_registry.tools_in(ns)
            result.append({
                "namespace": ns,
                "tools": [t.qualified_name for t in tools],
            })
        return jsonify(result)

    # ==================================================================
    # Toast (worker → orchestrator → frontend via WebSocket)
    # ==================================================================

    @bp.route("/toast", methods=["POST"])
    def receive_toast():
        data = request.get_json(silent=True) or {}
        sio = _socketio_ref[0]
        if sio is None:
            log.warning("toast received but SocketIO not initialised")
            return jsonify({"error": "socketio not ready"}), 503

        sio.emit("toast", {
            "message": data.get("message", ""),
            "title": data.get("title", ""),
            "status": data.get("status", "info"),
            "duration": data.get("duration", 5000),
        })
        return jsonify({"ok": True})

    return bp, queue, events, chat, config, tracker, _socketio_ref, _do_pop


# ------------------------------------------------------------------
# SocketIO setup
# ------------------------------------------------------------------

_STATUS_KINDS = (
    TaskStatus.PENDING, TaskStatus.CURATING, TaskStatus.READY,
    TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED, TaskStatus.FAILED,
    TaskStatus.REVIEW,
)


def setup_socketio(socketio: SocketIO, config: AssaiConfig,
                   queue: WorkQueue, events: EventBus,
                   pop_fn=None):
    """Wire SocketIO event handlers and start the background emitter."""

    @socketio.on("connect")
    def handle_connect():
        log.debug("WS client connected")
        socketio.emit("capabilities", {"telemetry": True})

    @socketio.on("disconnect")
    def handle_disconnect():
        log.debug("WS client disconnected")

    if pop_fn is not None:
        @socketio.on("work_pop")
        def handle_work_pop():
            """Worker requests work via WebSocket instead of HTTP."""
            result = pop_fn()
            return result or {}

    def _emit_loop():
        while True:
            socketio.sleep(2)
            try:
                tasks = queue.list()
                socketio.emit("tasks", [_task_json(t) for t in tasks])

                counts = {s: len(queue.list(status=s)) for s in _STATUS_KINDS}
                active = config.active_provider()
                socketio.emit("status", {
                    "queue": counts,
                    "events": len(events.history),
                    "llm_backend": config.llm.backend,
                    "llm_endpoint": config.llm.endpoint,
                    "active_provider": active.name,
                    "providers_count": len(config.providers),
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

    Returns ``(app, socketio, queue, events, chat, config, stream_tracker)``
    so callers can compose with worker blueprints (uber mode).
    """
    tracker = StreamTracker()

    bp, queue, events, chat, resolved_config, tracker, sio_ref, pop_fn = create_blueprint(
        config, prefix, stream_tracker=tracker,
    )
    app.register_blueprint(bp)

    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    sio_ref[0] = socketio
    setup_socketio(socketio, resolved_config, queue, events, pop_fn=pop_fn)

    return app, socketio, queue, events, chat, resolved_config, tracker
