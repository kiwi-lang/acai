"""Unit tests for acai/orchestrator/routes/skills.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acai.orchestrator.routes import RouterDeps
from acai.orchestrator.routes.skills import create_skills_router


def _make_skill(namespace="custom", name="greet"):
    s = MagicMock()
    s.namespace = namespace
    s.name = name
    s.description = "A greeting skill"
    s.path = f"/skills/{namespace}/{name}"
    return s


@pytest.fixture
def mock_deps(tmp_path):
    deps = MagicMock(spec=RouterDeps)
    deps.skill_store = MagicMock()
    deps.tool_registry = MagicMock()
    deps.workflows_dir = str(tmp_path / "workflows")
    deps.builtin_wf_dir = str(tmp_path / "builtin")
    return deps


@pytest.fixture
def client(mock_deps):
    app = FastAPI()
    router = create_skills_router(mock_deps)
    app.include_router(router)
    return TestClient(app)


class TestListSkills:
    def test_list_all(self, client, mock_deps):
        mock_deps.skill_store.all_skills.return_value = [_make_skill(), _make_skill("utils", "parse")]
        resp = client.get("/skills")
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        assert resp.json()[0]["qualified_name"] == "skills.custom.greet"


class TestGetSkill:
    def test_found(self, client, mock_deps):
        mock_deps.skill_store.read_file.side_effect = lambda ns, name, f: {
            "tool.json": json.dumps({"description": "Hello"}),
            "run.py": "print('hello')",
            "README.md": "# Greet",
            "requirements.txt": "requests",
        }.get(f)

        resp = client.get("/skills/custom/greet")
        assert resp.status_code == 200
        data = resp.json()
        assert data["namespace"] == "custom"
        assert data["name"] == "greet"
        assert data["code"] == "print('hello')"
        assert data["definition"]["description"] == "Hello"

    def test_not_found(self, client, mock_deps):
        mock_deps.skill_store.read_file.return_value = None
        resp = client.get("/skills/custom/missing")
        assert resp.status_code == 404


class TestCreateSkill:
    def test_create_success(self, client, mock_deps):
        mock_deps.skill_store.scaffold.return_value = "/skills/custom/greet"
        resp = client.post("/skills", json={
            "namespace": "custom",
            "name": "greet",
            "description": "Say hello",
            "code": "print('hi')",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["created"] is True
        assert data["qualified_name"] == "skills.custom.greet"
        mock_deps.skill_store.register_all.assert_called_once()

    def test_create_missing_fields(self, client):
        resp = client.post("/skills", json={"namespace": "custom"})
        assert resp.status_code == 400
        assert "required" in resp.json()["error"]

    def test_create_invalid_params_json(self, client):
        resp = client.post("/skills", json={
            "namespace": "custom",
            "name": "bad",
            "parameters": "not valid json{",
        })
        assert resp.status_code == 400
        assert "invalid parameters" in resp.json()["error"]


class TestUpdateSkillCode:
    def test_update(self, client, mock_deps):
        mock_deps.skill_store.read_file.return_value = "{}"
        resp = client.put("/skills/custom/greet/code", json={"code": "new code"})
        assert resp.status_code == 200
        mock_deps.skill_store.write_file.assert_called_once_with("custom", "greet", "run.py", "new code")

    def test_missing_code(self, client):
        resp = client.put("/skills/custom/greet/code", json={})
        assert resp.status_code == 400

    def test_not_found(self, client, mock_deps):
        mock_deps.skill_store.read_file.return_value = None
        resp = client.put("/skills/custom/missing/code", json={"code": "x"})
        assert resp.status_code == 404


class TestUpdateSkillDefinition:
    def test_update_description(self, client, mock_deps):
        mock_deps.skill_store.read_file.return_value = json.dumps({"description": "old"})
        resp = client.put("/skills/custom/greet/definition", json={"description": "new"})
        assert resp.status_code == 200
        assert resp.json()["definition"]["description"] == "new"
        mock_deps.skill_store.write_file.assert_called_once()

    def test_not_found(self, client, mock_deps):
        mock_deps.skill_store.read_file.return_value = None
        resp = client.put("/skills/custom/missing/definition", json={"description": "x"})
        assert resp.status_code == 404


class TestUpdateSkillReadme:
    def test_update(self, client, mock_deps):
        mock_deps.skill_store.read_file.return_value = "{}"
        resp = client.put("/skills/custom/greet/readme", json={"readme": "# Updated"})
        assert resp.status_code == 200
        mock_deps.skill_store.write_file.assert_called_once_with("custom", "greet", "README.md", "# Updated")

    def test_not_found(self, client, mock_deps):
        mock_deps.skill_store.read_file.return_value = None
        resp = client.put("/skills/custom/missing/readme", json={"readme": "x"})
        assert resp.status_code == 404


class TestUpdateSkillRequirements:
    def test_update(self, client, mock_deps):
        mock_deps.skill_store.read_file.return_value = "{}"
        resp = client.put("/skills/custom/greet/requirements", json={"requirements": "requests\nnumpy"})
        assert resp.status_code == 200
        mock_deps.skill_store.write_file.assert_called_once()

    def test_not_found(self, client, mock_deps):
        mock_deps.skill_store.read_file.return_value = None
        resp = client.put("/skills/custom/missing/requirements", json={"requirements": "x"})
        assert resp.status_code == 404


class TestDeleteSkill:
    def test_delete(self, client, mock_deps):
        mock_deps.skill_store.read_file.return_value = "{}"
        mock_deps.skill_store.dir = "/skills"
        resp = client.delete("/skills/custom/greet")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        mock_deps.skill_store.discover.assert_called_once()

    def test_not_found(self, client, mock_deps):
        mock_deps.skill_store.read_file.return_value = None
        resp = client.delete("/skills/custom/missing")
        assert resp.status_code == 404
