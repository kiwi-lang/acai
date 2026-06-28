"""Unit tests for acai/provider/routes.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acai.provider.config import (
    ModelConfig,
    ModelSet,
    ModelSetEntry,
    ProviderConfig,
)
from acai.provider.routes import create_provider_router


# ---------------------------------------------------------------------------
# Lightweight fake for AcaiConfig (only the fields / methods the router uses)
# ---------------------------------------------------------------------------

@dataclass
class _FakeConfig:
    workspace: str = "/tmp/test-workspace"
    providers: list[ProviderConfig] = field(default_factory=list)
    model_sets: list[ModelSet] = field(default_factory=list)
    _active_name: str = ""

    def active_provider(self) -> ProviderConfig:
        if self._active_name:
            p = self.get_provider(self._active_name)
            if p:
                return p
        return self.providers[0] if self.providers else ProviderConfig(name="default")

    def set_active(self, name: str) -> None:
        self._active_name = name

    def get_provider(self, name: str) -> ProviderConfig | None:
        for p in self.providers:
            if p.name == name:
                return p
        return None

    def get_model_set(self, name: str) -> ModelSet | None:
        for ms in self.model_sets:
            if ms.name == name:
                return ms
        return None


def _provider(name: str = "test-prov", **kw) -> ProviderConfig:
    defaults = dict(
        backend="openai",
        endpoint="http://localhost:9123",
        api_key="sk-test",
        server_port=9123,
        launch_template="",
        max_tokens=4096,
        temperature=1.0,
        context_window=128000,
        priority=0,
        models=[ModelConfig(name="gpt-test", slug="gpt-test")],
    )
    defaults.update(kw)
    return ProviderConfig(name=name, **defaults)


def _model_set(name: str = "default-set", default: bool = False, entries=None) -> ModelSet:
    return ModelSet(
        name=name,
        default=default,
        entries=entries or [ModelSetEntry(provider="p1", model="m1")],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return _FakeConfig(providers=[_provider("alpha"), _provider("beta")])


@pytest.fixture
def client(config):
    app = FastAPI()
    with patch("acai.provider.routes.save_providers"), \
         patch("acai.provider.routes.save_model_sets"):
        router = create_provider_router(config)
        app.include_router(router)
        yield TestClient(app)


# ===================================================================
# Provider CRUD
# ===================================================================

class TestListProviders:
    def test_returns_all(self, client, config):
        resp = client.get("/providers")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert names == ["alpha", "beta"]

    def test_active_flag(self, client, config):
        config._active_name = "beta"
        resp = client.get("/providers")
        data = resp.json()
        assert not data[0]["active"]
        assert data[1]["active"]

    def test_response_keys(self, client):
        resp = client.get("/providers")
        keys = set(resp.json()[0].keys())
        expected = {
            "name", "backend", "endpoint", "api_key", "server_port",
            "server_command", "max_tokens", "temperature", "context_window",
            "priority", "models", "active", "supports_thinking",
        }
        assert keys == expected


class TestCreateProvider:
    def test_success(self, client, config):
        resp = client.post("/providers", json={"name": "new-prov", "backend": "vllm"})
        assert resp.status_code == 201
        assert resp.json()["name"] == "new-prov"
        assert len(config.providers) == 3

    def test_missing_name(self, client, config):
        resp = client.post("/providers", json={"backend": "openai"})
        assert resp.status_code == 400
        assert "name is required" in resp.json()["error"]

    def test_empty_name(self, client, config):
        resp = client.post("/providers", json={"name": "  "})
        assert resp.status_code == 400

    def test_duplicate(self, client, config):
        resp = client.post("/providers", json={"name": "alpha"})
        assert resp.status_code == 409
        assert "already exists" in resp.json()["error"]


class TestGetProvider:
    def test_found(self, client):
        resp = client.get("/providers/alpha")
        assert resp.status_code == 200
        assert resp.json()["name"] == "alpha"

    def test_not_found(self, client):
        resp = client.get("/providers/missing")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not found"


class TestUpdateProvider:
    def test_update_scalar_fields(self, client, config):
        resp = client.put("/providers/alpha", json={
            "backend": "vllm",
            "endpoint": "http://new:8080",
            "api_key": "sk-new",
            "server_port": "8080",
            "max_tokens": "8192",
            "temperature": "0.5",
            "context_window": "64000",
            "priority": "10",
        })
        assert resp.status_code == 200
        prov = config.get_provider("alpha")
        assert prov.backend == "vllm"
        assert prov.endpoint == "http://new:8080"
        assert prov.api_key == "sk-new"
        assert prov.server_port == 8080
        assert prov.max_tokens == 8192
        assert prov.temperature == 0.5
        assert prov.context_window == 64000
        assert prov.priority == 10

    def test_rename(self, client, config):
        resp = client.put("/providers/alpha", json={"name": "alpha-v2"})
        assert resp.status_code == 200
        assert config.get_provider("alpha-v2") is not None
        assert config.get_provider("alpha") is None

    def test_rename_to_empty_rejected(self, client):
        resp = client.put("/providers/alpha", json={"name": "  "})
        assert resp.status_code == 400
        assert "name cannot be empty" in resp.json()["error"]

    def test_rename_duplicate(self, client):
        resp = client.put("/providers/alpha", json={"name": "beta"})
        assert resp.status_code == 409
        assert "already exists" in resp.json()["error"]

    def test_rename_updates_active(self, client, config):
        config._active_name = "alpha"
        resp = client.put("/providers/alpha", json={"name": "alpha-v2"})
        assert resp.status_code == 200
        assert config._active_name == "alpha-v2"

    def test_rename_same_name_no_conflict(self, client):
        resp = client.put("/providers/alpha", json={"name": "alpha"})
        assert resp.status_code == 200

    def test_update_models(self, client, config):
        resp = client.put("/providers/alpha", json={
            "models": [{"name": "new-model", "slug": "nm"}],
        })
        assert resp.status_code == 200
        prov = config.get_provider("alpha")
        assert len(prov.models) == 1
        assert prov.models[0].slug == "nm"

    def test_update_not_found(self, client):
        resp = client.put("/providers/missing", json={"backend": "openai"})
        assert resp.status_code == 404

    def test_update_launch_template(self, client, config):
        resp = client.put("/providers/alpha", json={
            "launch_template": "custom-cmd --port {server_port}",
        })
        assert resp.status_code == 200
        assert config.get_provider("alpha").launch_template == "custom-cmd --port {server_port}"


class TestDeleteProvider:
    def test_success(self, client, config):
        resp = client.delete("/providers/alpha")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert config.get_provider("alpha") is None
        assert len(config.providers) == 1

    def test_not_found(self, client):
        resp = client.delete("/providers/missing")
        assert resp.status_code == 404


class TestActivateProvider:
    def test_success(self, client, config):
        resp = client.post("/providers/beta/activate")
        assert resp.status_code == 200
        assert resp.json()["active"] is True
        assert config.providers[0].name == "beta"

    def test_not_found(self, client):
        resp = client.post("/providers/missing/activate")
        assert resp.status_code == 404


# ===================================================================
# Models listing
# ===================================================================

class TestListModels:
    def test_flat_list(self, client, config):
        resp = client.get("/models")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert all(d["provider"] in ("alpha", "beta") for d in data)
        assert all(d["is_default"] for d in data)

    def test_limit_param(self, client, config):
        config.providers = [
            _provider("multi", models=[
                ModelConfig(name=f"m{i}", slug=f"m{i}") for i in range(20)
            ]),
        ]
        resp = client.get("/models?limit=3")
        data = resp.json()
        assert len(data) == 4  # default (idx 0) + 3 non-default

    def test_limit_zero_unlimited(self, client, config):
        config.providers = [
            _provider("many", models=[
                ModelConfig(name=f"m{i}", slug=f"m{i}") for i in range(15)
            ]),
        ]
        resp = client.get("/models?limit=0")
        data = resp.json()
        assert len(data) == 15

    def test_invalid_limit_defaults(self, client, config):
        resp = client.get("/models?limit=abc")
        assert resp.status_code == 200

    def test_model_has_total_models_count(self, client, config):
        resp = client.get("/models")
        for d in resp.json():
            assert "total_models" in d
            assert d["total_models"] == 1


# ===================================================================
# Fetch models
# ===================================================================

class TestFetchModels:
    def test_not_found(self, client):
        resp = client.post("/providers/missing/fetch-models")
        assert resp.status_code == 404

    def test_unsupported_backend(self, client, config):
        config.providers.append(_provider("custom", backend="unknown-backend"))
        resp = client.post("/providers/custom/fetch-models")
        assert resp.status_code == 400
        assert "not supported" in resp.json()["error"]

    def test_success(self, client, config):
        fake_models = [{"id": "gpt-4"}, {"id": "gpt-3.5"}]
        with patch("acai.provider.routes._FETCH_MAP", {"openai": lambda prov: fake_models}):
            resp = client.post("/providers/alpha/fetch-models")
        assert resp.status_code == 200
        assert resp.json() == fake_models

    def test_request_exception(self, client, config):
        import requests

        def _boom(prov):
            raise requests.RequestException("connection refused")

        with patch("acai.provider.routes._FETCH_MAP", {"openai": _boom}):
            resp = client.post("/providers/alpha/fetch-models")
        assert resp.status_code == 502
        assert "connection refused" in resp.json()["error"]


# ===================================================================
# Model Sets CRUD
# ===================================================================

class TestListModelSets:
    def test_empty(self, client, config):
        resp = client.get("/model-sets")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all(self, client, config):
        config.model_sets = [_model_set("set-a"), _model_set("set-b")]
        resp = client.get("/model-sets")
        assert len(resp.json()) == 2


class TestCreateModelSet:
    def test_success(self, client, config):
        resp = client.post("/model-sets", json={
            "name": "my-set",
            "entries": [{"provider": "alpha", "model": "gpt-test"}],
        })
        assert resp.status_code == 201
        assert resp.json()["name"] == "my-set"
        assert len(config.model_sets) == 1

    def test_missing_name(self, client):
        resp = client.post("/model-sets", json={"entries": []})
        assert resp.status_code == 400
        assert "name is required" in resp.json()["error"]

    def test_empty_name(self, client):
        resp = client.post("/model-sets", json={"name": "  "})
        assert resp.status_code == 400

    def test_duplicate(self, client, config):
        config.model_sets = [_model_set("existing")]
        resp = client.post("/model-sets", json={"name": "existing"})
        assert resp.status_code == 409
        assert "already exists" in resp.json()["error"]

    def test_default_clears_others(self, client, config):
        config.model_sets = [_model_set("old", default=True)]
        resp = client.post("/model-sets", json={"name": "new-default", "default": True})
        assert resp.status_code == 201
        assert not config.model_sets[0].default
        assert config.model_sets[1].default


class TestGetModelSet:
    def test_found(self, client, config):
        config.model_sets = [_model_set("alpha-set")]
        resp = client.get("/model-sets/alpha-set")
        assert resp.status_code == 200
        assert resp.json()["name"] == "alpha-set"

    def test_not_found(self, client):
        resp = client.get("/model-sets/missing")
        assert resp.status_code == 404


class TestUpdateModelSet:
    def test_rename(self, client, config):
        config.model_sets = [_model_set("old-name")]
        resp = client.put("/model-sets/old-name", json={"name": "new-name"})
        assert resp.status_code == 200
        assert config.model_sets[0].name == "new-name"

    def test_rename_to_empty(self, client, config):
        config.model_sets = [_model_set("s")]
        resp = client.put("/model-sets/s", json={"name": "  "})
        assert resp.status_code == 400
        assert "name cannot be empty" in resp.json()["error"]

    def test_rename_duplicate(self, client, config):
        config.model_sets = [_model_set("a"), _model_set("b")]
        resp = client.put("/model-sets/a", json={"name": "b"})
        assert resp.status_code == 409
        assert "already exists" in resp.json()["error"]

    def test_rename_same_name_ok(self, client, config):
        config.model_sets = [_model_set("s")]
        resp = client.put("/model-sets/s", json={"name": "s"})
        assert resp.status_code == 200

    def test_set_default_clears_others(self, client, config):
        config.model_sets = [_model_set("a", default=True), _model_set("b")]
        resp = client.put("/model-sets/b", json={"default": True})
        assert resp.status_code == 200
        assert not config.model_sets[0].default
        assert config.model_sets[1].default

    def test_unset_default(self, client, config):
        config.model_sets = [_model_set("a", default=True)]
        resp = client.put("/model-sets/a", json={"default": False})
        assert resp.status_code == 200
        assert not config.model_sets[0].default

    def test_update_entries(self, client, config):
        config.model_sets = [_model_set("s")]
        resp = client.put("/model-sets/s", json={
            "entries": [
                {"provider": "p1", "model": "m1", "price_input": 1.0, "price_output": 2.0},
                {"provider": "p2", "model": "m2"},
            ],
        })
        assert resp.status_code == 200
        assert len(config.model_sets[0].entries) == 2

    def test_not_found(self, client):
        resp = client.put("/model-sets/missing", json={"name": "x"})
        assert resp.status_code == 404


class TestDeleteModelSet:
    def test_success(self, client, config):
        config.model_sets = [_model_set("to-delete")]
        resp = client.delete("/model-sets/to-delete")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert len(config.model_sets) == 0

    def test_not_found(self, client):
        resp = client.delete("/model-sets/missing")
        assert resp.status_code == 404


class TestSetDefaultModelSet:
    def test_success(self, client, config):
        config.model_sets = [_model_set("a", default=True), _model_set("b")]
        resp = client.post("/model-sets/b/default")
        assert resp.status_code == 200
        assert not config.model_sets[0].default
        assert config.model_sets[1].default
        assert resp.json()["default"] is True

    def test_not_found(self, client):
        resp = client.post("/model-sets/missing/default")
        assert resp.status_code == 404
