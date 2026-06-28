"""Unit tests for acai/orchestrator/routes/agents.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acai.orchestrator.routes import RouterDeps
from acai.orchestrator.routes.agents import create_agents_router


def _make_agent(name="coder", builtin=False, **kwargs):
    """Create a mock AgentDef."""
    agent = MagicMock()
    agent.name = name
    agent.builtin = builtin
    agent.to_dict.return_value = {"name": name, "builtin": builtin, **kwargs}
    return agent


@pytest.fixture
def mock_deps(tmp_path):
    deps = MagicMock(spec=RouterDeps)
    deps.agent_store = MagicMock()
    deps.workflows_dir = str(tmp_path / "workflows")
    deps.builtin_wf_dir = str(tmp_path / "builtin")
    return deps


@pytest.fixture
def client(mock_deps):
    app = FastAPI()
    router = create_agents_router(mock_deps)
    app.include_router(router)
    return TestClient(app)


class TestListAgents:
    def test_list_all(self, client, mock_deps):
        mock_deps.agent_store.list.return_value = [_make_agent("coder"), _make_agent("reviewer")]
        resp = client.get("/agents")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_by_workflow_id(self, client, mock_deps, tmp_path):
        wf_agents_dir = tmp_path / "workflows" / "my-wf" / "agents"
        wf_agents_dir.mkdir(parents=True)

        mock_deps.workflows_dir = str(tmp_path / "workflows")
        mock_deps.agent_store.scoped.return_value = contextmanager(lambda: (yield))()
        mock_deps.agent_store.list.return_value = [_make_agent("wf-agent")]

        app = FastAPI()
        router = create_agents_router(mock_deps)
        app.include_router(router)
        cl = TestClient(app)

        resp = cl.get("/agents?workflow_id=my-wf")
        assert resp.status_code == 200


class TestCreateAgent:
    def test_create_success(self, client, mock_deps):
        mock_deps.agent_store.get.return_value = None
        resp = client.post("/agents", json={"name": "new-agent", "description": "A new agent"})
        assert resp.status_code == 201
        mock_deps.agent_store.scaffold.assert_called_once()

    def test_create_missing_name(self, client, mock_deps):
        resp = client.post("/agents", json={"description": "no name"})
        assert resp.status_code == 400
        assert "name is required" in resp.json()["error"]

    def test_create_duplicate(self, client, mock_deps):
        mock_deps.agent_store.get.return_value = _make_agent("coder")
        resp = client.post("/agents", json={"name": "coder"})
        assert resp.status_code == 409
        assert "already exists" in resp.json()["error"]


class TestGetAgent:
    def test_found(self, client, mock_deps):
        mock_deps.agent_store.get.return_value = _make_agent("coder")
        resp = client.get("/agents/coder")
        assert resp.status_code == 200
        assert resp.json()["name"] == "coder"

    def test_not_found(self, client, mock_deps):
        mock_deps.agent_store.get.return_value = None
        resp = client.get("/agents/missing")
        assert resp.status_code == 404


class TestUpdateAgent:
    def test_update_fields(self, client, mock_deps):
        agent = _make_agent("coder")
        mock_deps.agent_store.get.return_value = agent
        resp = client.put("/agents/coder", json={
            "description": "Updated description",
            "uses_sandbox": True,
            "max_iterations": 15,
        })
        assert resp.status_code == 200
        mock_deps.agent_store.save.assert_called_once_with(agent)

    def test_update_not_found(self, client, mock_deps):
        mock_deps.agent_store.get.return_value = None
        resp = client.put("/agents/missing", json={"description": "x"})
        assert resp.status_code == 404


class TestDeleteAgent:
    def test_delete_success(self, client, mock_deps):
        agent = _make_agent("custom", builtin=False)
        mock_deps.agent_store.get.side_effect = [agent, None]
        resp = client.delete("/agents/custom")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        mock_deps.agent_store.delete.assert_called_once_with("custom")

    def test_delete_not_found(self, client, mock_deps):
        mock_deps.agent_store.get.return_value = None
        resp = client.delete("/agents/missing")
        assert resp.status_code == 404

    def test_delete_builtin_forbidden(self, client, mock_deps):
        agent = _make_agent("default", builtin=True)
        mock_deps.agent_store.get.return_value = agent
        resp = client.delete("/agents/default")
        assert resp.status_code == 403
        assert "built-in" in resp.json()["error"]

    def test_delete_reveals_builtin(self, client, mock_deps):
        custom_agent = _make_agent("coder", builtin=False)
        builtin_agent = _make_agent("coder", builtin=True)
        mock_deps.agent_store.get.side_effect = [custom_agent, builtin_agent]
        resp = client.delete("/agents/coder")
        assert resp.status_code == 200
        assert resp.json()["builtin_revealed"] is True


class TestGetAgentTemplate:
    def test_get_template(self, client, mock_deps):
        mock_deps.agent_store.get.return_value = _make_agent("coder")
        mock_deps.agent_store.read_template.return_value = "You are a coder."
        resp = client.get("/agents/coder/template")
        assert resp.status_code == 200
        assert resp.json()["content"] == "You are a coder."

    def test_not_found(self, client, mock_deps):
        mock_deps.agent_store.get.return_value = None
        resp = client.get("/agents/missing/template")
        assert resp.status_code == 404


class TestUpdateAgentTemplate:
    def test_update_template(self, client, mock_deps):
        mock_deps.agent_store.get.return_value = _make_agent("coder")
        resp = client.put("/agents/coder/template", json={"content": "New template."})
        assert resp.status_code == 200
        mock_deps.agent_store.save_template.assert_called_once_with("coder", "New template.")

    def test_not_found(self, client, mock_deps):
        mock_deps.agent_store.get.return_value = None
        resp = client.put("/agents/missing/template", json={"content": "x"})
        assert resp.status_code == 404


class TestResetAgent:
    def test_reset_builtin(self, client, mock_deps):
        mock_deps.agent_store._is_builtin.return_value = True
        mock_deps.agent_store.get.return_value = _make_agent("default", builtin=True)
        resp = client.post("/agents/default/reset")
        assert resp.status_code == 200
        mock_deps.agent_store.delete.assert_called_once_with("default")

    def test_reset_non_builtin(self, client, mock_deps):
        mock_deps.agent_store._is_builtin.return_value = False
        resp = client.post("/agents/custom/reset")
        assert resp.status_code == 400
        assert "not a built-in" in resp.json()["error"]
