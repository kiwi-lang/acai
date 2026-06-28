"""FastAPI application for the dev spawner.

Provides REST endpoints to list, start, stop, restart services
and retrieve their log output.
"""

from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from acai.devserver.manager import ProcessManager


def create_dev_app(manager: ProcessManager) -> FastAPI:
    """Build the dev spawner FastAPI application."""

    app = FastAPI(title="ACAI Dev Spawner", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/dev/services")
    def list_services():
        return manager.status_all()

    @app.get("/dev/services/{name}")
    def get_service(name: str):
        info = manager.status(name)
        if info is None:
            return JSONResponse({"error": f"unknown service: {name}"}, status_code=404)
        return info

    @app.post("/dev/services/{name}/start")
    def start_service(name: str):
        result = manager.start(name)
        if "error" in result:
            return JSONResponse(result, status_code=400)
        return result

    @app.post("/dev/services/{name}/stop")
    def stop_service(name: str):
        result = manager.stop(name)
        if "error" in result:
            return JSONResponse(result, status_code=400)
        return result

    @app.post("/dev/services/{name}/restart")
    def restart_service(name: str):
        result = manager.restart(name)
        if "error" in result:
            return JSONResponse(result, status_code=400)
        return result

    @app.get("/dev/services/{name}/logs")
    def get_logs(name: str, tail: int = Query(default=100, ge=1, le=5000)):
        lines = manager.logs(name, tail=tail)
        if lines is None:
            return JSONResponse({"error": f"unknown service: {name}"}, status_code=404)
        return {"name": name, "lines": lines, "count": len(lines)}

    return app
