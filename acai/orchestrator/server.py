"""Orchestrator HTTP server — owns project state and agent execution.

The orchestrator is a FastAPI + SocketIO app that:

* Accepts user conversation messages and streams LLM responses via SSE.
* Acquires workers from the ``LoadBalancer`` and dispatches work via
  ``TaskGraph`` subclasses (``ConverseGraph``, ``ThinkGraph``).
* Manages the ``LoadBalancer`` — workers register on startup and send
  periodic health telemetry over WebSocket.
* Relays SSE streams from workers to UI clients via ``StreamTracker``.
* Manages projects, specs, git worktrees, and the task CRUD API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response, StreamingResponse

from acai.orchestrator.compat import SocketIO, join_room, leave_room

from acai.orchestrator.agent_store import AgentDef, AgentStore, hydrate_task, resolve_task
from acai.orchestrator.load_balancer import LoadBalancer
from acai.orchestrator.stream import StreamTracker
from acai.orchestrator.chat import ChatStore
from acai.orchestrator.knowledge import KnowledgeStore
from acai.orchestrator.config import (
    AcaiConfig, ProviderConfig, load_config, load_providers, save_providers,
)
from acai.orchestrator.projects import Project, ProjectStore, scaffold, clone
from acai.scheduler import ProviderScheduler
from acai.tasks import ConverseGraph, ConverseScribeGraph, ThinkGraph, UberGraph, DynamicGraph, get_graph, list_graphs
from acai.events import EventBus
from acai.queue.work import TaskStatus, WorkQueue
from acai.tracker.git import GitTracker

log = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Helper: read JSON body (replaces request.get_json(silent=True))
# ------------------------------------------------------------------

async def _json_body(request: Request) -> dict:
    """Read JSON body, returning ``{}`` on any parse error."""
    try:
        return await request.json()
    except Exception:
        return {}


# ------------------------------------------------------------------
# Orchestrator — reaps stuck tasks
# ------------------------------------------------------------------

class Orchestrator:
    """Background thread that reaps stuck tasks.

    Tasks stuck in ``in_progress`` longer than the configured timeout
    are retried or marked failed.
    """

    def __init__(self, config: AcaiConfig, queue: WorkQueue,
                 socketio_ref: list | None = None,
                 chat: ChatStore | None = None):
        self.config = config
        self.queue = queue
        self._sio_ref = socketio_ref or [None]
        self._chat = chat

    def run(self):
        while True:
            self._reap_stuck()
            time.sleep(self.config.queue.poll_interval)

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
            if task.kind in ("converse", "think"):
                continue
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
        "enable_thinking": task.enable_thinking,
        "conversation": task.conversation or "",
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
# Router factory
# ------------------------------------------------------------------

def create_router(config: AcaiConfig | None = None,
                  prefix: str = "/agent",
                  stream_tracker: StreamTracker | None = None,
                  load_balancer: LoadBalancer | None = None):
    """Build the orchestrator APIRouter.

    Returns ``(router, queue, events, chat, config, stream_tracker,
    socketio_ref, load_balancer)`` so the caller can compose with
    SocketIO and worker routers.
    """
    if config is None:
        config = AcaiConfig()

    tracker = stream_tracker or StreamTracker()
    lb = load_balancer or LoadBalancer()
    lb.start()

    router = APIRouter(prefix=prefix, tags=["agent"])
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

    knowledge_dir = os.path.join(config.workspace, "knowledge")
    knowledge = KnowledgeStore(knowledge_dir)

    from acai.orchestrator.tools import discover_tools
    tool_registry = discover_tools(config=config)
    from acai.tools.meta import _configure as configure_meta_tools

    configure_meta_tools(tool_registry)

    for res in tool_registry.plugin_resources:
        if res.get("agents_dir"):
            agent_store.add_builtin_dir(res["agents_dir"])

    from acai.orchestrator.skill_store import SkillStore
    from acai.tools.skills import _configure as configure_skills

    skill_store = SkillStore(os.path.join(config.workspace, "skills"))
    for res in tool_registry.plugin_resources:
        if res.get("skills_dir"):
            extra_skills = SkillStore(res["skills_dir"])
            extra_skills.register_all(tool_registry)
    skill_store.register_all(tool_registry)
    configure_skills(skill_store)

    from acai.tools.ci import _configure as configure_ci
    configure_ci(config.ci)

    from acai.utils.audit import AuditTrail, NullAuditTrail

    def _make_audit(endpoint: str, **meta) -> AuditTrail | NullAuditTrail:
        """Create an AuditTrail for a request, or a no-op when disabled."""
        if not config.audit.enabled:
            return NullAuditTrail()
        trail = AuditTrail(output_dir=config.audit.dir)
        trail.set_meta(endpoint=endpoint, **meta)
        return trail

    orc = Orchestrator(config, queue, socketio_ref=_socketio_ref, chat=chat)
    threading.Thread(target=orc.run, daemon=True, name="orchestrator").start()

    # ==================================================================
    # Worker registry endpoints
    # ==================================================================

    @router.post("/workers/register", status_code=201)
    async def register_worker(request: Request):
        data = await _json_body(request)
        url = data.get("url", "")
        if not url:
            return JSONResponse({"error": "url is required"}, status_code=400)
        capabilities = data.get("capabilities", {})
        worker_id = lb.register(url, capabilities)
        return {"worker_id": worker_id}

    @router.delete("/workers/{worker_id}")
    def unregister_worker(worker_id: str):
        removed = lb.unregister(worker_id)
        if not removed:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"deleted": True}

    @router.get("/workers")
    def list_workers():
        return [w.to_dict() for w in lb.list_workers()]

    @router.post("/workers/heartbeat")
    async def worker_heartbeat_http(request: Request):
        data = await _json_body(request)
        worker_id = data.get("worker_id", "")
        telemetry = data.get("telemetry", {})
        if not worker_id:
            return JSONResponse({"error": "worker_id required"}, status_code=400)
        found = lb.heartbeat(worker_id, telemetry)
        if not found:
            return JSONResponse({"error": "unknown worker"}, status_code=404)
        return {"ok": True}

    # ==================================================================
    # Conversations CRUD
    # ==================================================================

    def _default_agent_for_project(proj_name: str) -> str:
        pn = (proj_name or "").strip()
        if not pn:
            return "default"
        p = projects.get(pn)
        if p is None:
            return "refiner"
        r = (getattr(p, "refiner", "") or "").strip()
        return r or "refiner"

    def _enrich_conversation_dict(meta: dict) -> dict:
        out = dict(meta)
        pn = (out.get("project") or "").strip()
        if pn:
            p = projects.get(pn)
            if p is not None:
                out["refiner"] = (getattr(p, "refiner", "") or "").strip() or "refiner"
        return out

    @router.get("/conversations")
    def list_conversations(request: Request):
        project = request.query_params.get("project", "")
        task_id = request.query_params.get("task_id", "")
        return [_enrich_conversation_dict(m) for m in chat.list(project=project, task_id=task_id)]

    @router.post("/conversations", status_code=201)
    async def create_conversation(request: Request):
        data = await _json_body(request)
        proj = data.get("project", "")
        task_id = data.get("task_id", "")
        default_agent = _default_agent_for_project(proj)
        meta = chat.create(
            title=data.get("title", ""),
            project=proj,
            task_id=task_id,
            provider=data.get("provider", "auto"),
            agent=data.get("agent", "") or default_agent,
        )
        return _enrich_conversation_dict(meta.to_dict())

    @router.patch("/conversations/{conv_id}")
    async def update_conversation(conv_id: str, request: Request):
        data = await _json_body(request)
        allowed = {"title", "description", "provider", "agent", "tags"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return JSONResponse({"error": "no updatable fields"}, status_code=400)
        if "tags" in fields and isinstance(fields["tags"], str):
            fields["tags"] = [t.strip() for t in fields["tags"].split(",") if t.strip()]
        updated = chat.update_meta(conv_id, **fields)
        if updated is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return _enrich_conversation_dict(updated)

    @router.get("/conversations/{conv_id}")
    def get_conversation(conv_id: str):
        meta = chat.get_meta(conv_id)
        if meta is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return _enrich_conversation_dict(meta)

    @router.delete("/conversations/{conv_id}")
    def delete_conversation(conv_id: str):
        chat.delete(conv_id)
        return {"deleted": True}

    # ==================================================================
    # Converse (SSE streaming via TaskGraph)
    # ==================================================================

    @router.get("/graphs")
    def get_graphs():
        """Return the list of user-facing task graphs, plus saved workflows."""
        graphs = list_graphs(user_facing_only=True)
        for d in (workflows_dir, _builtin_wf_dir):
            if not os.path.isdir(d):
                continue
            for entry in os.listdir(d):
                defn = os.path.join(d, entry, "definition.json")
                if not os.path.isfile(defn):
                    continue
                wf_id = entry
                if any(g["kind"] == f"workflow:{wf_id}" for g in graphs):
                    continue
                try:
                    with open(defn) as f:
                        spec = json.load(f)
                    graphs.append({
                        "kind": f"workflow:{wf_id}",
                        "label": spec.get("name", wf_id),
                        "description": spec.get("description", "Custom workflow"),
                    })
                except Exception:
                    pass
        return graphs

    @router.post("/converse")
    async def agent_converse(request: Request):
        import traceback as _tb

        data = await _json_body(request)
        message = data.get("message", "")
        conversation = data.get("conversation", "")
        project = data.get("project", "")
        task_id = data.get("task_id", "")
        provider_name = data.get("provider", "auto")
        agent_name = data.get("agent", "") or _default_agent_for_project(project)
        graph_kind = data.get("graph", "converse")

        workflow_spec = None
        workflow_dir = None
        if graph_kind.startswith("workflow:"):
            wf_id = graph_kind[len("workflow:"):]
            wf_dir = os.path.join(workflows_dir, wf_id)
            wf_path = os.path.join(wf_dir, "definition.json")
            if not os.path.isfile(wf_path):
                wf_dir = os.path.join(_builtin_wf_dir, wf_id)
                wf_path = os.path.join(wf_dir, "definition.json")
            if os.path.isfile(wf_path):
                with open(wf_path) as f:
                    workflow_spec = json.load(f)
                workflow_dir = wf_dir
                graph_kind = "workflow"
            else:
                return JSONResponse({"error": f"workflow '{wf_id}' not found"}, status_code=404)

        ephemeral = data.get("ephemeral", False)

        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)

        if ephemeral:
            import uuid as _uuid
            conversation = conversation or f"ephemeral-{_uuid.uuid4().hex[:12]}"
        elif not conversation:
            meta = chat.create(
                title=message[:80], project=project,
                task_id=task_id,
                provider=provider_name, agent=agent_name,
            )
            conversation = meta.id

        chat.append(conversation, {"role": "user", "content": message})

        provider_override = None
        if provider_name and provider_name != "auto":
            prov = config.get_provider(provider_name)
            if prov:
                from dataclasses import asdict as _asdict
                active = config.active_provider()
                if prov.name != active.name:
                    provider_override = _asdict(prov)

        work = {
            "message": message,
            "conversation": conversation,
            "agent": agent_name,
            "project": project,
            "spec_path": chat._msg_path(conversation),
            "stream_id": conversation,
            "provider_override": provider_override,
        }
        if workflow_spec:
            work["workflow_spec"] = workflow_spec
        if workflow_dir:
            work["workflow_dir"] = workflow_dir

        extra_ctx = data.get("context")
        if isinstance(extra_ctx, dict):
            work["extra_context"] = extra_ctx

        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        audit = _make_audit(
            "converse", conversation=conversation,
            agent=agent_name, project=project,
            graph=graph_kind,
        )

        async def generate():
            yield _sse("meta", {"conversation": conversation})
            try:
                async with lb.acquire() as worker:
                    audit.record("worker.acquired", phase="server", worker=worker.url)
                    graph = get_graph(
                        graph_kind, worker, work,
                        agent_store=agent_store,
                        chat=chat,
                        config=config,
                        tracker=tracker,
                        projects=projects,
                        tool_registry=tool_registry,
                        audit=audit,
                    )
                    async for event in graph.run(work):
                        yield _sse(
                            event.get("event_type", "message"),
                            event.get("data", {}),
                        )
            except TimeoutError:
                audit.record("error", phase="server", error="worker timeout")
                yield _sse("error", {"message": "No worker available (timeout waiting for a free worker)."})
            except Exception as exc:
                log.exception("converse stream error")
                audit.record("error", phase="server", error=str(exc))
                yield _sse("error", {
                    "message": f"{type(exc).__name__}: {exc}",
                    "traceback": _tb.format_exc(),
                })
            finally:
                audit.finalize()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"X-Conversation": conversation},
        )

    @router.get("/conversations/{conv_id}/context-stats")
    def conversation_context_stats(conv_id: str):
        messages = chat.read(conv_id)
        total_chars = sum(len(m.get("content", "")) for m in messages)
        estimated_tokens = total_chars // 4
        active = scheduler.select("worker") or config.active_provider()
        max_context = active.context_window
        return {
            "estimated_tokens": estimated_tokens,
            "max_context": max_context,
            "message_count": len(messages),
        }

    @router.get("/conversations/{conv_id}/inflight")
    def conversation_inflight(conv_id: str):
        active_statuses = (TaskStatus.PENDING, TaskStatus.READY, TaskStatus.IN_PROGRESS)
        for status in active_statuses:
            tasks = queue.list(status=status)
            for t in tasks:
                if t.spec_path and t.spec_path.endswith("conversation.json"):
                    conv_dir = os.path.dirname(t.spec_path)
                    if os.path.basename(conv_dir) == conv_id:
                        return {"inflight": True, "task_id": t.id, "status": t.status}
        return {"inflight": False}

    # ==================================================================
    # Uber conversation routing
    # ==================================================================

    @router.post("/uber/converse")
    async def uber_converse(request: Request):
        import traceback as _tb

        data = await _json_body(request)
        message = data.get("message", "")
        current_conversation = data.get("current_conversation", "")
        agent_name = data.get("agent", "default")
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)

        work = {
            "message": message,
            "current_conversation": current_conversation,
            "agent": agent_name,
        }

        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        audit = _make_audit(
            "uber/converse", agent=agent_name,
            current_conversation=current_conversation,
        )

        async def generate():
            try:
                async with lb.acquire() as worker:
                    audit.record("worker.acquired", phase="server", worker=worker.url)
                    graph = UberGraph.from_work(
                        worker, work,
                        agent_store=agent_store,
                        chat=chat,
                        config=config,
                        tracker=tracker,
                        projects=projects,
                        tool_registry=tool_registry,
                        audit=audit,
                    )
                    async for event in graph.run(work):
                        yield _sse(
                            event.get("event_type", "message"),
                            event.get("data", {}),
                        )
            except TimeoutError:
                audit.record("error", phase="server", error="worker timeout")
                yield _sse("error", {"message": "No worker available (timeout waiting for a free worker)."})
            except Exception as exc:
                log.exception("uber converse stream error")
                audit.record("error", phase="server", error=str(exc))
                yield _sse("error", {
                    "message": f"{type(exc).__name__}: {exc}",
                    "traceback": _tb.format_exc(),
                })
            finally:
                audit.finalize()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
        )

    # ==================================================================
    # Workflows — CRUD + execute
    # ==================================================================

    workflows_dir = os.path.join(config.workspace, "workflows")
    os.makedirs(workflows_dir, exist_ok=True)

    # Migrate legacy flat-file user workflows (<id>.json -> <id>/definition.json)
    for _fname in list(os.listdir(workflows_dir)):
        if _fname.endswith(".json") and os.path.isfile(os.path.join(workflows_dir, _fname)):
            _wf_id = _fname[:-5]
            _new_dir = os.path.join(workflows_dir, _wf_id)
            _new_path = os.path.join(_new_dir, "definition.json")
            if not os.path.isfile(_new_path):
                os.makedirs(_new_dir, exist_ok=True)
                os.rename(
                    os.path.join(workflows_dir, _fname),
                    _new_path,
                )
                log.info("migrated user workflow %s to %s", _fname, _new_path)
            else:
                os.remove(os.path.join(workflows_dir, _fname))
                log.info("removed stale flat-file workflow %s (directory already exists)", _fname)

    _builtin_wf_dir = os.path.join(os.path.dirname(__file__), os.pardir, "workflows")
    _builtin_wf_dir = os.path.normpath(_builtin_wf_dir)

    _extra_wf_dirs: list[str] = []
    for _res in tool_registry.plugin_resources:
        _pwd = _res.get("workflows_dir", "")
        if _pwd and os.path.isdir(_pwd):
            _extra_wf_dirs.append(_pwd)

    def _scan_wf_dir(directory: str, builtin: bool) -> list[dict]:
        results = []
        if not os.path.isdir(directory):
            return results
        for entry in sorted(os.listdir(directory)):
            defn = os.path.join(directory, entry, "definition.json")
            if not os.path.isfile(defn):
                continue
            try:
                with open(defn) as f:
                    spec = json.load(f)
                results.append({
                    "id": spec.get("id", entry),
                    "name": spec.get("name", entry),
                    "description": spec.get("description", ""),
                    "node_count": len(spec.get("nodes", [])),
                    "edge_count": len(spec.get("edges", [])),
                    "builtin": builtin,
                })
            except (json.JSONDecodeError, OSError):
                continue
        return results

    @router.get("/workflows/node-types")
    def get_node_types():
        """Return all registered node type definitions (pins, colors, etc.)."""
        from acai.tasks.nodes import all_types
        return [nt.to_dict() for nt in all_types()]

    @router.get("/workflows/agent-inputs/{agent_name}")
    def get_agent_template_inputs(agent_name: str):
        """Return custom template variables for an agent."""
        return {"agent": agent_name,
                "inputs": agent_store.template_inputs(agent_name)}

    @router.post("/workflows/resolve-pins")
    async def resolve_dynamic_pins(request: Request):
        """Resolve dynamic pins for a node given its data and the spec.

        Body: ``{node_type, data, spec?}``
        Returns: ``{pins: [...]}`` — list of pin dicts.
        """
        from acai.tasks.nodes import get as get_nt
        body = await _json_body(request)
        node_type = body.get("node_type", "")
        data = body.get("data", {})
        spec = body.get("spec")
        nt = get_nt(node_type)
        if nt is None:
            return {"pins": []}
        td = tool_registry.mcp_definitions() if tool_registry else []
        dyn = nt.dynamic_pins(data, spec, tool_defs=td)
        return {"pins": [p.to_dict() for p in dyn]}

    @router.get("/workflows/tool-definitions")
    def get_tool_definitions():
        """Return all registered tools with their parameter schemas.

        Used by the Skill Call node to populate its tool dropdown and
        dynamically generate input pins.
        """
        return tool_registry.mcp_definitions()

    @router.get("/workflows")
    def list_workflows():
        user_wfs = _scan_wf_dir(workflows_dir, builtin=False)
        user_ids = {w["id"] for w in user_wfs}
        builtin_wfs = [w for w in _scan_wf_dir(_builtin_wf_dir, builtin=True)
                       if w["id"] not in user_ids]
        for _wd in _extra_wf_dirs:
            for _w in _scan_wf_dir(_wd, builtin=True):
                if _w["id"] not in user_ids and _w["id"] not in {b["id"] for b in builtin_wfs}:
                    builtin_wfs.append(_w)
        return builtin_wfs + user_wfs

    @router.get("/workflows/{workflow_id}")
    def get_workflow(workflow_id: str):
        user_path = os.path.join(workflows_dir, workflow_id, "definition.json")
        if os.path.isfile(user_path):
            with open(user_path) as f:
                spec = json.load(f)
            spec["builtin"] = False
            return spec
        builtin_path = os.path.join(_builtin_wf_dir, workflow_id, "definition.json")
        if os.path.isfile(builtin_path):
            with open(builtin_path) as f:
                spec = json.load(f)
            spec["builtin"] = True
            return spec
        return JSONResponse({"error": "not found"}, status_code=404)

    @router.post("/workflows", status_code=201)
    async def save_workflow(request: Request):
        data = await _json_body(request)
        wf_id = data.get("id", "").strip()
        if not wf_id:
            return JSONResponse({"error": "id is required"}, status_code=400)
        data.setdefault("name", wf_id)
        wf_dir = os.path.join(workflows_dir, wf_id)
        os.makedirs(wf_dir, exist_ok=True)
        path = os.path.join(wf_dir, "definition.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    @router.put("/workflows/builtin/{workflow_id}")
    async def save_builtin_workflow(workflow_id: str, request: Request):
        """Dev-mode: overwrite a builtin workflow JSON in-place."""
        data = await _json_body(request)
        data["id"] = workflow_id
        data.setdefault("name", workflow_id)
        wf_dir = os.path.join(_builtin_wf_dir, workflow_id)
        os.makedirs(wf_dir, exist_ok=True)
        path = os.path.join(wf_dir, "definition.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    @router.put("/workflows/{workflow_id}")
    async def update_workflow(workflow_id: str, request: Request):
        data = await _json_body(request)
        data["id"] = workflow_id
        data.setdefault("name", workflow_id)
        wf_dir = os.path.join(workflows_dir, workflow_id)
        os.makedirs(wf_dir, exist_ok=True)
        path = os.path.join(wf_dir, "definition.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    @router.delete("/workflows/{workflow_id}")
    def delete_workflow(workflow_id: str):
        import shutil as _shutil
        wf_dir = os.path.join(workflows_dir, workflow_id)
        if os.path.isdir(wf_dir):
            _shutil.rmtree(wf_dir)
        return {"deleted": True}

    @router.get("/workflows/{workflow_id}/agents")
    def list_workflow_agents(workflow_id: str):
        """List agents bundled inside a workflow directory."""
        results = []
        for base in (os.path.join(workflows_dir, workflow_id),
                     os.path.join(_builtin_wf_dir, workflow_id)):
            agents_dir = os.path.join(base, "agents")
            if not os.path.isdir(agents_dir):
                continue
            for name in sorted(os.listdir(agents_dir)):
                def_path = os.path.join(agents_dir, name, "definition.json")
                if not os.path.isfile(def_path):
                    continue
                try:
                    with open(def_path) as f:
                        defn = json.load(f)
                    results.append({
                        "name": defn.get("name", name),
                        "description": defn.get("description", ""),
                        "provider": defn.get("provider", "auto"),
                        "output_format": defn.get("output_format", "text"),
                    })
                except (json.JSONDecodeError, OSError):
                    continue
            break
        return results

    @router.get("/workflows/{workflow_id}/skills")
    def list_workflow_skills(workflow_id: str):
        """List skills bundled inside a workflow directory."""
        results = []
        for base in (os.path.join(workflows_dir, workflow_id),
                     os.path.join(_builtin_wf_dir, workflow_id)):
            skills_dir = os.path.join(base, "skills")
            if not os.path.isdir(skills_dir):
                continue
            for ns in sorted(os.listdir(skills_dir)):
                ns_dir = os.path.join(skills_dir, ns)
                if not os.path.isdir(ns_dir):
                    continue
                for name in sorted(os.listdir(ns_dir)):
                    tool_path = os.path.join(ns_dir, name, "tool.json")
                    if not os.path.isfile(tool_path):
                        continue
                    try:
                        with open(tool_path) as f:
                            defn = json.load(f)
                        results.append({
                            "qualified_name": f"{ns}.{name}",
                            "namespace": ns,
                            "name": defn.get("name", name),
                            "description": defn.get("description", ""),
                        })
                    except (json.JSONDecodeError, OSError):
                        continue
            break
        return results

    @router.post("/workflows/{workflow_id}/agents")
    async def create_workflow_agent(workflow_id: str, request: Request):
        """Create or update an agent inside a workflow directory."""
        data = await _json_body(request)
        name = data.get("name", "").strip()
        if not name:
            return JSONResponse({"error": "agent name required"}, status_code=400)
        wf_dir = os.path.join(workflows_dir, workflow_id)
        if not os.path.isdir(wf_dir):
            wf_dir = os.path.join(_builtin_wf_dir, workflow_id)
        agent_dir = os.path.join(wf_dir, "agents", name)
        os.makedirs(agent_dir, exist_ok=True)
        definition: dict = {
            "name": name,
            "description": data.get("description", ""),
            "role": data.get("role", "system"),
            "provider": data.get("provider", "auto"),
            "output_format": data.get("output_format", "messages"),
        }
        if data.get("model_overrides"):
            definition["model_overrides"] = data["model_overrides"]
        if data.get("tools"):
            definition["tools"] = data["tools"]
        if data.get("tool_permissions"):
            definition["tool_permissions"] = data["tool_permissions"]
        if data.get("resource_permissions"):
            definition["resource_permissions"] = data["resource_permissions"]
        if data.get("context_sources"):
            definition["context_sources"] = data["context_sources"]
        if "max_iterations" in data:
            definition["max_iterations"] = data["max_iterations"]
        if "approval_required" in data:
            definition["approval_required"] = data["approval_required"]
        if "uses_sandbox" in data:
            definition["uses_sandbox"] = data["uses_sandbox"]
        if data.get("tags"):
            definition["tags"] = data["tags"]
        if data.get("avatar"):
            definition["avatar"] = data["avatar"]
        if data.get("scope"):
            definition["scope"] = data["scope"]
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            json.dump(definition, f, indent=2)
        template = data.get("system_template", "")
        if template:
            with open(os.path.join(agent_dir, "system.j2"), "w") as f:
                f.write(template)
        return {"created": True, "name": name}

    _DEFAULT_SKILL_CODE = (
        "#!/usr/bin/env python3\n"
        "import json, sys\n\n"
        "def main():\n"
        '    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}\n'
        '    result = {"status": "ok"}\n'
        "    print(json.dumps(result))\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    @router.post("/workflows/{workflow_id}/skills")
    async def create_workflow_skill(workflow_id: str, request: Request):
        """Create or update a skill inside a workflow directory."""
        data = await _json_body(request)
        ns = data.get("namespace", "").strip()
        name = data.get("name", "").strip()
        if not ns or not name:
            return JSONResponse({"error": "namespace and name required"}, status_code=400)
        wf_dir = os.path.join(workflows_dir, workflow_id)
        if not os.path.isdir(wf_dir):
            wf_dir = os.path.join(_builtin_wf_dir, workflow_id)
        skill_dir = os.path.join(wf_dir, "skills", ns, name)
        os.makedirs(skill_dir, exist_ok=True)
        params = data.get("parameters")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = None
        if not params:
            params = {"type": "object", "properties": {}, "required": []}
        tool_def = {
            "name": name,
            "description": data.get("description", ""),
            "parameters": params,
        }
        with open(os.path.join(skill_dir, "tool.json"), "w") as f:
            json.dump(tool_def, f, indent=2)
        code = data.get("code", "").strip() or _DEFAULT_SKILL_CODE
        with open(os.path.join(skill_dir, "run.py"), "w") as f:
            f.write(code)
        readme = data.get("readme", "").strip()
        if readme:
            with open(os.path.join(skill_dir, "README.md"), "w") as f:
                f.write(readme)
        return {"created": True, "qualified_name": f"{ns}.{name}"}

    @router.post("/workflows/validate")
    async def validate_workflow_spec(request: Request):
        """Validate a workflow spec posted as JSON body."""
        from acai.tasks.typecheck import typecheck

        spec = await _json_body(request)
        td = tool_registry.mcp_definitions() if tool_registry else []
        diags = typecheck(spec, tool_defs=td)
        errors = [d for d in diags if d.get("severity") == "error"]
        warnings = [d for d in diags if d.get("severity") == "warning"]
        return {
            "diagnostics": diags,
            "errors": errors,
            "warnings": warnings,
            "valid": len(errors) == 0,
        }

    @router.post("/workflows/{workflow_id}/validate")
    def validate_workflow_endpoint(workflow_id: str):
        """Validate a saved workflow by id."""
        from acai.tasks.typecheck import typecheck

        user_path = os.path.join(workflows_dir, workflow_id, "definition.json")
        builtin_path = os.path.join(_builtin_wf_dir, workflow_id, "definition.json")
        path = user_path if os.path.isfile(user_path) else builtin_path
        if not os.path.isfile(path):
            return JSONResponse({"error": "not found"}, status_code=404)
        with open(path) as f:
            spec = json.load(f)
        td = tool_registry.mcp_definitions() if tool_registry else []
        diags = typecheck(spec, tool_defs=td)
        errors = [d for d in diags if d.get("severity") == "error"]
        warnings = [d for d in diags if d.get("severity") == "warning"]
        return {
            "diagnostics": diags,
            "errors": errors,
            "warnings": warnings,
            "valid": len(errors) == 0,
        }

    @router.post("/workflows/{workflow_id}/run")
    async def run_workflow(workflow_id: str, request: Request):
        import traceback as _tb

        wf_dir = os.path.join(workflows_dir, workflow_id)
        path = os.path.join(wf_dir, "definition.json")
        if not os.path.isfile(path):
            wf_dir = os.path.join(_builtin_wf_dir, workflow_id)
            path = os.path.join(wf_dir, "definition.json")
        if not os.path.isfile(path):
            return JSONResponse({"error": "workflow not found"}, status_code=404)
        with open(path) as f:
            spec = json.load(f)

        data = await _json_body(request)
        message = data.get("message", "")
        conversation_raw = data.get("conversation", "")
        test_mode = data.get("test", False)
        test_conversation = data.get("test_conversation", [])

        conversation_preview = ""
        conversation_id = ""

        if not test_mode:
            if isinstance(conversation_raw, str) and conversation_raw.strip().startswith("["):
                conversation_preview = conversation_raw
            elif isinstance(conversation_raw, list):
                conversation_preview = json.dumps(conversation_raw, ensure_ascii=False)
            elif conversation_raw:
                conversation_id = conversation_raw

            if not conversation_id:
                meta = chat.create(
                    title=f"Workflow: {spec.get('name', workflow_id)}"[:80],
                    agent="default",
                )
                conversation_id = meta.id

            if message:
                chat.append(conversation_id, {"role": "user", "content": message})

        work = {
            "message": message,
            "conversation": conversation_id,
            "conversation_preview": conversation_preview,
            "workflow_spec": spec,
            "workflow_dir": wf_dir,
            "stream_id": conversation_id or f"test-{workflow_id}",
        }

        if test_conversation and isinstance(test_conversation, list):
            work["test_conversation"] = test_conversation

        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        audit = _make_audit(
            "workflow/run", workflow=workflow_id,
            workflow_name=spec.get("name", workflow_id),
            conversation=conversation_id,
        )

        async def generate():
            yield _sse("meta", {"conversation": conversation_id})
            try:
                async with lb.acquire() as worker:
                    audit.record("worker.acquired", phase="server", worker=worker.url)
                    graph = DynamicGraph.from_work(
                        worker, work,
                        agent_store=agent_store,
                        chat=chat,
                        config=config,
                        tracker=tracker,
                        projects=projects,
                        tool_registry=tool_registry,
                        audit=audit,
                    )
                    async for event in graph.run(work):
                        yield _sse(
                            event.get("event_type", "message"),
                            event.get("data", {}),
                        )
            except TimeoutError:
                audit.record("error", phase="server", error="worker timeout")
                yield _sse("error", {"message": "No worker available (timeout)."})
            except Exception as exc:
                log.exception("workflow run error")
                audit.record("error", phase="server", error=str(exc))
                yield _sse("error", {
                    "message": f"{type(exc).__name__}: {exc}",
                    "traceback": _tb.format_exc(),
                })
            finally:
                audit.finalize()
                yield _sse("audit_complete", {"audit_id": audit.request_id})

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"X-Conversation": conversation_id},
        )

    # ==================================================================
    # Think-then-generate conversation
    # ==================================================================

    @router.post("/think/converse")
    async def think_converse(request: Request):
        import traceback as _tb

        data = await _json_body(request)
        message = data.get("message", "")
        conversation = data.get("conversation", "")
        project = data.get("project", "")
        task_id = data.get("task_id", "")
        provider_name = data.get("provider", "")
        agent_name = data.get("agent", "")
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)

        if not conversation:
            default_agent = _default_agent_for_project(project)
            meta = chat.create(
                title=message[:80], project=project,
                task_id=task_id,
                provider=provider_name or "auto",
                agent=agent_name or default_agent,
            )
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

        conv_meta = chat.get_meta(conversation) or {}
        proj = (project or conv_meta.get("project") or "").strip()
        default_agent = _default_agent_for_project(proj)
        meta_agent = (conv_meta.get("agent") or "").strip()
        effective_agent = agent_name or meta_agent or default_agent

        provider_override = None
        if provider_name and provider_name != "auto":
            prov = config.get_provider(provider_name)
            if prov:
                from dataclasses import asdict as _asdict
                active = config.active_provider()
                if prov.name != active.name:
                    provider_override = _asdict(prov)

        work = {
            "message": message,
            "conversation": conversation,
            "agent": effective_agent,
            "project": project or proj,
            "spec_path": chat._msg_path(conversation),
            "stream_id": conversation,
            "provider_override": provider_override,
        }

        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        audit = _make_audit(
            "think/converse", conversation=conversation,
            agent=effective_agent, project=project or proj,
        )

        async def generate():
            yield _sse("meta", {"conversation": conversation})
            try:
                async with lb.acquire() as worker:
                    audit.record("worker.acquired", phase="server", worker=worker.url)
                    graph = ThinkGraph.from_work(
                        worker, work,
                        agent_store=agent_store,
                        chat=chat,
                        config=config,
                        tracker=tracker,
                        projects=projects,
                        tool_registry=tool_registry,
                        audit=audit,
                    )
                    async for event in graph.run(work):
                        yield _sse(
                            event.get("event_type", "message"),
                            event.get("data", {}),
                        )
            except TimeoutError:
                audit.record("error", phase="server", error="worker timeout")
                yield _sse("error", {"message": "No worker available (timeout waiting for a free worker)."})
            except Exception as exc:
                log.exception("think/converse stream error")
                audit.record("error", phase="server", error=str(exc))
                yield _sse("error", {
                    "message": f"{type(exc).__name__}: {exc}",
                    "traceback": _tb.format_exc(),
                })
            finally:
                audit.finalize()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"X-Conversation": conversation},
        )

    # ==================================================================
    # Scribe conversation (converse + silent knowledge update)
    # ==================================================================

    @router.post("/scribe/converse")
    async def scribe_converse(request: Request):
        import traceback as _tb

        data = await _json_body(request)
        message = data.get("message", "")
        conversation = data.get("conversation", "")
        project = data.get("project", "")
        task_id = data.get("task_id", "")
        provider_name = data.get("provider", "")
        agent_name = data.get("agent", "")
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)

        if not conversation:
            default_agent = _default_agent_for_project(project)
            meta = chat.create(
                title=message[:80], project=project,
                task_id=task_id,
                provider=provider_name or "auto",
                agent=agent_name or default_agent,
            )
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

        conv_meta = chat.get_meta(conversation) or {}
        proj = (project or conv_meta.get("project") or "").strip()
        default_agent = _default_agent_for_project(proj)
        meta_agent = (conv_meta.get("agent") or "").strip()
        effective_agent = agent_name or meta_agent or default_agent

        provider_override = None
        if provider_name and provider_name != "auto":
            prov = config.get_provider(provider_name)
            if prov:
                from dataclasses import asdict as _asdict
                active = config.active_provider()
                if prov.name != active.name:
                    provider_override = _asdict(prov)

        work = {
            "message": message,
            "conversation": conversation,
            "agent": effective_agent,
            "project": project or proj,
            "spec_path": chat._msg_path(conversation),
            "stream_id": conversation,
            "provider_override": provider_override,
        }

        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        audit = _make_audit(
            "scribe/converse", conversation=conversation,
            agent=effective_agent, project=project or proj,
        )

        async def generate():
            yield _sse("meta", {"conversation": conversation})
            try:
                async with lb.acquire() as worker:
                    audit.record("worker.acquired", phase="server", worker=worker.url)
                    graph = ConverseScribeGraph.from_work(
                        worker, work,
                        agent_store=agent_store,
                        chat=chat,
                        config=config,
                        tracker=tracker,
                        projects=projects,
                        tool_registry=tool_registry,
                        audit=audit,
                    )
                    async for event in graph.run(work):
                        yield _sse(
                            event.get("event_type", "message"),
                            event.get("data", {}),
                        )
            except TimeoutError:
                audit.record("error", phase="server", error="worker timeout")
                yield _sse("error", {"message": "No worker available (timeout waiting for a free worker)."})
            except Exception as exc:
                log.exception("scribe/converse stream error")
                audit.record("error", phase="server", error=str(exc))
                yield _sse("error", {
                    "message": f"{type(exc).__name__}: {exc}",
                    "traceback": _tb.format_exc(),
                })
            finally:
                audit.finalize()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"X-Conversation": conversation},
        )

    # ==================================================================
    # History
    # ==================================================================

    @router.get("/history")
    def agent_history(request: Request):
        conversation = request.query_params.get("conversation", "")
        if not conversation:
            return {"messages": [], "streaming": None}

        messages = chat.read(conversation)
        streaming = None

        active_task, partial = tracker.get_partial(conversation)
        if active_task is not None:
            streaming = {
                "task_id": active_task,
                "partial": partial,
            }

        return {"messages": messages, "streaming": streaming}

    @router.delete("/history")
    def agent_history_clear(request: Request):
        conversation = request.query_params.get("conversation", "")
        if conversation:
            chat.clear(conversation)
        return {"cleared": True}

    # ==================================================================
    # Work endpoints
    # ==================================================================

    @router.post("/work/result/{task_id}")
    async def work_result(task_id: str, request: Request):
        data = await _json_body(request)
        result_text = data.get("result", "")
        error = data.get("error")
        kind = data.get("kind", "")

        task = queue.get(task_id)
        if task is None:
            return JSONResponse({"error": "task not found"}, status_code=404)
        conversation = task.conversation or ""

        ext = task.ext or {}
        is_scheduler_driven = bool(ext.get("scheduler_driven"))

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
                if conversation and not is_scheduler_driven:
                    chat.append(conversation, {
                        "role": "assistant",
                        "content": f"[Error] {error}",
                    })
        else:
            current = queue.get(task_id)
            already_chained = current and current.status == "chained"
            if already_chained:
                queue.update(task_id, result_path=result_path)
            else:
                queue.update(task_id, status=TaskStatus.COMPLETED, result_path=result_path)

            if not is_scheduler_driven:
                reasoning_text = data.get("reasoning", "")
                if kind == "llm_complete" and (result_text or reasoning_text) and conversation and not already_chained:
                    msg: dict = {"role": "assistant", "content": result_text}
                    if reasoning_text:
                        msg["reasoning"] = reasoning_text
                    chat.append(conversation, msg)
                elif kind == "tool_call" and conversation:
                    tool_name = data.get("tool", task.title.replace("tool: ", ""))
                    chat.append(conversation, {
                        "role": "tool_result",
                        "content": result_text or "",
                        "name": tool_name,
                    })

        return {"ok": True}

    # ==================================================================
    # Streaming: SSE endpoint (orchestrator -> UI)
    # ==================================================================

    @router.get("/stream/{stream_id}")
    def stream_sse(stream_id: str):
        """SSE endpoint — UI subscribes by root task id."""
        q = tracker.subscribe(stream_id)

        active_task, partial = tracker.get_partial(stream_id)
        replay = ""
        if active_task is not None and partial:
            replay = (
                f"event: token\n"
                f"data: {json.dumps({'task_id': active_task, 'token': partial, 'index': -1})}\n\n"
            )

        def generate():
            if replay:
                yield replay
            try:
                while True:
                    try:
                        event = q.get(timeout=30)
                    except Exception:
                        yield ": keepalive\n\n"
                        continue

                    etype = event.get("event_type", "message")
                    edata = event.get("data", {})
                    yield f"event: {etype}\ndata: {json.dumps(edata, ensure_ascii=False)}\n\n"

                    if etype in ("done", "error"):
                        break
            finally:
                tracker.unsubscribe(stream_id, q)

        return StreamingResponse(generate(), media_type="text/event-stream")

    # ==================================================================
    # Audit trail endpoints
    # ==================================================================

    @router.get("/audit/{audit_id}")
    def get_audit(audit_id: str):
        audit_dir = config.audit.dir
        path = os.path.join(audit_dir, audit_id, "audit.json")
        if not os.path.isfile(path):
            return JSONResponse({"error": "audit not found"}, status_code=404)
        with open(path) as f:
            return json.load(f)

    @router.get("/audit")
    def list_audits(request: Request):
        audit_dir = config.audit.dir
        if not os.path.isdir(audit_dir):
            return []
        limit = int(request.query_params.get("limit", "20"))
        dirs = sorted(
            [d for d in os.listdir(audit_dir)
             if os.path.isdir(os.path.join(audit_dir, d)) and d != "latest"],
            key=lambda d: os.path.getmtime(os.path.join(audit_dir, d)),
            reverse=True,
        )[:limit]
        results = []
        for d in dirs:
            p = os.path.join(audit_dir, d, "audit.json")
            if not os.path.isfile(p):
                continue
            try:
                with open(p) as f:
                    data = json.load(f)
                results.append({
                    "request_id": data.get("request_id", d),
                    "started_at_iso": data.get("started_at_iso", ""),
                    "total_duration_ms": data.get("total_duration_ms", 0),
                    "meta": data.get("meta", {}),
                })
            except Exception:
                continue
        return results

    # ==================================================================
    # Task queue CRUD
    # ==================================================================

    @router.get("/tasks")
    def list_tasks(request: Request):
        status = request.query_params.get("status")
        project = request.query_params.get("project")
        root_only = request.query_params.get("root_only", "").lower() in ("1", "true", "yes")
        tasks = queue.list(status=status, project=project, root_only=root_only)
        return [_task_json(t) for t in tasks]

    @router.post("/tasks", status_code=201)
    async def create_task(request: Request):
        data = await _json_body(request)
        title = data.get("title", "")
        if not title:
            return JSONResponse({"error": "title is required"}, status_code=400)

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
        return _task_json(task)

    @router.get("/tasks/{task_id}")
    def get_task(task_id: str):
        task = queue.get(task_id)
        if task is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return _task_json(task)

    @router.get("/tasks/{task_id}/tree")
    def get_task_tree(task_id: str):
        tasks = queue.list_tree(task_id)
        if not tasks:
            return JSONResponse({"error": "not found"}, status_code=404)
        return [_task_json(t) for t in tasks]

    @router.patch("/tasks/{task_id}")
    async def update_task(task_id: str, request: Request):
        data = await _json_body(request)
        allowed = {
            "title", "description", "status", "priority",
            "spec", "spec_path", "assigned_to", "depends_on", "max_retries",
            "kind", "gpu", "project", "agent",
        }
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return JSONResponse({"error": "no updatable fields provided"}, status_code=400)

        queue.update(task_id, **fields)
        task = queue.get(task_id)
        if task is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return _task_json(task)

    # ==================================================================
    # Knowledge documents
    # ==================================================================

    @router.get("/knowledge")
    def list_knowledge(request: Request):
        subject = request.query_params.get("subject", "")
        subsubject = request.query_params.get("subsubject", "")
        if not subject and not subsubject:
            return knowledge.tree()
        docs = knowledge.list(subject=subject, subsubject=subsubject)
        return [d.summary() for d in docs]

    @router.get("/knowledge/search")
    def search_knowledge(request: Request):
        query = request.query_params.get("q", "")
        subject = request.query_params.get("subject", "")
        subsubject = request.query_params.get("subsubject", "")
        if not query:
            return JSONResponse({"error": "q parameter is required"}, status_code=400)
        docs = knowledge.search(query, subject=subject, subsubject=subsubject)
        return [d.to_dict() for d in docs]

    @router.post("/knowledge", status_code=201)
    async def create_knowledge(request: Request):
        data = await _json_body(request)
        subject = data.get("subject", "")
        subsubject = data.get("subsubject", "")
        title = data.get("title", "")
        if not subject or not subsubject or not title:
            return JSONResponse(
                {"error": "subject, subsubject, and title are required"},
                status_code=400,
            )
        doc = knowledge.create(
            subject=subject,
            subsubject=subsubject,
            title=title,
            content=data.get("content", ""),
        )
        return doc.to_dict()

    @router.get("/knowledge/{subject}/{subsubject}/{title}")
    def get_knowledge(subject: str, subsubject: str, title: str):
        doc = knowledge.get(subject, subsubject, title)
        if doc is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return doc.to_dict()

    @router.patch("/knowledge/{subject}/{subsubject}/{title}")
    async def update_knowledge(subject: str, subsubject: str, title: str, request: Request):
        data = await _json_body(request)
        content = data.get("content", "")
        if not content:
            return JSONResponse({"error": "content is required"}, status_code=400)
        doc = knowledge.update(subject, subsubject, title, content)
        if doc is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return doc.to_dict()

    @router.post("/knowledge/{subject}/{subsubject}/{title}/append")
    async def append_knowledge(subject: str, subsubject: str, title: str, request: Request):
        data = await _json_body(request)
        content = data.get("content", "")
        if not content:
            return JSONResponse({"error": "content is required"}, status_code=400)
        doc = knowledge.append_content(subject, subsubject, title, content)
        if doc is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return doc.to_dict()

    @router.post("/knowledge/{subject}/{subsubject}/{title}/delete")
    async def delete_knowledge(subject: str, subsubject: str, title: str):
        deleted = knowledge.delete(subject, subsubject, title)
        if not deleted:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"deleted": True}

    # ==================================================================
    # Skills
    # ==================================================================

    @router.get("/skills")
    def list_skills_endpoint(workflow_id: str = ""):
        def _fmt(skills):
            return [
                {
                    "qualified_name": f"skills.{s.namespace}.{s.name}",
                    "namespace": s.namespace,
                    "name": s.name,
                    "description": s.description,
                    "path": s.path,
                }
                for s in skills
            ]
        if workflow_id:
            wf_skills_dirs = [
                os.path.join(d, workflow_id, "skills")
                for d in (workflows_dir, _builtin_wf_dir)
            ]
            dirs = [d for d in wf_skills_dirs if os.path.isdir(d)]
            if dirs:
                with skill_store.scoped(*dirs):
                    return _fmt(skill_store.all_skills())
        return _fmt(skill_store.all_skills())

    @router.get("/skills/{namespace}/{name}")
    def get_skill_endpoint(namespace: str, name: str):
        import json as _json

        tool_json = skill_store.read_file(namespace, name, "tool.json")
        if tool_json is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        code = skill_store.read_file(namespace, name, "run.py") or ""
        readme = skill_store.read_file(namespace, name, "README.md") or ""

        try:
            definition = _json.loads(tool_json)
        except _json.JSONDecodeError:
            definition = {}

        return {
            "qualified_name": f"skills.{namespace}.{name}",
            "namespace": namespace,
            "name": name,
            "definition": definition,
            "code": code,
            "readme": readme,
        }

    @router.post("/skills", status_code=201)
    async def create_skill_endpoint(request: Request):
        import json as _json

        data = await _json_body(request)
        namespace = data.get("namespace", "")
        name = data.get("name", "")
        description = data.get("description", "")

        if not namespace or not name:
            return JSONResponse({"error": "namespace and name are required"}, status_code=400)

        params = data.get("parameters")
        if isinstance(params, str):
            try:
                params = _json.loads(params)
            except _json.JSONDecodeError:
                return JSONResponse({"error": "invalid parameters JSON"}, status_code=400)

        path = skill_store.scaffold(
            namespace=namespace,
            name=name,
            description=description,
            parameters=params,
            code=data.get("code", ""),
            readme=data.get("readme", ""),
        )

        skill_store.register_all(tool_registry)

        return {
            "created": True,
            "qualified_name": f"skills.{namespace}.{name}",
            "path": path,
        }

    @router.put("/skills/{namespace}/{name}/code")
    async def update_skill_code_endpoint(namespace: str, name: str, request: Request):
        data = await _json_body(request)
        code = data.get("code", "")
        if not code:
            return JSONResponse({"error": "code is required"}, status_code=400)

        existing = skill_store.read_file(namespace, name, "tool.json")
        if existing is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        skill_store.write_file(namespace, name, "run.py", code)
        return {"updated": True}

    @router.put("/skills/{namespace}/{name}/definition")
    async def update_skill_definition_endpoint(namespace: str, name: str, request: Request):
        import json as _json

        data = await _json_body(request)

        raw = skill_store.read_file(namespace, name, "tool.json")
        if raw is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        try:
            defn = _json.loads(raw)
        except _json.JSONDecodeError:
            defn = {}

        if "description" in data:
            defn["description"] = data["description"]
        if "parameters" in data:
            params = data["parameters"]
            if isinstance(params, str):
                params = _json.loads(params)
            defn["parameters"] = params

        skill_store.write_file(namespace, name, "tool.json", _json.dumps(defn, indent=2))
        skill_store.register_all(tool_registry)
        return {"updated": True, "definition": defn}

    @router.put("/skills/{namespace}/{name}/readme")
    async def update_skill_readme_endpoint(namespace: str, name: str, request: Request):
        data = await _json_body(request)
        readme = data.get("readme", "")

        existing = skill_store.read_file(namespace, name, "tool.json")
        if existing is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        skill_store.write_file(namespace, name, "README.md", readme)
        return {"updated": True}

    @router.delete("/skills/{namespace}/{name}")
    def delete_skill_endpoint(namespace: str, name: str):
        import shutil

        existing = skill_store.read_file(namespace, name, "tool.json")
        if existing is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        skill_path = os.path.join(skill_store.dir, namespace, name)
        shutil.rmtree(skill_path, ignore_errors=True)

        skill_store.discover()
        return {"deleted": True}

    # ==================================================================
    # Specs
    # ==================================================================

    @router.get("/specs")
    def list_specs():
        specs_dir = config.scribe.specs_dir
        if not os.path.isdir(specs_dir):
            return []
        names = sorted(
            n for n in os.listdir(specs_dir)
            if os.path.isfile(os.path.join(specs_dir, n))
        )
        return names

    @router.get("/specs/{name}")
    def get_spec(name: str):
        path = os.path.join(config.scribe.specs_dir, name)
        if not os.path.isfile(path):
            return JSONResponse({"error": "not found"}, status_code=404)
        with open(path) as f:
            return {"name": name, "content": f.read()}

    # ==================================================================
    # Git worktrees
    # ==================================================================

    @router.get("/worktrees")
    def list_worktrees():
        wts = git.list_worktrees()
        return [
            {"path": w.path, "branch": w.branch, "head": w.head}
            for w in wts
        ]

    # ==================================================================
    # Providers CRUD
    # ==================================================================

    def _provider_json(p: ProviderConfig, active_name: str = "") -> dict:
        from dataclasses import asdict as _asdict
        d = _asdict(p)
        d["active"] = (p.name == active_name)
        return d

    @router.get("/providers")
    def list_providers_route():
        active = config.active_provider()
        return [_provider_json(p, active.name) for p in config.providers]

    @router.post("/providers", status_code=201)
    async def create_provider(request: Request):
        data = await _json_body(request)
        name = data.get("name", "").strip()
        if not name:
            return JSONResponse({"error": "name is required"}, status_code=400)
        if config.get_provider(name) is not None:
            return JSONResponse({"error": f"provider '{name}' already exists"}, status_code=409)

        prov = ProviderConfig.from_dict({**data, "name": name})
        config.providers.append(prov)
        save_providers(config.workspace, config.providers)

        active = config.active_provider()
        return _provider_json(prov, active.name)

    @router.get("/providers/{name}")
    def get_provider_route(name: str):
        prov = config.get_provider(name)
        if prov is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        active = config.active_provider()
        return _provider_json(prov, active.name)

    @router.put("/providers/{name}")
    async def update_provider(name: str, request: Request):
        prov = config.get_provider(name)
        if prov is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        data = await _json_body(request)
        for key in ("backend", "model", "slug", "endpoint", "api_key",
                     "server_port", "launch_template", "max_tokens",
                     "temperature", "context_window", "priority", "roles"):
            if key in data:
                val = data[key]
                if key == "roles" and isinstance(val, str):
                    val = [r.strip() for r in val.split(",") if r.strip()]
                if key in ("server_port", "max_tokens", "context_window", "priority"):
                    val = int(val)
                if key == "temperature":
                    val = float(val)
                setattr(prov, key, val)

        if prov.model and not prov.slug:
            from acai.orchestrator.config import _model_to_slug
            prov.slug = _model_to_slug(prov.model)

        save_providers(config.workspace, config.providers)
        active = config.active_provider()
        return _provider_json(prov, active.name)

    @router.delete("/providers/{name}")
    def delete_provider(name: str):
        prov = config.get_provider(name)
        if prov is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        config.providers = [p for p in config.providers if p.name != name]
        save_providers(config.workspace, config.providers)
        return {"deleted": True}

    @router.post("/providers/{name}/activate")
    def activate_provider(name: str):
        prov = config.get_provider(name)
        if prov is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        config.set_active(name)
        return _provider_json(prov, name)

    # ==================================================================
    # System config
    # ==================================================================

    @router.get("/config")
    def get_config():
        from acai.orchestrator.config import config_to_dict
        return config_to_dict(config)

    @router.patch("/config")
    async def patch_config(request: Request):
        from dataclasses import fields as dc_fields
        from acai.orchestrator.config import config_to_dict, save_config

        data = await _json_body(request)
        for section_name in ("sandbox", "worker", "git", "queue", "audit", "ci"):
            patch = data.get(section_name)
            if not isinstance(patch, dict):
                continue
            obj = getattr(config, section_name)
            known = {f.name for f in dc_fields(type(obj))}
            for k, v in patch.items():
                if k in known:
                    setattr(obj, k, v)

        save_config(config.workspace, config)
        return config_to_dict(config)

    # ==================================================================
    # Status
    # ==================================================================

    @router.get("/status")
    def agent_status():
        counts = {}
        for s in _STATUS_KINDS:
            counts[s] = len(queue.list(status=s))

        active = config.active_provider()
        workers = lb.list_workers()
        return {
            "queue": counts,
            "events": len(events.history),
            "llm_backend": active.backend,
            "llm_endpoint": active.endpoint,
            "active_provider": active.name,
            "providers_count": len(config.providers),
            "workers": len(workers),
            "workers_idle": sum(1 for w in workers if w.status.value == "idle"),
        }

    # ==================================================================
    # Events log
    # ==================================================================

    @router.get("/events")
    def list_events(request: Request):
        limit = int(request.query_params.get("limit", "50"))
        recent = events.history[-limit:]
        return [
            {
                "kind": e.kind.value,
                "source": e.source,
                "data": e.data,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in recent
        ]

    # ==================================================================
    # Projects
    # ==================================================================

    def _project_json(p: Project) -> dict:
        from dataclasses import asdict
        return asdict(p)

    @router.get("/projects")
    def list_projects():
        return [_project_json(p) for p in projects.list()]

    @router.post("/projects", status_code=201)
    async def create_project(request: Request):
        data = await _json_body(request)
        name = data.get("name", "").strip()
        if not name:
            return JSONResponse({"error": "name is required"}, status_code=400)

        slug = name.replace(" ", "-").lower()

        refiner = (data.get("refiner") or "refiner").strip() or "refiner"
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
            refiner=refiner,
        )

        try:
            if proj.source == "clone" and proj.repo_url:
                clone(proj)
            else:
                scaffold(proj)
            projects.save(proj)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

        return _project_json(proj)

    @router.get("/projects/{name}")
    def get_project(name: str):
        proj = projects.get(name)
        if proj is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return _project_json(proj)

    @router.patch("/projects/{name}")
    async def update_project(name: str, request: Request):
        proj = projects.get(name)
        if proj is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        data = await _json_body(request)
        _STR_FIELDS = ("language", "template", "repo_url", "provider",
                        "python_version", "venv_path", "refiner", "path")
        for key in _STR_FIELDS:
            if key in data:
                setattr(proj, key, str(data[key] or "").strip())
        projects.save(proj)
        return _project_json(proj)

    @router.delete("/projects/{name}")
    def delete_project(name: str):
        projects.delete(name)
        return {"deleted": True}

    # ==================================================================
    # Agents CRUD
    # ==================================================================

    def _agent_json(a: AgentDef) -> dict:
        return a.to_dict()

    @router.get("/agents")
    def list_agents(workflow_id: str = ""):
        if workflow_id:
            wf_agents_dirs = [
                os.path.join(d, workflow_id, "agents")
                for d in (workflows_dir, _builtin_wf_dir)
            ]
            dirs = [d for d in wf_agents_dirs if os.path.isdir(d)]
            if dirs:
                with agent_store.scoped(*dirs):
                    return [_agent_json(a) for a in agent_store.list()]
        return [_agent_json(a) for a in agent_store.list()]

    @router.post("/agents", status_code=201)
    async def create_agent(request: Request):
        data = await _json_body(request)
        name = data.get("name", "").strip()
        if not name:
            return JSONResponse({"error": "name is required"}, status_code=400)
        slug = name.replace(" ", "-").lower()
        if agent_store.get(slug) is not None:
            return JSONResponse({"error": f"agent '{slug}' already exists"}, status_code=409)

        agent = AgentDef.from_dict({**data, "name": slug})
        agent_store.scaffold(agent)
        return _agent_json(agent)

    @router.get("/agents/{name}")
    def get_agent(name: str):
        agent = agent_store.get(name)
        if agent is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return _agent_json(agent)

    @router.put("/agents/{name}")
    async def update_agent(name: str, request: Request):
        agent = agent_store.get(name)
        if agent is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        data = await _json_body(request)
        updatable = (
            "description", "role", "avatar", "provider", "output_format",
            "model_overrides", "system_template", "context_sources",
            "tools", "tool_permissions", "resource_permissions", "scope",
            "uses_sandbox", "max_iterations", "approval_required", "tags",
        )
        for key in updatable:
            if key in data:
                val = data[key]
                if key == "uses_sandbox":
                    val = bool(val)
                if key == "max_iterations":
                    val = int(val)
                if key == "approval_required":
                    val = bool(val)
                setattr(agent, key, val)

        agent_store.save(agent)
        agent.builtin = False
        return _agent_json(agent)

    @router.delete("/agents/{name}")
    def delete_agent(name: str):
        agent = agent_store.get(name)
        if agent is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if agent.builtin:
            return JSONResponse({"error": "cannot delete a built-in agent"}, status_code=403)
        agent_store.delete(name)
        remaining = agent_store.get(name)
        return {"deleted": True, "builtin_revealed": remaining is not None}

    @router.get("/agents/{name}/template")
    def get_agent_template(name: str):
        agent = agent_store.get(name)
        if agent is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        content = agent_store.read_template(name)
        return {"name": name, "content": content}

    @router.put("/agents/{name}/template")
    async def update_agent_template(name: str, request: Request):
        agent = agent_store.get(name)
        if agent is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        data = await _json_body(request)
        content = data.get("content", "")
        agent_store.save_template(name, content)
        return {"name": name, "content": content}

    @router.post("/agents/{name}/reset")
    def reset_agent(name: str):
        if not agent_store._is_builtin(name):
            return JSONResponse({"error": "not a built-in agent"}, status_code=400)
        agent_store.delete(name)
        agent = agent_store.get(name)
        if agent is None:
            return JSONResponse({"error": "built-in not found after reset"}, status_code=500)
        return _agent_json(agent)

    # ==================================================================
    # Tool namespaces (from the builtin registry)
    # ==================================================================

    @router.get("/tools/namespaces")
    def list_tool_namespaces():
        result = []
        for ns in tool_registry.namespaces():
            tools = tool_registry.tools_in(ns)
            has_project_scope = any(t.scope_level == "project" for t in tools)
            result.append({
                "namespace": ns,
                "tools": [t.qualified_name for t in tools],
                "resource_permissions": tool_registry.resource_permissions(ns),
                "has_project_scope": has_project_scope,
            })
        return result

    # ==================================================================
    # Toast (worker -> orchestrator -> frontend via WebSocket)
    # ==================================================================

    @router.post("/toast")
    async def receive_toast(request: Request):
        data = await _json_body(request)
        sio = _socketio_ref[0]
        if sio is None:
            log.warning("toast received but SocketIO not initialised")
            return JSONResponse({"error": "socketio not ready"}, status_code=503)

        sio.emit("toast", {
            "message": data.get("message", ""),
            "title": data.get("title", ""),
            "status": data.get("status", "info"),
            "duration": data.get("duration", 5000),
        })
        return {"ok": True}

    # ==================================================================
    # JSON store (file-backed config/data persistence)
    # ==================================================================

    from acai.orchestrator.route_jsonstore import router as jsonstore_router
    router.include_router(jsonstore_router)

    # ==================================================================
    # Auto-update API
    # ==================================================================

    from acai.orchestrator import updater
    from starlette.responses import StreamingResponse as _StreamingResponse

    @router.post("/update")
    async def trigger_update():
        return _StreamingResponse(
            updater.stream_upgrade(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/version")
    def get_version():
        import acai as _pkg
        latest = updater.get_latest_version()
        result: dict = {"version": _pkg.__version__}
        if latest:
            result["latest"] = latest
            result["update_available"] = updater.needs_update(latest)
        return result

    # ==================================================================
    # Git backup (workspace auto-save to GitHub)
    # ==================================================================

    from acai.orchestrator import gitsync
    workspace_path = Path(config.workspace)

    @router.get("/git/status")
    async def git_status_route():
        status = gitsync.get_status(workspace_path)
        status["data_path"] = str(workspace_path.resolve())
        return status

    @router.post("/git/generate-key")
    async def git_generate_key():
        loop = asyncio.get_event_loop()
        pub = await loop.run_in_executor(None, gitsync.generate_ssh_key)
        return {"public_key": pub}

    @router.get("/git/ssh-key")
    async def git_ssh_key():
        pub = gitsync.get_ssh_public_key()
        if pub is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="No SSH key generated yet")
        return {"public_key": pub}

    @router.post("/git/setup")
    async def git_setup(request: Request):
        body = await _json_body(request)
        remote = (body.get("remote") or "").strip()
        if not remote:
            return JSONResponse({"error": "remote is required"}, status_code=400)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, gitsync.git_init, workspace_path, remote)

        result = await loop.run_in_executor(None, gitsync.git_sync, workspace_path)
        gitsync.ensure_sync_running(workspace_path)

        resp: dict = {"message": "Git configured", "remote": remote, "commit": result.commit}
        if result.push_error:
            resp["push_error"] = result.push_error
        if result.error:
            resp["error"] = result.error
        return resp

    @router.post("/git/sync")
    async def git_trigger_sync():
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, gitsync.git_sync, workspace_path)
        resp: dict = {"commit": result.commit, "pushed": result.pushed}
        if result.push_error:
            resp["push_error"] = result.push_error
        if result.error:
            resp["error"] = result.error
        return resp

    @router.post("/git/test")
    async def git_test_connection():
        loop = asyncio.get_event_loop()

        def _test():
            import subprocess as _sp
            r = _sp.run(
                ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new",
                 "git@github.com-acai"],
                capture_output=True, text=True, timeout=15,
            )
            output = (r.stdout + r.stderr).strip()
            return r.returncode == 1 and "successfully authenticated" in output.lower(), output

        ok, output = await loop.run_in_executor(None, _test)
        return {"connected": ok, "output": output}

    return router, queue, events, chat, config, tracker, _socketio_ref, lb


# ------------------------------------------------------------------
# SocketIO setup
# ------------------------------------------------------------------

_STATUS_KINDS = (
    TaskStatus.PENDING, TaskStatus.CURATING, TaskStatus.READY,
    TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED, TaskStatus.FAILED,
    TaskStatus.REVIEW,
)


def setup_socketio(socketio: SocketIO, config: AcaiConfig,
                   queue: WorkQueue, events: EventBus,
                   load_balancer: LoadBalancer | None = None,
                   app=None):
    """Wire SocketIO event handlers."""

    @socketio.on("connect")
    def handle_connect():
        log.debug("WS client connected")
        socketio.emit("capabilities", {"telemetry": True})

    @socketio.on("disconnect")
    def handle_disconnect():
        log.debug("WS client disconnected")

    @socketio.on("join_conversation")
    def handle_join_conversation(data):
        conv_id = data.get("conversation", "") if isinstance(data, dict) else ""
        if conv_id:
            join_room(f"conv:{conv_id}")
            log.debug("client joined room conv:%s", conv_id)

    @socketio.on("leave_conversation")
    def handle_leave_conversation(data):
        conv_id = data.get("conversation", "") if isinstance(data, dict) else ""
        if conv_id:
            leave_room(f"conv:{conv_id}")
            log.debug("client left room conv:%s", conv_id)

    if load_balancer is not None:
        @socketio.on("worker_heartbeat")
        def handle_worker_heartbeat(data):
            if not isinstance(data, dict):
                return
            worker_id = data.get("worker_id", "")
            telemetry = data.get("telemetry", {})
            if worker_id:
                load_balancer.heartbeat(worker_id, telemetry)

    async def _async_emit_loop():
        sio = socketio.server
        while True:
            await asyncio.sleep(2)
            try:
                tasks = queue.list()
                await sio.emit("tasks", [_task_json(t) for t in tasks])

                counts = {s: len(queue.list(status=s)) for s in _STATUS_KINDS}
                active = config.active_provider()
                status_data: dict = {
                    "queue": counts,
                    "events": len(events.history),
                    "llm_backend": active.backend,
                    "llm_endpoint": active.endpoint,
                    "active_provider": active.name,
                    "providers_count": len(config.providers),
                }
                if load_balancer is not None:
                    workers = load_balancer.list_workers()
                    status_data["workers"] = len(workers)
                    status_data["workers_idle"] = sum(
                        1 for w in workers if w.status.value == "idle"
                    )
                await sio.emit("status", status_data)

                recent = events.history[-100:]
                await sio.emit("events", [
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

    if app is not None:
        @app.on_event("startup")
        async def _start_emitter():
            asyncio.create_task(_async_emit_loop())
    else:
        def _sync_emit_loop():
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
                        "llm_backend": active.backend,
                        "llm_endpoint": active.endpoint,
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
        socketio.start_background_task(_sync_emit_loop)


# ------------------------------------------------------------------
# Convenience wrapper
# ------------------------------------------------------------------

def routes(app, config: AcaiConfig | None = None, prefix: str = "/agent",
           load_balancer: LoadBalancer | None = None):
    """Register orchestrator routes and SocketIO on an existing app.

    Returns ``(app, socketio, queue, events, chat, config, stream_tracker, load_balancer)``
    so callers can compose with worker routers (uber mode).
    """
    tracker = StreamTracker()
    lb = load_balancer or LoadBalancer()

    router, queue, events, chat, resolved_config, tracker, sio_ref, lb = create_router(
        config, prefix, stream_tracker=tracker, load_balancer=lb,
    )
    app.include_router(router)

    socketio = SocketIO(app, cors_allowed_origins="*")
    sio_ref[0] = socketio
    setup_socketio(socketio, resolved_config, queue, events,
                   load_balancer=lb, app=app)

    @app.on_event("startup")
    async def _capture_loop():
        from acai.orchestrator.compat import _main_loop_ref
        _main_loop_ref[0] = asyncio.get_running_loop()

    @app.on_event("startup")
    async def _start_background_services():
        from acai.orchestrator import gitsync, updater

        workspace_path = Path(resolved_config.workspace)
        gitsync.start_sync(workspace_path)

        settings_path = workspace_path / "store" / "_config" / "_settings.json"
        if settings_path.is_file():
            import json as _json
            with open(settings_path) as f:
                settings = _json.load(f)
            if settings.get("auto_update"):
                updater.start_update_loop(settings.get("update_interval_hours", 24))

    app.state.workspace = resolved_config.workspace

    return app, socketio, queue, events, chat, resolved_config, tracker, lb
