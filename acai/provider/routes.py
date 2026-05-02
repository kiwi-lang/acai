"""FastAPI sub-router for provider CRUD, /models, and /fetch-models."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from acai.provider.config import (
    ModelConfig,
    ProviderConfig,
    _provider_to_dict,
    save_providers,
)
from acai.provider.registry import _FETCH_MAP

if TYPE_CHECKING:
    from acai.orchestrator.config import AcaiConfig

log = logging.getLogger(__name__)


async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def _provider_json(p: ProviderConfig, active_name: str = "") -> dict:
    """Build the JSON representation of a provider for API responses."""
    d = {
        "name": p.name,
        "backend": p.backend,
        "endpoint": p.endpoint,
        "api_key": p.api_key,
        "server_port": p.server_port,
        "server_command": p.launch_template,
        "max_tokens": p.max_tokens,
        "temperature": p.temperature,
        "context_window": p.context_window,
        "priority": p.priority,
        "models": [asdict(m) for m in p.models],
        "active": (p.name == active_name),
        "supports_thinking": p.supports_thinking,
    }
    return d


def create_provider_router(config: AcaiConfig) -> APIRouter:
    """Build and return a FastAPI router with all provider endpoints."""
    router = APIRouter()

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
        for key in ("backend", "endpoint", "api_key",
                     "server_port", "launch_template", "max_tokens",
                     "temperature", "context_window", "priority"):
            if key in data:
                val = data[key]
                if key in ("server_port", "max_tokens", "context_window", "priority"):
                    val = int(val)
                if key == "temperature":
                    val = float(val)
                setattr(prov, key, val)

        if "models" in data and isinstance(data["models"], list):
            prov.models = [
                ModelConfig.from_dict(m) if isinstance(m, dict) else m
                for m in data["models"]
            ]

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
        config.providers = [prov] + [p for p in config.providers if p.name != name]
        config.set_active(name)
        save_providers(config.workspace, config.providers)
        return _provider_json(prov, name)

    @router.get("/models")
    def list_all_models(request: Request):
        """Flat list of models across all providers.

        Query params:
          ``limit`` -- max non-default models per provider (default 10).
                       The default model (position 0) is always included.
                       Set to 0 for unlimited.
        """
        qs_limit = request.query_params.get("limit", "10")
        try:
            per_provider = int(qs_limit)
        except ValueError:
            per_provider = 10
        result: list[dict] = []
        for p in config.providers:
            for idx, m in enumerate(p.models):
                if per_provider > 0 and idx > per_provider:
                    break
                resolved = p.resolve_model(m)
                d = asdict(resolved)
                d["provider"] = p.name
                d["is_default"] = (idx == 0)
                d["total_models"] = len(p.models)
                result.append(d)
        return result

    @router.post("/providers/{name}/fetch-models")
    async def fetch_provider_models(name: str):
        """Query the provider's API for available models."""
        import requests as _requests

        prov = config.get_provider(name)
        if prov is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        fetcher = _FETCH_MAP.get(prov.backend)
        if fetcher is None:
            return JSONResponse(
                {"error": f"fetch-models not supported for backend '{prov.backend}'"},
                status_code=400,
            )

        try:
            return fetcher(prov)
        except _requests.RequestException as exc:
            log.warning("fetch-models failed for %s: %s", name, exc)
            return JSONResponse({"error": str(exc)}, status_code=502)

    return router
