"""Unit tests for acai/orchestrator/routes/projects.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acai.orchestrator.routes import RouterDeps
from acai.orchestrator.routes.projects import create_projects_router


@dataclass
class FakeProject:
    name: str = "my-project"
    language: str = "python"
    source: str = "new"
    template: str = "default"
    repo_url: str = ""
    provider: str = ""
    python_version: str = "3.12"
    venv_path: str = ".venv"
    path: str = "/workspace/my-project"
    refiner: str = "refiner"


@pytest.fixture
def mock_deps(tmp_path):
    deps = MagicMock(spec=RouterDeps)
    deps.projects = MagicMock()
    deps.config = MagicMock()
    deps.config.git.worktree_dir = str(tmp_path / "worktrees")
    deps.config.workspace = str(tmp_path / "workspace")
    return deps


@pytest.fixture
def client(mock_deps):
    app = FastAPI()
    router = create_projects_router(mock_deps)
    app.include_router(router)
    return TestClient(app)


class TestListProjects:
    def test_list(self, client, mock_deps):
        mock_deps.projects.list.return_value = [FakeProject(), FakeProject(name="other")]
        resp = client.get("/projects")
        assert resp.status_code == 200
        assert len(resp.json()) == 2


class TestCreateProject:
    def test_create_new(self, client, mock_deps):
        with patch("acai.orchestrator.routes.projects.scaffold") as mock_scaffold:
            resp = client.post("/projects", json={"name": "My Project", "language": "python"})
            assert resp.status_code == 201
            mock_scaffold.assert_called_once()
            mock_deps.projects.save.assert_called_once()

    def test_create_clone(self, client, mock_deps):
        with patch("acai.orchestrator.routes.projects.clone") as mock_clone:
            resp = client.post("/projects", json={
                "name": "cloned",
                "source": "clone",
                "repo_url": "git@github.com:user/repo.git",
            })
            assert resp.status_code == 201
            mock_clone.assert_called_once()

    def test_create_missing_name(self, client):
        resp = client.post("/projects", json={"language": "python"})
        assert resp.status_code == 400
        assert "name is required" in resp.json()["error"]

    def test_create_error(self, client, mock_deps):
        with patch("acai.orchestrator.routes.projects.scaffold", side_effect=RuntimeError("disk full")):
            resp = client.post("/projects", json={"name": "fail"})
            assert resp.status_code == 500
            assert "disk full" in resp.json()["error"]


class TestGetProject:
    def test_found(self, client, mock_deps):
        mock_deps.projects.get.return_value = FakeProject()
        resp = client.get("/projects/my-project")
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-project"

    def test_not_found(self, client, mock_deps):
        mock_deps.projects.get.return_value = None
        resp = client.get("/projects/missing")
        assert resp.status_code == 404


class TestUpdateProject:
    def test_update(self, client, mock_deps):
        proj = FakeProject()
        mock_deps.projects.get.return_value = proj
        resp = client.patch("/projects/my-project", json={"language": "rust"})
        assert resp.status_code == 200
        mock_deps.projects.save.assert_called_once()

    def test_not_found(self, client, mock_deps):
        mock_deps.projects.get.return_value = None
        resp = client.patch("/projects/missing", json={"language": "go"})
        assert resp.status_code == 404


class TestDeleteProject:
    def test_delete(self, client, mock_deps):
        resp = client.delete("/projects/my-project")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        mock_deps.projects.delete.assert_called_once_with("my-project")
