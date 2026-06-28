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
from acai.knowledge import KnowledgeDB, KnowledgeStore
from acai.orchestrator.config import AcaiConfig, load_config
from acai.provider import (
    ProviderConfig, ProviderScheduler, load_providers, save_providers,
    create_provider_router,
)
from acai.orchestrator.projects import Project, ProjectStore, scaffold, clone
from acai.tasks import ConverseGraph, ConverseScribeGraph, ThinkGraph, UberGraph, DynamicGraph, get_graph, list_graphs
from acai.orchestrator.events import EventBus
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
            if task.conversation:
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
    knowledge_db = KnowledgeDB(os.path.join(knowledge_dir, ".knowledge.db"))
    _sync_result = knowledge_db.sync(knowledge_dir)
    if _sync_result.get("added") or _sync_result.get("updated"):
        log.info("knowledge DB synced on startup: %s", _sync_result)

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

    from acai.orchestrator.tts import TTSService, ingest_voice_catalog
    tts_service = TTSService(config.tts, workspace=config.workspace)

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

    # NOTE: Conversations routes moved to routes/conversations.py
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

        provider_name = data.get("provider", "")
        model_slug = data.get("model", "")
        provider_override = None
        if provider_name and provider_name != "auto":
            prov = config.get_provider(provider_name)
            if prov:
                provider_override = {"name": prov.name}
                if model_slug:
                    provider_override["model"] = model_slug

        work = {
            "message": message,
            "current_conversation": current_conversation,
            "agent": agent_name,
            "provider_override": provider_override,
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

    # NOTE: Workflows routes moved to routes/workflows.py
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
        model_slug = data.get("model", "")
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
                provider_override = {"name": prov.name}
                if model_slug:
                    provider_override["model"] = model_slug

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
        model_slug = data.get("model", "")
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
                provider_override = {"name": prov.name}
                if model_slug:
                    provider_override["model"] = model_slug

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

    @router.post("/tasks/{task_id}/run")
    async def run_task(task_id: str):
        import subprocess as _sp
        import traceback as _tb

        task = queue.get(task_id)
        if task is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if task.status == TaskStatus.IN_PROGRESS:
            return JSONResponse(
                {"error": "task is already running"},
                status_code=409,
            )

        is_retry = task.status in (TaskStatus.FAILED, TaskStatus.COMPLETED)

        project_name = task.project or ""
        proj = projects.get(project_name) if project_name else None
        project_path = proj.path if proj else ""

        # --- task clone setup ---
        # Each task gets its own clone so concurrent tasks never clash.
        # The clone is deleted on success; kept on failure for retry.
        clone_path = ""
        branch_name = ""
        if project_path and os.path.isdir(project_path):
            short_id = task_id[:8]
            branch_name = f"acai/task-{short_id}"
            clones_base = os.path.join(os.path.dirname(project_path), ".task-clones")
            os.makedirs(clones_base, exist_ok=True)
            clone_path = os.path.join(clones_base, f"task-{short_id}")

            if os.path.isdir(clone_path):
                log.info("reusing existing task clone for %s: %s", task_id, clone_path)
            else:
                try:
                    _sp.run(
                        ["git", "clone", project_path, clone_path],
                        capture_output=True, text=True, timeout=120, check=True,
                    )
                    # Point origin at the project repo for push
                    _sp.run(
                        ["git", "remote", "set-url", "origin", project_path],
                        cwd=clone_path,
                        capture_output=True, text=True, timeout=10,
                    )
                    # Check if the branch already exists (from a previous run)
                    branch_exists = _sp.run(
                        ["git", "branch", "--list", branch_name],
                        cwd=clone_path,
                        capture_output=True, text=True, timeout=10,
                    ).stdout.strip() != ""

                    if not branch_exists:
                        branch_exists = _sp.run(
                            ["git", "ls-remote", "--heads", "origin", branch_name],
                            cwd=clone_path,
                            capture_output=True, text=True, timeout=10,
                        ).stdout.strip() != ""

                    if branch_exists:
                        _sp.run(
                            ["git", "checkout", branch_name],
                            cwd=clone_path,
                            capture_output=True, text=True, timeout=30, check=True,
                        )
                    else:
                        _sp.run(
                            ["git", "checkout", "-b", branch_name],
                            cwd=clone_path,
                            capture_output=True, text=True, timeout=30, check=True,
                        )
                    log.info("task clone ready: %s  branch=%s", clone_path, branch_name)
                except _sp.CalledProcessError as exc:
                    log.warning("task clone failed for %s: %s", task_id, exc.stderr)
                    clone_path = project_path

        queue.update(task_id, status=TaskStatus.IN_PROGRESS, worktree=clone_path)

        agent_name = task.agent or "coder"
        description = task.description or ""
        message = task.title
        if description:
            message = f"{task.title}\n\n{description}"

        # Task conversations live as conv_N.json in the task dir
        # (never in the ChatStore UI index).  Load prior work from
        # the most recent file for resume context.
        prior_history = chat.task_history(project_name, task_id)
        prior_messages: list[dict] = []
        if prior_history:
            prior_messages = chat.read_task_conversation(prior_history[-1])
            log.info(
                "task %s has %d prior run(s), loading context from %s",
                task_id, len(prior_history), prior_history[-1],
            )

        # Use an ephemeral conversation for live execution so messages
        # are persisted during the run but never show in the UI.
        conv_id = f"ephemeral-task-{task_id}"
        queue.update(task_id, conversation=conv_id)
        chat.append(conv_id, {"role": "user", "content": message})

        active_prov = config.active_provider()
        provider_override = {"name": active_prov.name}

        effective_path = clone_path or project_path
        work = {
            "message": message,
            "conversation": conv_id,
            "agent": agent_name,
            "project": project_name,
            "task_id": task_id,
            "title": task.title,
            "description": description,
            "worktree": effective_path,
            "spec_path": chat._msg_path(conv_id),
            "stream_id": conv_id,
            "kind": "converse",
            "provider_override": provider_override,
            "provider": active_prov.name,
            "prior_work": prior_messages,
        }

        audit = _make_audit(
            "task_run", task_id=task_id,
            agent=agent_name, project=project_name,
            retry=is_retry,
        )

        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        def _commit_and_push(cwd: str, tid: str, msg: str) -> str | None:
            """Fallback: commit partial work when the graph crashes.

            The happy-path commit is handled by ``TaskGraph._finalize_git``.
            This function only runs in error/timeout branches where the
            graph never reached its ``done`` event.

            Returns the short commit hash on success, ``None`` if
            nothing to commit or on error.
            """
            if not cwd or not os.path.isdir(cwd):
                return None
            try:
                status = _sp.run(
                    ["git", "status", "--porcelain"],
                    cwd=cwd, capture_output=True, text=True, timeout=10,
                )
                if not status.stdout.strip():
                    return None
                _sp.run(["git", "add", "-A"], cwd=cwd,
                        capture_output=True, text=True, timeout=30)
                _sp.run(
                    ["git", "commit", "-m", msg],
                    cwd=cwd, capture_output=True, text=True, timeout=30,
                )
                push = _sp.run(
                    ["git", "push", "-u", "origin", "HEAD"],
                    cwd=cwd, capture_output=True, text=True, timeout=60,
                )
                if push.returncode != 0:
                    log.warning("git push failed for task %s: %s", tid, push.stderr.strip())
                head = _sp.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=cwd, capture_output=True, text=True, timeout=10,
                )
                sha = head.stdout.strip()
                log.info("committed work for task %s: %s", tid, sha)
                return sha
            except Exception as exc:
                log.warning("failed to commit work for task %s: %s", tid, exc)
                return None

        def _cleanup_clone(path: str, tid: str) -> None:
            """Remove a task clone directory."""
            import shutil as _shutil
            if not path or path == project_path or not os.path.isdir(path):
                return
            try:
                _shutil.rmtree(path)
                log.info("cleaned up task clone for %s: %s", tid, path)
            except Exception as exc:
                log.warning("failed to clean up clone for %s: %s", tid, exc)

        async def generate():
            import asyncio

            yield _sse("meta", {
                "task_id": task_id, "conversation": conv_id,
                "status": "in_progress", "retry": is_retry,
            })
            try:
                async with lb.acquire() as worker:
                    audit.record("worker.acquired", phase="server", worker=worker.url)
                    graph = get_graph(
                        "converse", worker, work,
                        agent_store=agent_store,
                        chat=chat,
                        config=config,
                        tracker=tracker,
                        projects=projects,
                        tool_registry=tool_registry,
                        audit=audit,
                    )
                    heartbeat_interval = 15
                    ait = graph.run(work).__aiter__()
                    while True:
                        next_task = asyncio.ensure_future(ait.__anext__())
                        while not next_task.done():
                            done, _ = await asyncio.wait(
                                {next_task}, timeout=heartbeat_interval,
                            )
                            if not done:
                                yield _sse("heartbeat", {
                                    "task_id": task_id,
                                    "status": "in_progress",
                                })
                        try:
                            event = next_task.result()
                        except StopAsyncIteration:
                            break
                        yield _sse(
                            event.get("event_type", "message"),
                            event.get("data", {}),
                        )

                queue.update(task_id, status=TaskStatus.COMPLETED)
                _cleanup_clone(clone_path, task_id)
                yield _sse("task_status", {"task_id": task_id, "status": "completed"})
            except TimeoutError:
                audit.record("error", phase="server", error="worker timeout")
                sha = _commit_and_push(
                    effective_path, task_id,
                    f"acai: partial work for task {task_id[:8]} (timeout)",
                )
                if sha:
                    yield _sse("info", {"message": f"Partial work committed: {sha}"})
                queue.update(task_id, status=TaskStatus.FAILED, error_log="No worker available (timeout)")
                yield _sse("error", {"message": "No worker available (timeout waiting for a free worker)."})
                yield _sse("task_status", {"task_id": task_id, "status": "failed"})
            except Exception as exc:
                log.exception("task run error for %s", task_id)
                error_msg = f"{type(exc).__name__}: {exc}"
                audit.record("error", phase="server", error=error_msg)
                sha = _commit_and_push(
                    effective_path, task_id,
                    f"acai: partial work for task {task_id[:8]} (error)",
                )
                if sha:
                    yield _sse("info", {"message": f"Partial work committed: {sha}"})
                queue.update(task_id, status=TaskStatus.FAILED, error_log=error_msg)
                yield _sse("error", {"message": error_msg, "traceback": _tb.format_exc()})
                yield _sse("task_status", {"task_id": task_id, "status": "failed"})
            finally:
                # Persist the ephemeral conversation to conv_N.json
                # in the task dir and clean up the tmp directory.
                try:
                    final_messages = chat.read(conv_id)
                    if final_messages and project_name:
                        saved = chat.save_task_conversation(
                            project_name, task_id, final_messages,
                        )
                        log.info("saved task conversation: %s", saved)
                    chat.delete(conv_id)
                except Exception:
                    log.warning("failed to persist task conversation for %s", task_id, exc_info=True)
                audit.finalize()
                yield _sse("done", {"task_id": task_id})

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"X-Task-Id": task_id, "X-Conversation": conv_id},
        )


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
    # Providers CRUD (delegated to acai.provider.routes)
    # ==================================================================
    router.include_router(create_provider_router(config))

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
        for section_name in ("sandbox", "worker", "git", "queue", "audit", "ci", "tts"):
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
    # TTS
    # ==================================================================

    @router.get("/tts/voices")
    def tts_voices():
        return tts_service.list_voices()

    @router.post("/tts/voices/catalog")
    async def tts_ingest_catalog(request: Request):
        data = await _json_body(request)
        catalog = data.get("catalog")
        if not catalog or not isinstance(catalog, dict):
            return JSONResponse({"error": "catalog dict is required"}, status_code=400)
        ingest_voice_catalog(catalog)
        return tts_service.list_voices()

    @router.post("/tts/download")
    async def tts_download(request: Request):
        data = await _json_body(request)
        voice_id = data.get("voice", config.tts.voice)
        if not voice_id:
            return JSONResponse({"error": "voice is required"}, status_code=400)

        import asyncio, queue as _queue

        progress_q: _queue.Queue[dict] = _queue.Queue()

        def _on_progress(received: int, total: int) -> None:
            pct = int(received * 100 / total) if total else 0
            progress_q.put({
                "received": received,
                "total": total,
                "percent": pct,
            })

        async def _stream():
            def _sse(event: str, data: dict) -> str:
                return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            loop = asyncio.get_event_loop()
            fut = loop.run_in_executor(
                None, lambda: tts_service.download_voice(voice_id, on_progress=_on_progress),
            )

            while not fut.done():
                await asyncio.sleep(0.15)
                while not progress_q.empty():
                    try:
                        yield _sse("progress", progress_q.get_nowait())
                    except _queue.Empty:
                        break

            try:
                path = fut.result()
                while not progress_q.empty():
                    try:
                        yield _sse("progress", progress_q.get_nowait())
                    except _queue.Empty:
                        break
                yield _sse("done", {"voice": voice_id, "path": path, "downloaded": True})
            except Exception as exc:
                log.exception("TTS download failed for %s", voice_id)
                yield _sse("error", {"message": str(exc)})

        return StreamingResponse(_stream(), media_type="text/event-stream")

    @router.post("/tts/synthesize")
    async def tts_synthesize(request: Request):
        data = await _json_body(request)
        text = data.get("text", "")
        stream = data.get("stream", False)

        if not tts_service.enabled:
            return JSONResponse({"error": "TTS is not enabled"}, status_code=400)
        if not text:
            return JSONResponse({"error": "text is required"}, status_code=400)

        if not stream:
            wav_bytes = tts_service.synthesize(text)
            return Response(content=wav_bytes, media_type="audio/wav")

        async def _stream_audio():
            sentences = tts_service.split_sentences(text)
            for sentence in sentences:
                pcm = tts_service.synthesize_pcm(sentence)
                payload = tts_service.audio_event(pcm)
                yield _sse("audio", payload)
            yield _sse("done", {})

        return StreamingResponse(_stream_audio(), media_type="text/event-stream")

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

    # ------------------------------------------------------------------
    # Sub-routers (extracted from this monolith — see acai/orchestrator/routes/)
    # ------------------------------------------------------------------
    from acai.orchestrator.routes import RouterDeps
    from acai.orchestrator.routes.knowledge import create_knowledge_router
    from acai.orchestrator.routes.skills import create_skills_router
    from acai.orchestrator.routes.agents import create_agents_router
    from acai.orchestrator.routes.projects import create_projects_router
    from acai.orchestrator.routes.git import create_git_router
    from acai.orchestrator.routes.workflows import create_workflows_router
    from acai.orchestrator.routes.conversations import create_conversations_router

    _deps = RouterDeps(
        config=config,
        queue=queue,
        chat=chat,
        agent_store=agent_store,
        knowledge=knowledge,
        knowledge_db=knowledge_db,
        skill_store=skill_store,
        tool_registry=tool_registry,
        projects=projects,
        tracker=tracker,
        events=events,
        load_balancer=lb,
        workflows_dir=workflows_dir,
        builtin_wf_dir=_builtin_wf_dir,
        socketio_ref=_socketio_ref,
    )

    # Register sub-routers (these provide the same endpoints as the inline
    # definitions above — during migration, the inline versions take priority
    # since they are registered first on the same router object).
    # Once the inline route blocks are removed, only these remain.
    _knowledge_rt = create_knowledge_router(_deps)
    _skills_rt = create_skills_router(_deps)
    _agents_rt = create_agents_router(_deps)
    _projects_rt = create_projects_router(_deps)
    _git_rt = create_git_router(_deps)
    _workflows_rt = create_workflows_router(_deps, make_audit=_make_audit, extra_wf_dirs=_extra_wf_dirs)
    _conversations_rt = create_conversations_router(_deps, scheduler=scheduler, make_audit=_make_audit)

    router.include_router(_knowledge_rt)
    router.include_router(_skills_rt)
    router.include_router(_agents_rt)
    router.include_router(_projects_rt)
    router.include_router(_git_rt)
    router.include_router(_workflows_rt)
    router.include_router(_conversations_rt)

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
