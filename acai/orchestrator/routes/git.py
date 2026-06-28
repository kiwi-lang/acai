"""Git sync routes — status, SSH key management, sync triggers."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from acai.orchestrator import gitsync

if TYPE_CHECKING:
    from acai.orchestrator.routes import RouterDeps

log = logging.getLogger(__name__)


async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def create_git_router(deps: RouterDeps) -> APIRouter:
    """Build the /git/* router."""

    router = APIRouter(tags=["git"])
    workspace_path = Path(deps.config.workspace)

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
            r = subprocess.run(
                ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new",
                 "git@github.com-acai"],
                capture_output=True, text=True, timeout=15,
            )
            output = (r.stdout + r.stderr).strip()
            return r.returncode == 1 and "successfully authenticated" in output.lower(), output

        ok, output = await loop.run_in_executor(None, _test)
        return {"connected": ok, "output": output}

    return router
