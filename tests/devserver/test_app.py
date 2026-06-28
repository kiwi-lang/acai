"""Unit tests for acai/devserver/app.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from acai.devserver.app import create_dev_app


@pytest.fixture
def manager():
    return MagicMock()


@pytest.fixture
def client(manager):
    app = create_dev_app(manager)
    return TestClient(app)


class TestListServices:
    def test_returns_status_all(self, client, manager):
        manager.status_all.return_value = [
            {"name": "web", "status": "running"},
            {"name": "worker", "status": "stopped"},
        ]
        resp = client.get("/dev/services")
        assert resp.status_code == 200
        assert resp.json() == [
            {"name": "web", "status": "running"},
            {"name": "worker", "status": "stopped"},
        ]
        manager.status_all.assert_called_once()

    def test_returns_empty_list(self, client, manager):
        manager.status_all.return_value = []
        resp = client.get("/dev/services")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetService:
    def test_known_service(self, client, manager):
        manager.status.return_value = {"name": "web", "status": "running", "pid": 123}
        resp = client.get("/dev/services/web")
        assert resp.status_code == 200
        assert resp.json()["name"] == "web"
        manager.status.assert_called_once_with("web")

    def test_unknown_service_returns_404(self, client, manager):
        manager.status.return_value = None
        resp = client.get("/dev/services/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json()
        assert "nonexistent" in resp.json()["error"]


class TestStartService:
    def test_start_success(self, client, manager):
        manager.start.return_value = {"name": "web", "status": "running"}
        resp = client.post("/dev/services/web/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        manager.start.assert_called_once_with("web")

    def test_start_error_returns_400(self, client, manager):
        manager.start.return_value = {"error": "service already running"}
        resp = client.post("/dev/services/web/start")
        assert resp.status_code == 400
        assert resp.json()["error"] == "service already running"


class TestStopService:
    def test_stop_success(self, client, manager):
        manager.stop.return_value = {"name": "web", "status": "stopped"}
        resp = client.post("/dev/services/web/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"
        manager.stop.assert_called_once_with("web")

    def test_stop_error_returns_400(self, client, manager):
        manager.stop.return_value = {"error": "service not running"}
        resp = client.post("/dev/services/web/stop")
        assert resp.status_code == 400
        assert resp.json()["error"] == "service not running"


class TestRestartService:
    def test_restart_success(self, client, manager):
        manager.restart.return_value = {"name": "web", "status": "running"}
        resp = client.post("/dev/services/web/restart")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        manager.restart.assert_called_once_with("web")

    def test_restart_error_returns_400(self, client, manager):
        manager.restart.return_value = {"error": "unknown service"}
        resp = client.post("/dev/services/web/restart")
        assert resp.status_code == 400
        assert resp.json()["error"] == "unknown service"


class TestGetLogs:
    def test_logs_success(self, client, manager):
        manager.logs.return_value = ["line1", "line2", "line3"]
        resp = client.get("/dev/services/web/logs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "web"
        assert body["lines"] == ["line1", "line2", "line3"]
        assert body["count"] == 3
        manager.logs.assert_called_once_with("web", tail=100)

    def test_logs_with_custom_tail(self, client, manager):
        manager.logs.return_value = ["only-one"]
        resp = client.get("/dev/services/web/logs?tail=1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        manager.logs.assert_called_once_with("web", tail=1)

    def test_logs_unknown_service_returns_404(self, client, manager):
        manager.logs.return_value = None
        resp = client.get("/dev/services/unknown/logs")
        assert resp.status_code == 404
        assert "error" in resp.json()

    def test_logs_tail_below_minimum_returns_422(self, client, manager):
        resp = client.get("/dev/services/web/logs?tail=0")
        assert resp.status_code == 422

    def test_logs_tail_above_maximum_returns_422(self, client, manager):
        resp = client.get("/dev/services/web/logs?tail=9999")
        assert resp.status_code == 422

    def test_logs_empty_list(self, client, manager):
        manager.logs.return_value = []
        resp = client.get("/dev/services/web/logs")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0
        assert resp.json()["lines"] == []


class TestCORSMiddleware:
    def test_cors_allows_any_origin(self, client, manager):
        manager.status_all.return_value = []
        resp = client.get(
            "/dev/services",
            headers={"Origin": "http://example.com"},
        )
        assert resp.headers.get("access-control-allow-origin") == "*"
