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

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response, StreamingResponse

from assai.orchestrator.compat import SocketIO, join_room, leave_room

from assai.orchestrator.agent_store import AgentDef, AgentStore, hydrate_task, resolve_task
from assai.orchestrator.load_balancer import LoadBalancer
from assai.orchestrator.stream import StreamTracker
from assai.orchestrator.chat import ChatStore
from assai.orchestrator.config import (
    AssaiConfig, ProviderConfig, load_config, load_providers, save_providers,
)
from assai.orchestrator.projects import Project, ProjectStore, scaffold, clone
from assai.scheduler import ProviderScheduler
from assai.tasks import ConverseGraph, ThinkGraph, UberGraph, DynamicGraph
from assai.events import EventBus
from assai.queue.work import TaskStatus, WorkQueue
from assai.tracker.git import GitTracker

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

    def __init__(self, config: AssaiConfig, queue: WorkQueue,
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

def create_router(config: AssaiConfig | None = None,
                  prefix: str = "/agent",
                  stream_tracker: StreamTracker | None = None,
                  load_balancer: LoadBalancer | None = None):
    """Build the orchestrator APIRouter.

    Returns ``(router, queue, events, chat, config, stream_tracker,
    socketio_ref, load_balancer)`` so the caller can compose with
    SocketIO and worker routers.
    """
    if config is None:
        config = AssaiConfig()

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

    from assai.orchestrator.tools import discover_tools
    tool_registry = discover_tools()
    from assai.tools.meta import _configure as configure_meta_tools

    configure_meta_tools(tool_registry)

    from assai.utils.audit import AuditTrail, NullAuditTrail

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
    def list_conversations():
        return [_enrich_conversation_dict(m) for m in chat.list()]

    @router.post("/conversations", status_code=201)
    async def create_conversation(request: Request):
        data = await _json_body(request)
        proj = data.get("project", "")
        default_agent = _default_agent_for_project(proj)
        meta = chat.create(
            title=data.get("title", ""),
            project=proj,
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

    @router.post("/converse")
    async def agent_converse(request: Request):
        import traceback as _tb

        data = await _json_body(request)
        message = data.get("message", "")
        conversation = data.get("conversation", "")
        project = data.get("project", "")
        provider_name = data.get("provider", "auto")
        agent_name = data.get("agent", "") or _default_agent_for_project(project)

        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)

        if not conversation:
            meta = chat.create(
                title=message[:80], project=project,
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

        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        audit = _make_audit(
            "converse", conversation=conversation,
            agent=agent_name, project=project,
        )

        async def generate():
            yield _sse("meta", {"conversation": conversation})
            try:
                async with lb.acquire() as worker:
                    audit.record("worker.acquired", phase="server", worker=worker.url)
                    graph = ConverseGraph.from_work(
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

    _builtin_wf_dir = os.path.join(os.path.dirname(__file__), os.pardir, "agents", "dynamic")
    _builtin_wf_dir = os.path.normpath(_builtin_wf_dir)

    def _scan_wf_dir(directory: str, builtin: bool) -> list[dict]:
        results = []
        if not os.path.isdir(directory):
            return results
        for fname in sorted(os.listdir(directory)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(directory, fname)
            try:
                with open(path) as f:
                    spec = json.load(f)
                results.append({
                    "id": spec.get("id", fname[:-5]),
                    "name": spec.get("name", fname[:-5]),
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
        from assai.tasks.nodes import all_types
        return [nt.to_dict() for nt in all_types()]

    @router.get("/workflows/agent-inputs/{agent_name}")
    def get_agent_template_inputs(agent_name: str):
        """Return custom template variables for an agent."""
        return {"agent": agent_name,
                "inputs": agent_store.template_inputs(agent_name)}

    @router.get("/workflows")
    def list_workflows():
        user_wfs = _scan_wf_dir(workflows_dir, builtin=False)
        user_ids = {w["id"] for w in user_wfs}
        builtin_wfs = [w for w in _scan_wf_dir(_builtin_wf_dir, builtin=True)
                       if w["id"] not in user_ids]
        return builtin_wfs + user_wfs

    @router.get("/workflows/{workflow_id}")
    def get_workflow(workflow_id: str):
        user_path = os.path.join(workflows_dir, f"{workflow_id}.json")
        if os.path.isfile(user_path):
            with open(user_path) as f:
                spec = json.load(f)
            spec["builtin"] = False
            return spec
        builtin_path = os.path.join(_builtin_wf_dir, f"{workflow_id}.json")
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
        path = os.path.join(workflows_dir, f"{wf_id}.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    @router.put("/workflows/builtin/{workflow_id}")
    async def save_builtin_workflow(workflow_id: str, request: Request):
        """Dev-mode: overwrite a builtin workflow JSON in-place."""
        data = await _json_body(request)
        data["id"] = workflow_id
        data.setdefault("name", workflow_id)
        path = os.path.join(_builtin_wf_dir, f"{workflow_id}.json")
        os.makedirs(_builtin_wf_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    @router.put("/workflows/{workflow_id}")
    async def update_workflow(workflow_id: str, request: Request):
        data = await _json_body(request)
        data["id"] = workflow_id
        data.setdefault("name", workflow_id)
        path = os.path.join(workflows_dir, f"{workflow_id}.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    @router.delete("/workflows/{workflow_id}")
    def delete_workflow(workflow_id: str):
        path = os.path.join(workflows_dir, f"{workflow_id}.json")
        if os.path.isfile(path):
            os.remove(path)
        return {"deleted": True}

    @router.post("/workflows/{workflow_id}/run")
    async def run_workflow(workflow_id: str, request: Request):
        import traceback as _tb

        path = os.path.join(workflows_dir, f"{workflow_id}.json")
        if not os.path.isfile(path):
            path = os.path.join(_builtin_wf_dir, f"{workflow_id}.json")
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
        provider_name = data.get("provider", "")
        agent_name = data.get("agent", "")
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)

        if not conversation:
            default_agent = _default_agent_for_project(project)
            meta = chat.create(
                title=message[:80], project=project,
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
                    result_preview = result_text[:500] if result_text else ""
                    chat.append(conversation, {
                        "role": "tool_result",
                        "content": result_preview,
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
            from assai.orchestrator.config import _model_to_slug
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
    def list_agents():
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
            "tools", "sandbox", "max_iterations", "approval_required", "tags",
        )
        for key in updatable:
            if key in data:
                val = data[key]
                if key == "sandbox" and isinstance(val, dict):
                    from assai.orchestrator.agent_store import SandboxConfig
                    val = SandboxConfig(**val)
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
            result.append({
                "namespace": ns,
                "tools": [t.qualified_name for t in tools],
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

    return router, queue, events, chat, config, tracker, _socketio_ref, lb


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

def routes(app, config: AssaiConfig | None = None, prefix: str = "/agent",
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
        from assai.orchestrator.compat import _main_loop_ref
        _main_loop_ref[0] = asyncio.get_running_loop()

    return app, socketio, queue, events, chat, resolved_config, tracker, lb
