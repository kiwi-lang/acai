"""HTTP interface for the agent system.

Two ways to use this module:

1. **Integrate into an existing Flask app**::

       from assai.agents.server import routes
       routes(app)                          # uses default config
       routes(app, config=my_config)        # custom config

2. **Run standalone**::

       python -m assai.agents.server
       python -m assai.agents.server -c config.yaml --port 5050
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from flask import Blueprint, Flask, jsonify, request

from assai.agents.converse import ConverseAgent
from assai.agents.llm import create_llm
from assai.agents.scribe import ScribeAgent
from assai.agents.worker import Worker
from assai.config import AssaiConfig, load_config
from assai.events import EventBus
from assai.queue.work import TaskStatus, WorkQueue
from assai.tracker.git import GitTracker

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Orchestrator — watches completed items and chains the next work
# ------------------------------------------------------------------

class Orchestrator:
    """Background thread that chains work items.

    Watches for completed ``llm_complete`` items whose results contain
    tool calls, creates ``tool_call`` items for each, and schedules a
    follow-up ``llm_complete`` once all tool results are in.
    """

    def __init__(self, config: AssaiConfig, queue: WorkQueue,
                 tasks_dir: str = "tasks"):
        self.config = config
        self.queue = queue
        self.tasks_dir = tasks_dir

    def run(self):
        """Block forever, polling for completed items to chain."""
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
        """If the LLM result contains tool_calls, create work items."""
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

        # Already chained — check if follow-ups exist for this task
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
            # Read original messages, append assistant message + placeholders
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

            # Mark the original task as fully chained so we don't re-process
            self.queue.update(task.id, status="chained")

    def _write_payload(self, parent_id: str, suffix: str, payload) -> str:
        task_dir = os.path.join(self.tasks_dir, parent_id)
        os.makedirs(task_dir, exist_ok=True)
        path = os.path.join(task_dir, f"payload_{suffix}.json")
        with open(path, "w") as f:
            json.dump(payload, f)
        return path


# ------------------------------------------------------------------
# Blueprint
# ------------------------------------------------------------------

def create_blueprint(config: AssaiConfig | None = None,
                     prefix: str = "/agent") -> Blueprint:
    """Build a Flask Blueprint with all agent routes."""
    if config is None:
        config = AssaiConfig()

    bp = Blueprint("agent", __name__, url_prefix=prefix)

    llm    = create_llm(config.llm)
    events = EventBus()
    queue  = WorkQueue(config.queue.url)
    git    = GitTracker(config.git.repo_path, config.git.worktree_dir)

    converse = ConverseAgent(
        "converse", config, events, llm, config.scribe.specs_dir,
    )
    ScribeAgent(
        "scribe", config, events, llm, config.scribe.specs_dir, git,
    )

    # -- start worker + orchestrator in background threads -------------
    worker = Worker(config, queue)
    orchestrator = Orchestrator(config, queue)

    threading.Thread(target=worker.run, daemon=True, name="worker").start()
    threading.Thread(target=orchestrator.run, daemon=True, name="orchestrator").start()

    # -- helpers --------------------------------------------------------

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

    # ==================================================================
    # Conversation
    # ==================================================================

    @bp.route("/converse", methods=["POST"])
    def agent_converse():
        data = request.get_json(silent=True) or {}
        message = data.get("message", "")
        if not message:
            return jsonify({"error": "message is required"}), 400

        response = converse.respond(message)
        return jsonify({"response": response})

    @bp.route("/history", methods=["GET"])
    def agent_history():
        return jsonify({"messages": converse.history})

    @bp.route("/history", methods=["DELETE"])
    def agent_history_clear():
        converse.history.clear()
        return jsonify({"cleared": True})

    # ==================================================================
    # Task queue
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
        for s in (TaskStatus.PENDING, TaskStatus.CURATING,
                  TaskStatus.READY, TaskStatus.IN_PROGRESS,
                  TaskStatus.COMPLETED, TaskStatus.FAILED,
                  TaskStatus.REVIEW):
            counts[s] = len(queue.list(status=s))

        return jsonify({
            "queue": counts,
            "events": len(events.history),
            "conversation_turns": len(converse.history),
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

    return bp


# ------------------------------------------------------------------
# Convenience wrapper
# ------------------------------------------------------------------

def routes(app, config: AssaiConfig | None = None, prefix: str = "/agent"):
    """Register agent routes on an existing Flask app."""
    bp = create_blueprint(config, prefix)
    app.register_blueprint(bp)
    return app


# ------------------------------------------------------------------
# Standalone entrypoint
# ------------------------------------------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="assai agent server")
    parser.add_argument("-c", "--config", default=None,
                        help="path to a YAML config file")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=5050, type=int)
    parser.add_argument("--prefix", default="/agent",
                        help="URL prefix for all routes")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    if args.config:
        load_config(args.config)

    config = AssaiConfig()
    app = Flask(__name__)
    routes(app, config, prefix=args.prefix)

    print(f"Agent server on http://{args.host}:{args.port}{args.prefix}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
