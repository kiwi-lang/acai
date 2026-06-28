"""Unit tests for acai/orchestrator/routes/git.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acai.orchestrator.routes import RouterDeps
from acai.orchestrator.routes.git import create_git_router


@pytest.fixture
def mock_deps(tmp_path):
    deps = MagicMock(spec=RouterDeps)
    deps.config = MagicMock()
    deps.config.workspace = str(tmp_path / "workspace")
    return deps


@pytest.fixture
def client(mock_deps):
    app = FastAPI()
    router = create_git_router(mock_deps)
    app.include_router(router)
    return TestClient(app)


class TestGitStatus:
    def test_status(self, client, mock_deps, tmp_path):
        with patch("acai.orchestrator.routes.git.gitsync.get_status") as mock_status:
            mock_status.return_value = {"configured": True, "remote": "origin"}
            resp = client.get("/git/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["configured"] is True
            assert "data_path" in data


class TestGitGenerateKey:
    def test_generate(self, client):
        with patch("acai.orchestrator.routes.git.gitsync.generate_ssh_key") as mock_gen:
            mock_gen.return_value = "ssh-ed25519 AAAA... user@host"
            resp = client.post("/git/generate-key")
            assert resp.status_code == 200
            assert "ssh-ed25519" in resp.json()["public_key"]


class TestGitSshKey:
    def test_key_exists(self, client):
        with patch("acai.orchestrator.routes.git.gitsync.get_ssh_public_key") as mock_key:
            mock_key.return_value = "ssh-ed25519 AAAA..."
            resp = client.get("/git/ssh-key")
            assert resp.status_code == 200
            assert resp.json()["public_key"] == "ssh-ed25519 AAAA..."

    def test_key_not_found(self, client):
        with patch("acai.orchestrator.routes.git.gitsync.get_ssh_public_key") as mock_key:
            mock_key.return_value = None
            resp = client.get("/git/ssh-key")
            assert resp.status_code == 404


class TestGitSetup:
    def test_setup_success(self, client):
        with patch("acai.orchestrator.routes.git.gitsync") as mock_gs:
            mock_gs.git_init = MagicMock()
            result = MagicMock()
            result.commit = "abc1234"
            result.push_error = ""
            result.error = ""
            mock_gs.git_sync.return_value = result
            mock_gs.ensure_sync_running = MagicMock()
            mock_gs.get_status = MagicMock()

            resp = client.post("/git/setup", json={"remote": "git@github.com:user/repo.git"})
            assert resp.status_code == 200
            assert resp.json()["remote"] == "git@github.com:user/repo.git"

    def test_setup_missing_remote(self, client):
        resp = client.post("/git/setup", json={})
        assert resp.status_code == 400
        assert "remote is required" in resp.json()["error"]


class TestGitSync:
    def test_sync(self, client):
        with patch("acai.orchestrator.routes.git.gitsync.git_sync") as mock_sync:
            result = MagicMock()
            result.commit = "abc123"
            result.pushed = True
            result.push_error = ""
            result.error = ""
            mock_sync.return_value = result

            resp = client.post("/git/sync")
            assert resp.status_code == 200
            assert resp.json()["pushed"] is True


class TestGitTestConnection:
    def test_success(self, client):
        with patch("acai.orchestrator.routes.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="Hi user! You've successfully authenticated",
                returncode=1,
            )
            resp = client.post("/git/test")
            assert resp.status_code == 200
            assert resp.json()["connected"] is True

    def test_failure(self, client):
        with patch("acai.orchestrator.routes.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="Permission denied (publickey)",
                returncode=255,
            )
            resp = client.post("/git/test")
            assert resp.status_code == 200
            assert resp.json()["connected"] is False
