"""Conversation routes — CRUD, converse (SSE streaming), context stats."""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from acai.queue.work import TaskStatus
from acai.tasks import get_graph, list_graphs

if TYPE_CHECKING:
    from acai.orchestrator.routes import RouterDeps
    from acai.provider import ProviderScheduler

log = logging.getLogger(__name__)


async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def create_conversations_router(
    deps: RouterDeps,
    *,
    scheduler: ProviderScheduler,
    make_audit: Callable[..., Any],
) -> APIRouter:
    """Build the /conversations/* and /converse routes."""

    router = APIRouter(tags=["conversations"])
    config = deps.config
    chat = deps.chat
    queue = deps.queue
    projects = deps.projects
    agent_store = deps.agent_store
    tool_registry = deps.tool_registry
    tracker = deps.tracker
    lb = deps.load_balancer
    workflows_dir = deps.workflows_dir
    _builtin_wf_dir = deps.builtin_wf_dir

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

    @router.get("/graphs")
    def get_graphs():
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

        model_slug = data.get("model", "")

        provider_override = None
        if provider_name and provider_name != "auto":
            prov = config.get_provider(provider_name)
            if prov:
                provider_override = {"name": prov.name}
                if model_slug:
                    provider_override["model"] = model_slug

        enable_thinking = data.get("enable_thinking")

        resolved_provider = (
            config.get_provider(provider_name) if provider_name and provider_name != "auto"
            else config.active_provider()
        ) or config.active_provider()

        work = {
            "message": message,
            "conversation": conversation,
            "agent": agent_name,
            "project": project,
            "spec_path": chat._msg_path(conversation),
            "stream_id": conversation,
            "provider_override": provider_override,
            "provider": provider_name,
            "model": model_slug or resolved_provider.model_slug,
            "enable_thinking": enable_thinking,
        }
        if task_id:
            work["task_id"] = task_id
        if workflow_spec:
            work["workflow_spec"] = workflow_spec
        if workflow_dir:
            work["workflow_dir"] = workflow_dir

        extra_ctx = data.get("context")
        if not isinstance(extra_ctx, dict):
            extra_ctx = {}
        if task_id:
            task_obj = queue.get(task_id)
            if task_obj:
                extra_ctx["current_task"] = {
                    "id": task_obj.id,
                    "title": task_obj.title,
                    "description": task_obj.description or "",
                    "kind": task_obj.kind,
                    "status": task_obj.status,
                    "priority": task_obj.priority,
                    "agent": task_obj.agent or "",
                }
        if extra_ctx:
            work["extra_context"] = extra_ctx

        def _sse(event: str, data_payload: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data_payload, ensure_ascii=False)}\n\n"

        audit = make_audit(
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
                        task_runner=deps.task_runner,
                        input_queue=deps.input_queue,
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
                summary = audit.client_summary()
                if summary.get("request_id"):
                    yield _sse("audit_complete", summary)

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
        active = scheduler.default() or config.active_provider()
        max_context = active.context_window
        return {
            "estimated_tokens": estimated_tokens,
            "max_context": max_context,
            "message_count": len(messages),
        }

    @router.post("/conversations/{conv_id}/input")
    async def conversation_input(conv_id: str, request: Request):
        """Submit the user's answer to a pending interaction tool."""
        data = await request.json()
        text = data.get("text", "").strip()
        if not text:
            return {"ok": False, "error": "empty response"}

        input_queue = deps.input_queue
        if not input_queue or not input_queue.has_pending(conv_id):
            return {"ok": False, "error": "no pending interaction"}

        input_queue.submit_input(conv_id, {"text": text})
        return {"ok": True}

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

    return router
