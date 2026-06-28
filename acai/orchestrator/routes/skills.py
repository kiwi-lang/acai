"""Skills routes — CRUD for user-defined tool skills."""

from __future__ import annotations

import json
import logging
import os
import shutil
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from acai.orchestrator.routes import RouterDeps

log = logging.getLogger(__name__)


async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def create_skills_router(deps: RouterDeps) -> APIRouter:
    """Build the /skills/* router."""

    router = APIRouter(tags=["skills"])
    skill_store = deps.skill_store
    tool_registry = deps.tool_registry
    workflows_dir = deps.workflows_dir
    _builtin_wf_dir = deps.builtin_wf_dir

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
        tool_json = skill_store.read_file(namespace, name, "tool.json")
        if tool_json is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        code = skill_store.read_file(namespace, name, "run.py") or ""
        readme = skill_store.read_file(namespace, name, "README.md") or ""
        requirements = skill_store.read_file(namespace, name, "requirements.txt") or ""

        try:
            definition = json.loads(tool_json)
        except json.JSONDecodeError:
            definition = {}

        return {
            "qualified_name": f"skills.{namespace}.{name}",
            "namespace": namespace,
            "name": name,
            "definition": definition,
            "code": code,
            "readme": readme,
            "requirements": requirements,
        }

    @router.post("/skills", status_code=201)
    async def create_skill_endpoint(request: Request):
        data = await _json_body(request)
        namespace = data.get("namespace", "")
        name = data.get("name", "")
        description = data.get("description", "")

        if not namespace or not name:
            return JSONResponse({"error": "namespace and name are required"}, status_code=400)

        params = data.get("parameters")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                return JSONResponse({"error": "invalid parameters JSON"}, status_code=400)

        path = skill_store.scaffold(
            namespace=namespace,
            name=name,
            description=description,
            parameters=params,
            code=data.get("code", ""),
            readme=data.get("readme", ""),
            requirements=data.get("requirements", ""),
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
        data = await _json_body(request)

        raw = skill_store.read_file(namespace, name, "tool.json")
        if raw is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        try:
            defn = json.loads(raw)
        except json.JSONDecodeError:
            defn = {}

        if "description" in data:
            defn["description"] = data["description"]
        if "parameters" in data:
            params = data["parameters"]
            if isinstance(params, str):
                params = json.loads(params)
            defn["parameters"] = params

        skill_store.write_file(namespace, name, "tool.json", json.dumps(defn, indent=2))
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

    @router.put("/skills/{namespace}/{name}/requirements")
    async def update_skill_requirements_endpoint(namespace: str, name: str, request: Request):
        data = await _json_body(request)
        requirements = data.get("requirements", "")

        existing = skill_store.read_file(namespace, name, "tool.json")
        if existing is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        skill_store.write_file(namespace, name, "requirements.txt", requirements)
        return {"updated": True}

    @router.delete("/skills/{namespace}/{name}")
    def delete_skill_endpoint(namespace: str, name: str):
        existing = skill_store.read_file(namespace, name, "tool.json")
        if existing is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        skill_path = os.path.join(skill_store.dir, namespace, name)
        shutil.rmtree(skill_path, ignore_errors=True)

        skill_store.discover()
        return {"deleted": True}

    return router
