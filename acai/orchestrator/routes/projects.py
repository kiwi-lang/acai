"""Project CRUD routes."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from acai.orchestrator.projects import Project, scaffold, clone

if TYPE_CHECKING:
    from acai.orchestrator.routes import RouterDeps

log = logging.getLogger(__name__)


async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def create_projects_router(deps: RouterDeps) -> APIRouter:
    """Build the /projects/* router."""

    router = APIRouter(tags=["projects"])
    projects = deps.projects
    config = deps.config

    def _project_json(p: Project) -> dict:
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

    return router
