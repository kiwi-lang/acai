"""Unit tests for acai/orchestrator/routes/workflows.py."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acai.orchestrator.routes import RouterDeps
from acai.orchestrator.routes.workflows import create_workflows_router


@pytest.fixture
def mock_deps(tmp_path):
    deps = MagicMock(spec=RouterDeps)
    deps.config = MagicMock()
    deps.agent_store = MagicMock()
    deps.agent_store.scoped = MagicMock(return_value=contextmanager(lambda: (yield))())
    deps.skill_store = MagicMock()
    deps.tool_registry = MagicMock()
    deps.tool_registry.mcp_definitions.return_value = []
    deps.chat = MagicMock()
    mock_meta = MagicMock()
    mock_meta.id = "conv-test-id"
    deps.chat.create.return_value = mock_meta
    deps.queue = MagicMock()
    deps.projects = MagicMock()
    deps.tracker = MagicMock()
    deps.load_balancer = MagicMock()
    deps.workflows_dir = str(tmp_path / "workflows")
    deps.builtin_wf_dir = str(tmp_path / "builtin")
    os.makedirs(deps.workflows_dir, exist_ok=True)
    os.makedirs(deps.builtin_wf_dir, exist_ok=True)
    return deps


@pytest.fixture
def mock_audit():
    audit = MagicMock()
    audit.record = MagicMock()
    audit.finalize = MagicMock()
    audit.client_summary.return_value = {}
    return audit


@pytest.fixture
def client(mock_deps, mock_audit):
    app = FastAPI()
    router = create_workflows_router(
        mock_deps,
        make_audit=MagicMock(return_value=mock_audit),
    )
    app.include_router(router)
    return TestClient(app)


def _create_workflow(base_dir, wf_id, spec=None):
    """Helper to create a workflow definition on disk."""
    wf_dir = os.path.join(base_dir, wf_id)
    os.makedirs(wf_dir, exist_ok=True)
    spec = spec or {"id": wf_id, "name": f"Workflow {wf_id}", "description": "test", "nodes": [], "edges": []}
    with open(os.path.join(wf_dir, "definition.json"), "w") as f:
        json.dump(spec, f)
    return wf_dir


class TestGetNodeTypes:
    def test_returns_types(self, client):
        with patch("acai.orchestrator.routes.workflows.all_types", create=True) as mock_types:
            from acai.tasks import nodes
            with patch.object(nodes, "all_types", return_value=[]) as mock_at:
                resp = client.get("/workflows/node-types")
                assert resp.status_code == 200


class TestGetToolDefinitions:
    def test_returns_defs(self, client, mock_deps):
        mock_deps.tool_registry.mcp_definitions.return_value = [{"name": "shell_run"}]
        resp = client.get("/workflows/tool-definitions")
        assert resp.status_code == 200
        assert resp.json() == [{"name": "shell_run"}]


class TestListWorkflows:
    def test_list_user_and_builtin(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "user-wf")
        _create_workflow(mock_deps.builtin_wf_dir, "builtin-wf")

        resp = client.get("/workflows")
        assert resp.status_code == 200
        data = resp.json()
        ids = [w["id"] for w in data]
        assert "user-wf" in ids
        assert "builtin-wf" in ids

    def test_user_overrides_builtin(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "shared-wf")
        _create_workflow(mock_deps.builtin_wf_dir, "shared-wf")

        resp = client.get("/workflows")
        data = resp.json()
        shared = [w for w in data if w["id"] == "shared-wf"]
        assert len(shared) == 1
        assert shared[0]["builtin"] is False


class TestGetWorkflow:
    def test_get_user(self, client, mock_deps):
        spec = {"id": "wf1", "name": "WF1", "nodes": []}
        _create_workflow(mock_deps.workflows_dir, "wf1", spec)

        resp = client.get("/workflows/wf1")
        assert resp.status_code == 200
        assert resp.json()["name"] == "WF1"
        assert resp.json()["builtin"] is False

    def test_get_builtin(self, client, mock_deps):
        spec = {"id": "b1", "name": "Builtin 1", "nodes": []}
        _create_workflow(mock_deps.builtin_wf_dir, "b1", spec)

        resp = client.get("/workflows/b1")
        assert resp.status_code == 200
        assert resp.json()["builtin"] is True

    def test_not_found(self, client):
        resp = client.get("/workflows/nonexistent")
        assert resp.status_code == 404


class TestSaveWorkflow:
    def test_save_new(self, client, mock_deps):
        resp = client.post("/workflows", json={
            "id": "new-wf",
            "name": "New Workflow",
            "nodes": [],
            "edges": [],
        })
        assert resp.status_code == 201
        assert resp.json()["id"] == "new-wf"
        path = os.path.join(mock_deps.workflows_dir, "new-wf", "definition.json")
        assert os.path.isfile(path)

    def test_save_missing_id(self, client):
        resp = client.post("/workflows", json={"name": "No ID"})
        assert resp.status_code == 400


class TestUpdateWorkflow:
    def test_update(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        resp = client.put("/workflows/wf1", json={"name": "Updated WF"})
        assert resp.status_code == 200
        assert resp.json()["id"] == "wf1"
        assert resp.json()["name"] == "Updated WF"


class TestDeleteWorkflow:
    def test_delete(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        resp = client.delete("/workflows/wf1")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert not os.path.isdir(os.path.join(mock_deps.workflows_dir, "wf1"))

    def test_delete_nonexistent(self, client):
        resp = client.delete("/workflows/ghost")
        assert resp.status_code == 200


class TestWorkflowAgents:
    def test_list_agents(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        agent_dir = os.path.join(wf_dir, "agents", "coder")
        os.makedirs(agent_dir)
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            json.dump({"name": "coder", "description": "Writes code"}, f)

        resp = client.get("/workflows/wf1/agents")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["name"] == "coder"

    def test_create_agent(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        resp = client.post("/workflows/wf1/agents", json={
            "name": "reviewer",
            "description": "Reviews code",
        })
        assert resp.status_code == 200
        assert resp.json()["created"] is True

    def test_create_agent_no_name(self, client, mock_deps):
        resp = client.post("/workflows/wf1/agents", json={"description": "x"})
        assert resp.status_code == 400

    def test_get_agent(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        agent_dir = os.path.join(wf_dir, "agents", "coder")
        os.makedirs(agent_dir)
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            json.dump({"name": "coder", "role": "system"}, f)
        with open(os.path.join(agent_dir, "system.j2"), "w") as f:
            f.write("You are a coder.")

        resp = client.get("/workflows/wf1/agents/coder")
        assert resp.status_code == 200
        assert resp.json()["name"] == "coder"
        assert resp.json()["system_template_content"] == "You are a coder."

    def test_get_agent_not_found(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        resp = client.get("/workflows/wf1/agents/missing")
        assert resp.status_code == 404


class TestWorkflowSkills:
    def test_list_skills(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        skill_dir = os.path.join(wf_dir, "skills", "custom", "greet")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "tool.json"), "w") as f:
            json.dump({"name": "greet", "description": "Say hello"}, f)

        resp = client.get("/workflows/wf1/skills")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["qualified_name"] == "custom.greet"

    def test_create_skill(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        resp = client.post("/workflows/wf1/skills", json={
            "namespace": "utils",
            "name": "parse",
            "description": "Parse data",
        })
        assert resp.status_code == 200
        assert resp.json()["created"] is True

    def test_create_skill_no_name(self, client):
        resp = client.post("/workflows/wf1/skills", json={"namespace": "x"})
        assert resp.status_code == 400

    def test_get_skill(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        skill_dir = os.path.join(wf_dir, "skills", "custom", "greet")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "tool.json"), "w") as f:
            json.dump({"name": "greet", "description": "Hello", "parameters": {}}, f)
        with open(os.path.join(skill_dir, "run.py"), "w") as f:
            f.write("print('hi')")

        resp = client.get("/workflows/wf1/skills/custom/greet")
        assert resp.status_code == 200
        data = resp.json()
        assert data["namespace"] == "custom"
        assert data["code"] == "print('hi')"

    def test_get_skill_not_found(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        resp = client.get("/workflows/wf1/skills/custom/missing")
        assert resp.status_code == 404


class TestValidateWorkflow:
    def test_validate_spec(self, client, mock_deps):
        with patch("acai.tasks.typecheck.typecheck") as mock_tc:
            mock_tc.return_value = []
            resp = client.post("/workflows/validate", json={
                "id": "wf1",
                "nodes": [],
                "edges": [],
            })
            assert resp.status_code == 200
            assert resp.json()["valid"] is True

    def test_validate_by_id(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        with patch("acai.tasks.typecheck.typecheck") as mock_tc:
            mock_tc.return_value = [{"severity": "warning", "message": "unused node"}]
            resp = client.post("/workflows/wf1/validate")
            assert resp.status_code == 200
            assert resp.json()["valid"] is True
            assert len(resp.json()["warnings"]) == 1

    def test_validate_not_found(self, client, mock_deps):
        resp = client.post("/workflows/nonexistent/validate")
        assert resp.status_code == 404


class TestRunWorkflow:
    def test_workflow_not_found(self, client, mock_deps):
        resp = client.post("/workflows/nonexistent/run", json={"message": "hi"})
        assert resp.status_code == 404

    def test_run_workflow_streams_events(self, client, mock_deps, mock_audit):
        _create_workflow(mock_deps.workflows_dir, "wf1", {
            "id": "wf1", "name": "WF1", "nodes": [], "edges": [],
        })
        mock_audit.client_summary.return_value = {"request_id": "req-123"}

        async def _fake_run(work):
            yield {"event_type": "message", "data": {"text": "hello"}}

        mock_graph = MagicMock()
        mock_graph.run = _fake_run

        mock_worker = MagicMock()
        mock_worker.url = "http://localhost:8080"

        @contextmanager
        def _acquire():
            yield mock_worker

        from unittest.mock import AsyncMock

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(return_value=mock_worker)
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_deps.load_balancer.acquire.return_value = async_ctx

        with patch("acai.tasks.DynamicGraph.from_work", return_value=mock_graph):
            resp = client.post("/workflows/wf1/run", json={"message": "go"})
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            body = resp.text
            assert "event: meta" in body
            assert "event: message" in body
            assert "event: audit_complete" in body

    def test_run_workflow_timeout_error(self, client, mock_deps, mock_audit):
        _create_workflow(mock_deps.workflows_dir, "wf1", {
            "id": "wf1", "name": "WF1", "nodes": [], "edges": [],
        })
        mock_audit.client_summary.return_value = {}

        from unittest.mock import AsyncMock

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError("no workers"))
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_deps.load_balancer.acquire.return_value = async_ctx

        resp = client.post("/workflows/wf1/run", json={"message": "go"})
        assert resp.status_code == 200
        body = resp.text
        assert "event: error" in body
        assert "No worker available (timeout)" in body

    def test_run_workflow_generic_exception(self, client, mock_deps, mock_audit):
        _create_workflow(mock_deps.workflows_dir, "wf1", {
            "id": "wf1", "name": "WF1", "nodes": [], "edges": [],
        })
        mock_audit.client_summary.return_value = {}

        from unittest.mock import AsyncMock

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("connection lost"))
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_deps.load_balancer.acquire.return_value = async_ctx

        resp = client.post("/workflows/wf1/run", json={"message": "go"})
        assert resp.status_code == 200
        body = resp.text
        assert "event: error" in body
        assert "RuntimeError: connection lost" in body
        assert "traceback" in body

    def test_run_workflow_fallback_to_builtin(self, client, mock_deps, mock_audit):
        _create_workflow(mock_deps.builtin_wf_dir, "builtin-wf", {
            "id": "builtin-wf", "name": "Builtin", "nodes": [], "edges": [],
        })
        mock_audit.client_summary.return_value = {}

        from unittest.mock import AsyncMock

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_deps.load_balancer.acquire.return_value = async_ctx

        resp = client.post("/workflows/builtin-wf/run", json={"message": "go"})
        assert resp.status_code == 200
        assert "event: meta" in resp.text

    def test_run_workflow_test_mode(self, client, mock_deps, mock_audit):
        _create_workflow(mock_deps.workflows_dir, "wf1", {
            "id": "wf1", "name": "WF1", "nodes": [], "edges": [],
        })
        mock_audit.client_summary.return_value = {}

        from unittest.mock import AsyncMock

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_deps.load_balancer.acquire.return_value = async_ctx

        resp = client.post("/workflows/wf1/run", json={
            "message": "test",
            "test": True,
            "test_conversation": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        mock_deps.chat.create.assert_not_called()

    def test_run_workflow_conversation_as_list(self, client, mock_deps, mock_audit):
        _create_workflow(mock_deps.workflows_dir, "wf1", {
            "id": "wf1", "name": "WF1", "nodes": [], "edges": [],
        })
        mock_audit.client_summary.return_value = {}

        from unittest.mock import AsyncMock

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_deps.load_balancer.acquire.return_value = async_ctx

        resp = client.post("/workflows/wf1/run", json={
            "message": "hi",
            "conversation": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200

    def test_run_workflow_conversation_as_json_string(self, client, mock_deps, mock_audit):
        _create_workflow(mock_deps.workflows_dir, "wf1", {
            "id": "wf1", "name": "WF1", "nodes": [], "edges": [],
        })
        mock_audit.client_summary.return_value = {}

        from unittest.mock import AsyncMock

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_deps.load_balancer.acquire.return_value = async_ctx

        resp = client.post("/workflows/wf1/run", json={
            "message": "hi",
            "conversation": '[{"role": "user", "content": "hi"}]',
        })
        assert resp.status_code == 200

    def test_run_workflow_conversation_as_id(self, client, mock_deps, mock_audit):
        _create_workflow(mock_deps.workflows_dir, "wf1", {
            "id": "wf1", "name": "WF1", "nodes": [], "edges": [],
        })
        mock_audit.client_summary.return_value = {}

        from unittest.mock import AsyncMock

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_deps.load_balancer.acquire.return_value = async_ctx

        resp = client.post("/workflows/wf1/run", json={
            "message": "hi",
            "conversation": "conv-abc-123",
        })
        assert resp.status_code == 200
        mock_deps.chat.create.assert_not_called()


class TestJsonBodyErrorHandling:
    """Test the _json_body fallback when request body is invalid."""

    def test_save_workflow_with_invalid_json_body(self, client):
        resp = client.post(
            "/workflows",
            content=b"not json at all {{{",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert "id is required" in resp.json()["error"]


class TestScanWorkflowDirEdgeCases:
    """Test _scan_wf_dir error handling: malformed JSON, non-dir entries."""

    def test_malformed_definition_json_skipped(self, client, mock_deps):
        wf_dir = os.path.join(mock_deps.workflows_dir, "bad-wf")
        os.makedirs(wf_dir)
        with open(os.path.join(wf_dir, "definition.json"), "w") as f:
            f.write("{invalid json!!!")

        resp = client.get("/workflows")
        assert resp.status_code == 200
        ids = [w["id"] for w in resp.json()]
        assert "bad-wf" not in ids

    def test_entry_without_definition_file_skipped(self, client, mock_deps):
        wf_dir = os.path.join(mock_deps.workflows_dir, "no-def")
        os.makedirs(wf_dir)

        resp = client.get("/workflows")
        assert resp.status_code == 200
        ids = [w["id"] for w in resp.json()]
        assert "no-def" not in ids

    def test_nonexistent_workflow_dir(self, mock_deps, mock_audit):
        mock_deps.workflows_dir = "/nonexistent/path"
        app = FastAPI()
        router = create_workflows_router(
            mock_deps,
            make_audit=MagicMock(return_value=mock_audit),
        )
        app.include_router(router)
        tc = TestClient(app)

        resp = tc.get("/workflows")
        assert resp.status_code == 200
        assert resp.json() == []


class TestListWorkflowsExtraWfDirs:
    """Test list_workflows with extra_wf_dirs parameter."""

    def test_extra_wf_dirs_included(self, mock_deps, mock_audit, tmp_path):
        extra_dir = str(tmp_path / "extra")
        os.makedirs(extra_dir)
        _create_workflow(extra_dir, "extra-wf")

        app = FastAPI()
        router = create_workflows_router(
            mock_deps,
            make_audit=MagicMock(return_value=mock_audit),
            extra_wf_dirs=[extra_dir],
        )
        app.include_router(router)
        tc = TestClient(app)

        resp = tc.get("/workflows")
        assert resp.status_code == 200
        ids = [w["id"] for w in resp.json()]
        assert "extra-wf" in ids

    def test_extra_wf_dirs_no_duplicate_with_builtin(self, mock_deps, mock_audit, tmp_path):
        extra_dir = str(tmp_path / "extra")
        os.makedirs(extra_dir)
        _create_workflow(extra_dir, "shared-id")
        _create_workflow(mock_deps.builtin_wf_dir, "shared-id")

        app = FastAPI()
        router = create_workflows_router(
            mock_deps,
            make_audit=MagicMock(return_value=mock_audit),
            extra_wf_dirs=[extra_dir],
        )
        app.include_router(router)
        tc = TestClient(app)

        resp = tc.get("/workflows")
        shared = [w for w in resp.json() if w["id"] == "shared-id"]
        assert len(shared) == 1


class TestResolveDynamicPins:
    """Test the /workflows/resolve-pins endpoint."""

    def test_unknown_node_type_returns_empty(self, client):
        with patch("acai.tasks.nodes.get", return_value=None):
            resp = client.post("/workflows/resolve-pins", json={
                "node_type": "nonexistent",
                "data": {},
            })
            assert resp.status_code == 200
            assert resp.json() == {"pins": []}

    def test_valid_node_type_with_pins(self, client, mock_deps):
        mock_pin = MagicMock()
        mock_pin.to_dict.return_value = {"name": "out", "type": "string"}
        mock_nt = MagicMock()
        mock_nt.dynamic_pins.return_value = [mock_pin]

        with patch("acai.tasks.nodes.get", return_value=mock_nt):
            resp = client.post("/workflows/resolve-pins", json={
                "node_type": "agent",
                "data": {"agent": "coder"},
                "spec": {"nodes": []},
            })
            assert resp.status_code == 200
            assert resp.json()["pins"] == [{"name": "out", "type": "string"}]

    def test_tool_registry_none(self, mock_deps, mock_audit):
        mock_deps.tool_registry = None

        app = FastAPI()
        router = create_workflows_router(
            mock_deps,
            make_audit=MagicMock(return_value=mock_audit),
        )
        app.include_router(router)
        tc = TestClient(app)

        mock_nt = MagicMock()
        mock_nt.dynamic_pins.return_value = []
        with patch("acai.tasks.nodes.get", return_value=mock_nt):
            resp = tc.post("/workflows/resolve-pins", json={
                "node_type": "agent",
                "data": {},
            })
            assert resp.status_code == 200
            mock_nt.dynamic_pins.assert_called_once()
            assert mock_nt.dynamic_pins.call_args[1]["tool_defs"] == []


class TestGetAgentTemplateInputs:
    def test_returns_template_inputs(self, client, mock_deps):
        mock_deps.agent_store.template_inputs.return_value = ["topic", "language"]
        resp = client.get("/workflows/agent-inputs/coder")
        assert resp.status_code == 200
        assert resp.json() == {"agent": "coder", "inputs": ["topic", "language"]}


class TestSaveBuiltinWorkflow:
    def test_save_builtin(self, client, mock_deps):
        resp = client.put("/workflows/builtin/new-builtin", json={
            "name": "New Builtin",
            "nodes": [],
            "edges": [],
        })
        assert resp.status_code == 200
        assert resp.json()["id"] == "new-builtin"
        path = os.path.join(mock_deps.builtin_wf_dir, "new-builtin", "definition.json")
        assert os.path.isfile(path)

    def test_save_builtin_defaults_name(self, client, mock_deps):
        resp = client.put("/workflows/builtin/no-name", json={
            "nodes": [],
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "no-name"


class TestCreateWorkflowAgentAdvanced:
    """Test optional fields and fallback to builtin dir."""

    def test_create_agent_all_optional_fields(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        resp = client.post("/workflows/wf1/agents", json={
            "name": "full-agent",
            "description": "Full agent",
            "role": "assistant",
            "provider": "openai",
            "output_format": "json",
            "model_overrides": {"temperature": 0.5},
            "tools": ["shell_run", "file_read"],
            "tool_permissions": {"shell_run": "ask"},
            "resource_permissions": {"files": "read"},
            "context_sources": ["project"],
            "max_iterations": 10,
            "approval_required": True,
            "uses_sandbox": True,
            "tags": ["coding"],
            "avatar": "robot",
            "scope": "project",
            "system_template": "You are a helpful assistant.",
        })
        assert resp.status_code == 200
        assert resp.json()["created"] is True

        agent_dir = os.path.join(mock_deps.workflows_dir, "wf1", "agents", "full-agent")
        with open(os.path.join(agent_dir, "definition.json")) as f:
            defn = json.load(f)
        assert defn["model_overrides"] == {"temperature": 0.5}
        assert defn["tools"] == ["shell_run", "file_read"]
        assert defn["max_iterations"] == 10
        assert defn["approval_required"] is True
        assert defn["uses_sandbox"] is True
        assert defn["tags"] == ["coding"]
        assert defn["avatar"] == "robot"
        assert defn["scope"] == "project"

        with open(os.path.join(agent_dir, "system.j2")) as f:
            tpl = f.read()
        assert tpl == "You are a helpful assistant."

    def test_create_agent_fallback_to_builtin(self, client, mock_deps):
        _create_workflow(mock_deps.builtin_wf_dir, "builtin-wf")
        resp = client.post("/workflows/builtin-wf/agents", json={
            "name": "new-agent",
            "description": "Agent in builtin",
        })
        assert resp.status_code == 200
        assert resp.json()["created"] is True

    def test_create_agent_empty_name(self, client, mock_deps):
        resp = client.post("/workflows/wf1/agents", json={"name": "   "})
        assert resp.status_code == 400
        assert "agent name required" in resp.json()["error"]


class TestGetWorkflowAgentEdgeCases:
    def test_get_agent_no_template(self, client, mock_deps):
        """Agent definition without system.j2 should return empty template."""
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        agent_dir = os.path.join(wf_dir, "agents", "minimal")
        os.makedirs(agent_dir)
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            json.dump({"name": "minimal", "role": "system"}, f)

        resp = client.get("/workflows/wf1/agents/minimal")
        assert resp.status_code == 200
        assert resp.json()["system_template_content"] == ""

    def test_get_agent_from_builtin(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.builtin_wf_dir, "bwf")
        agent_dir = os.path.join(wf_dir, "agents", "builtin-agent")
        os.makedirs(agent_dir)
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            json.dump({"name": "builtin-agent"}, f)

        resp = client.get("/workflows/bwf/agents/builtin-agent")
        assert resp.status_code == 200
        assert resp.json()["name"] == "builtin-agent"


class TestListWorkflowAgentsEdgeCases:
    def test_malformed_agent_json_skipped(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        agent_dir = os.path.join(wf_dir, "agents", "bad")
        os.makedirs(agent_dir)
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            f.write("not json")

        resp = client.get("/workflows/wf1/agents")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_agent_dir_entry_without_definition(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        agent_dir = os.path.join(wf_dir, "agents", "empty")
        os.makedirs(agent_dir)

        resp = client.get("/workflows/wf1/agents")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_agents_dir(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        resp = client.get("/workflows/wf1/agents")
        assert resp.status_code == 200
        assert resp.json() == []


class TestListWorkflowSkillsEdgeCases:
    def test_no_skills_dir(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        resp = client.get("/workflows/wf1/skills")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_malformed_skill_json_skipped(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        skill_dir = os.path.join(wf_dir, "skills", "ns", "bad")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "tool.json"), "w") as f:
            f.write("broken json {{{")

        resp = client.get("/workflows/wf1/skills")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_namespace_entry_is_file_not_dir(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        skills_dir = os.path.join(wf_dir, "skills")
        os.makedirs(skills_dir)
        with open(os.path.join(skills_dir, "not-a-dir"), "w") as f:
            f.write("file")

        resp = client.get("/workflows/wf1/skills")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_skill_entry_without_tool_json(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        skill_dir = os.path.join(wf_dir, "skills", "ns", "nojson")
        os.makedirs(skill_dir)

        resp = client.get("/workflows/wf1/skills")
        assert resp.status_code == 200
        assert resp.json() == []


class TestCreateWorkflowSkillAdvanced:
    def test_skill_with_params_as_json_string(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        params = json.dumps({"type": "object", "properties": {"x": {"type": "string"}}})
        resp = client.post("/workflows/wf1/skills", json={
            "namespace": "utils",
            "name": "parse",
            "parameters": params,
        })
        assert resp.status_code == 200
        skill_dir = os.path.join(mock_deps.workflows_dir, "wf1", "skills", "utils", "parse")
        with open(os.path.join(skill_dir, "tool.json")) as f:
            defn = json.load(f)
        assert defn["parameters"]["properties"]["x"]["type"] == "string"

    def test_skill_with_invalid_params_string_uses_default(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        resp = client.post("/workflows/wf1/skills", json={
            "namespace": "utils",
            "name": "bad-params",
            "parameters": "not valid json {{",
        })
        assert resp.status_code == 200
        skill_dir = os.path.join(mock_deps.workflows_dir, "wf1", "skills", "utils", "bad-params")
        with open(os.path.join(skill_dir, "tool.json")) as f:
            defn = json.load(f)
        assert defn["parameters"] == {"type": "object", "properties": {}, "required": []}

    def test_skill_with_readme_and_requirements(self, client, mock_deps):
        _create_workflow(mock_deps.workflows_dir, "wf1")
        resp = client.post("/workflows/wf1/skills", json={
            "namespace": "utils",
            "name": "rich",
            "description": "A skill",
            "code": "print('hello')",
            "readme": "# Rich Skill\nUsage info",
            "requirements": "requests>=2.28\nnumpy",
        })
        assert resp.status_code == 200
        skill_dir = os.path.join(mock_deps.workflows_dir, "wf1", "skills", "utils", "rich")
        with open(os.path.join(skill_dir, "README.md")) as f:
            assert "Rich Skill" in f.read()
        with open(os.path.join(skill_dir, "requirements.txt")) as f:
            assert "requests>=2.28" in f.read()
        with open(os.path.join(skill_dir, "run.py")) as f:
            assert f.read() == "print('hello')"

    def test_skill_fallback_to_builtin_dir(self, client, mock_deps):
        _create_workflow(mock_deps.builtin_wf_dir, "bwf")
        resp = client.post("/workflows/bwf/skills", json={
            "namespace": "ns",
            "name": "myskill",
        })
        assert resp.status_code == 200
        assert resp.json()["created"] is True

    def test_skill_missing_namespace(self, client, mock_deps):
        resp = client.post("/workflows/wf1/skills", json={"name": "x"})
        assert resp.status_code == 400
        assert "namespace and name required" in resp.json()["error"]

    def test_skill_empty_namespace(self, client, mock_deps):
        resp = client.post("/workflows/wf1/skills", json={"namespace": "  ", "name": "x"})
        assert resp.status_code == 400


class TestGetWorkflowSkillAdvanced:
    def test_get_skill_with_readme_and_requirements(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        skill_dir = os.path.join(wf_dir, "skills", "ns", "full")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "tool.json"), "w") as f:
            json.dump({"name": "full", "description": "desc", "parameters": {}}, f)
        with open(os.path.join(skill_dir, "run.py"), "w") as f:
            f.write("print('run')")
        with open(os.path.join(skill_dir, "README.md"), "w") as f:
            f.write("# README")
        with open(os.path.join(skill_dir, "requirements.txt"), "w") as f:
            f.write("httpx>=0.24")

        resp = client.get("/workflows/wf1/skills/ns/full")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "print('run')"
        assert data["readme"] == "# README"
        assert data["requirements"] == "httpx>=0.24"

    def test_get_skill_without_optional_files(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        skill_dir = os.path.join(wf_dir, "skills", "ns", "bare")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "tool.json"), "w") as f:
            json.dump({"name": "bare", "description": ""}, f)

        resp = client.get("/workflows/wf1/skills/ns/bare")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == ""
        assert data["readme"] == ""
        assert data["requirements"] == ""

    def test_get_skill_from_builtin(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.builtin_wf_dir, "bwf")
        skill_dir = os.path.join(wf_dir, "skills", "ns", "bskill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "tool.json"), "w") as f:
            json.dump({"name": "bskill", "description": "builtin skill"}, f)

        resp = client.get("/workflows/bwf/skills/ns/bskill")
        assert resp.status_code == 200
        assert resp.json()["name"] == "bskill"


class TestValidateWorkflowAdvanced:
    def test_validate_spec_with_errors(self, client, mock_deps):
        with patch("acai.tasks.typecheck.typecheck") as mock_tc:
            mock_tc.return_value = [
                {"severity": "error", "message": "missing agent"},
                {"severity": "warning", "message": "unused"},
            ]
            resp = client.post("/workflows/validate", json={
                "id": "",
                "nodes": [],
                "edges": [],
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["valid"] is False
            assert len(data["errors"]) == 1
            assert len(data["warnings"]) == 1

    def test_validate_spec_resolves_workflow_agents_dir(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        agents_dir = os.path.join(wf_dir, "agents")
        os.makedirs(agents_dir)

        with patch("acai.tasks.typecheck.typecheck") as mock_tc:
            mock_tc.return_value = []
            resp = client.post("/workflows/validate", json={
                "id": "wf1",
                "nodes": [],
                "edges": [],
            })
            assert resp.status_code == 200
            assert resp.json()["valid"] is True
            mock_deps.agent_store.scoped.assert_called()

    def test_validate_by_id_with_agents_dir(self, client, mock_deps):
        wf_dir = _create_workflow(mock_deps.workflows_dir, "wf1")
        agents_dir = os.path.join(wf_dir, "agents")
        os.makedirs(agents_dir)

        with patch("acai.tasks.typecheck.typecheck") as mock_tc:
            mock_tc.return_value = []
            resp = client.post("/workflows/wf1/validate")
            assert resp.status_code == 200
            assert resp.json()["valid"] is True

    def test_validate_spec_no_tool_registry(self, mock_deps, mock_audit):
        mock_deps.tool_registry = None

        app = FastAPI()
        router = create_workflows_router(
            mock_deps,
            make_audit=MagicMock(return_value=mock_audit),
        )
        app.include_router(router)
        tc = TestClient(app)

        with patch("acai.tasks.typecheck.typecheck") as mock_tc:
            mock_tc.return_value = []
            resp = tc.post("/workflows/validate", json={
                "id": "",
                "nodes": [],
            })
            assert resp.status_code == 200
            call_kwargs = mock_tc.call_args[1]
            assert call_kwargs["tool_defs"] == []


class TestSaveWorkflowEdgeCases:
    def test_id_whitespace_only(self, client):
        resp = client.post("/workflows", json={"id": "   "})
        assert resp.status_code == 400
        assert "id is required" in resp.json()["error"]

    def test_defaults_name_to_id(self, client, mock_deps):
        resp = client.post("/workflows", json={"id": "auto-name"})
        assert resp.status_code == 201
        assert resp.json()["name"] == "auto-name"
