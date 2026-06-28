"""Agent CRUD routes — list, create, update, delete, template management."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from acai.orchestrator.agent_store import AgentDef

if TYPE_CHECKING:
    from acai.orchestrator.routes import RouterDeps

log = logging.getLogger(__name__)


async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def create_agents_router(deps: RouterDeps) -> APIRouter:
    """Build the /agents/* router."""

    router = APIRouter(tags=["agents"])
    agent_store = deps.agent_store
    workflows_dir = deps.workflows_dir
    _builtin_wf_dir = deps.builtin_wf_dir

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
            "provider_allow", "provider_forbid",
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

    return router
