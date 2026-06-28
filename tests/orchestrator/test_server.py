"""Unit tests for acai/orchestrator/server.py.

Tests the inline endpoints defined in ``create_router`` (workers, tasks,
conversations, history, specs, worktrees, streaming, audit, status, events,
tools, toast, TTS, config, version/update) as well as the ``Orchestrator``
reaper, ``_task_json``, ``_json_body``, and ``_dump_request`` helpers.
"""

from __future__ import annotations

import json
import os
import queue as _stdlib_queue
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from acai.queue.work import TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(**overrides):
    """Return a MagicMock that looks like a ``Task`` row."""
    defaults = dict(
        id="t001",
        kind="task",
        gpu=0,
        title="Do something",
        description="desc",
        status=TaskStatus.PENDING,
        priority=0,
        spec="",
        spec_path="",
        context_path="",
        result_path="",
        worktree="",
        retries=0,
        max_retries=3,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        started_at=None,
        assigned_to="",
        depends_on="",
        error_log="",
        project="",
        agent="",
        parent_task="",
        root_task="",
        enable_thinking=None,
        conversation="",
        ext=None,
    )
    defaults.update(overrides)
    t = MagicMock()
    for k, v in defaults.items():
        setattr(t, k, v)
    return t


# ---------------------------------------------------------------------------
# Fixture: create_router with fully mocked internals
# ---------------------------------------------------------------------------

@pytest.fixture
def server_app(tmp_path):
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    os.makedirs(os.path.join(workspace, "projects"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "agents"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "knowledge"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "skills"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "workflows"), exist_ok=True)

    mock_config = MagicMock()
    mock_config.workspace = workspace
    mock_config.queue.url = f"sqlite:///{tmp_path}/test.db"
    mock_config.queue.poll_interval = 999
    mock_config.queue.task_timeout = 0
    mock_config.git.repo_path = str(tmp_path / "repo")
    mock_config.git.worktree_dir = str(tmp_path / "worktrees")
    mock_config.providers = []
    mock_config.worker.max_retries = 3
    mock_config.worker.tasks_dir = str(tmp_path / "tasks")
    mock_config.scribe.specs_dir = str(tmp_path / "specs")
    mock_config.audit.enabled = False
    mock_config.audit.dir = str(tmp_path / "audit")
    mock_config.ci = MagicMock()
    mock_config.tts = MagicMock()
    mock_config.tts.voice = "default"
    mock_config.sandbox = MagicMock()
    mock_config.dev = MagicMock()
    mock_config.model_sets = []

    active_provider = MagicMock()
    active_provider.name = "test-provider"
    active_provider.backend = "openai"
    active_provider.endpoint = "http://localhost:1234"
    mock_config.active_provider.return_value = active_provider
    mock_config.get_provider.return_value = active_provider

    mock_lb = MagicMock()
    mock_lb.start = MagicMock()
    mock_lb.list_workers.return_value = []
    mock_lb.register.return_value = "w-001"
    mock_lb.unregister.return_value = True
    mock_lb.heartbeat.return_value = True

    mock_tracker = MagicMock()
    mock_tracker.get_partial.return_value = (None, "")
    mock_tracker.subscribe.return_value = _stdlib_queue.Queue()

    mock_queue = MagicMock()
    mock_queue.list.return_value = []
    mock_queue.get.return_value = None

    patches = [
        patch("acai.orchestrator.server.WorkQueue", return_value=mock_queue),
        patch("acai.orchestrator.server.GitTracker"),
        patch("acai.orchestrator.server.ProjectStore"),
        patch("acai.orchestrator.server.ChatStore"),
        patch("acai.orchestrator.server.ProviderScheduler"),
        patch("acai.orchestrator.server.AgentStore"),
        patch("acai.orchestrator.server.KnowledgeStore"),
        patch("acai.orchestrator.server.KnowledgeDB"),
        patch("acai.orchestrator.server.EventBus"),
        patch("acai.orchestrator.server.load_config"),
        patch("acai.orchestrator.server.StreamTracker", return_value=mock_tracker),
        patch("acai.orchestrator.server.LoadBalancer", return_value=mock_lb),
        patch("acai.orchestrator.tools.discover_tools"),
        patch("acai.tools.meta._configure"),
        patch("acai.orchestrator.skill_store.SkillStore"),
        patch("acai.tools.skills._configure"),
        patch("acai.tools.ci._configure"),
        patch("acai.orchestrator.tts.TTSService"),
        patch("acai.orchestrator.tts.ingest_voice_catalog"),
        patch("threading.Thread"),
        patch("acai.orchestrator.server.create_provider_router", return_value=MagicMock()),
        patch("acai.orchestrator.routes.knowledge.create_knowledge_router", return_value=MagicMock()),
        patch("acai.orchestrator.routes.skills.create_skills_router", return_value=MagicMock()),
        patch("acai.orchestrator.routes.agents.create_agents_router", return_value=MagicMock()),
        patch("acai.orchestrator.routes.projects.create_projects_router", return_value=MagicMock()),
        patch("acai.orchestrator.routes.git.create_git_router", return_value=MagicMock()),
        patch("acai.orchestrator.routes.workflows.create_workflows_router", return_value=MagicMock()),
        patch("acai.orchestrator.routes.conversations.create_conversations_router", return_value=MagicMock()),
    ]

    for p in patches:
        p.start()

    from acai.orchestrator.server import create_router

    router, q, events, chat, cfg, trk, sio_ref, lb = create_router(
        config=mock_config,
        stream_tracker=mock_tracker,
        load_balancer=mock_lb,
    )

    app = FastAPI()
    app.include_router(router)

    for p in patches:
        p.stop()

    return {
        "app": app,
        "queue": q,
        "events": events,
        "chat": chat,
        "config": mock_config,
        "tracker": mock_tracker,
        "sio_ref": sio_ref,
        "lb": mock_lb,
        "tmp": tmp_path,
    }


@pytest.fixture
def client(server_app):
    return TestClient(server_app["app"])


# =====================================================================
# _json_body
# =====================================================================

class TestJsonBody:
    @pytest.mark.asyncio
    async def test_valid_json(self):
        from acai.orchestrator.server import _json_body
        req = MagicMock(spec=Request)
        req.json = AsyncMock(return_value={"key": "val"})
        assert await _json_body(req) == {"key": "val"}

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self):
        from acai.orchestrator.server import _json_body
        req = MagicMock(spec=Request)
        req.json = AsyncMock(side_effect=ValueError("bad json"))
        assert await _json_body(req) == {}


# =====================================================================
# _task_json
# =====================================================================

class TestTaskJson:
    def test_all_fields_present(self):
        from acai.orchestrator.server import _task_json
        t = _make_task()
        result = _task_json(t)
        assert result["id"] == "t001"
        assert result["kind"] == "task"
        assert result["title"] == "Do something"
        assert result["status"] == TaskStatus.PENDING
        assert result["retries"] == 0
        assert result["max_retries"] == 3

    def test_none_timestamps(self):
        from acai.orchestrator.server import _task_json
        t = _make_task(created_at=None, updated_at=None, started_at=None)
        result = _task_json(t)
        assert result["created_at"] == ""
        assert result["updated_at"] == ""
        assert result["started_at"] == ""

    def test_none_optional_strings(self):
        from acai.orchestrator.server import _task_json
        t = _make_task(spec=None, project=None, agent=None, parent_task=None,
                       root_task=None, conversation=None)
        result = _task_json(t)
        assert result["spec"] == ""
        assert result["project"] == ""
        assert result["agent"] == ""
        assert result["parent_task"] == ""
        assert result["root_task"] == ""
        assert result["conversation"] == ""


# =====================================================================
# _dump_request
# =====================================================================

class TestDumpRequest:
    def test_writes_json_file(self, tmp_path):
        from acai.orchestrator.server import _dump_request
        ws = str(tmp_path / "ws")
        os.makedirs(ws)
        _dump_request(ws, "task-123", [{"role": "user", "content": "hi"}], "coder")
        dump_dir = os.path.join(ws, ".requests")
        assert os.path.isdir(dump_dir)
        files = os.listdir(dump_dir)
        assert len(files) == 1
        with open(os.path.join(dump_dir, files[0])) as f:
            data = json.load(f)
        assert data["task_id"] == "task-123"
        assert data["agent"] == "coder"
        assert len(data["messages"]) == 1

    def test_with_tools(self, tmp_path):
        from acai.orchestrator.server import _dump_request
        ws = str(tmp_path / "ws2")
        os.makedirs(ws)
        tools = [{"name": "shell_run"}]
        _dump_request(ws, "t2", [], "coder", tools=tools)
        dump_dir = os.path.join(ws, ".requests")
        files = os.listdir(dump_dir)
        with open(os.path.join(dump_dir, files[0])) as f:
            data = json.load(f)
        assert data["tools"] == tools

    def test_os_error_handled(self, tmp_path):
        from acai.orchestrator.server import _dump_request
        ws = str(tmp_path / "ws3")
        os.makedirs(ws)
        with patch("builtins.open", side_effect=OSError("disk full")):
            _dump_request(ws, "t3", [], "coder")


# =====================================================================
# Worker endpoints
# =====================================================================

class TestWorkerEndpoints:
    def test_register_worker(self, client, server_app):
        resp = client.post("/agent/workers/register", json={"url": "http://w1:8080"})
        assert resp.status_code == 201
        assert "worker_id" in resp.json()

    def test_register_worker_missing_url(self, client):
        resp = client.post("/agent/workers/register", json={})
        assert resp.status_code == 400
        assert "url is required" in resp.json()["error"]

    def test_register_worker_empty_url(self, client):
        resp = client.post("/agent/workers/register", json={"url": ""})
        assert resp.status_code == 400

    def test_unregister_worker(self, client, server_app):
        resp = client.delete("/agent/workers/w-001")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_unregister_worker_not_found(self, client, server_app):
        server_app["lb"].unregister.return_value = False
        resp = client.delete("/agent/workers/unknown")
        assert resp.status_code == 404

    def test_list_workers(self, client, server_app):
        w = MagicMock()
        w.to_dict.return_value = {"id": "w1", "url": "http://w1:8080"}
        server_app["lb"].list_workers.return_value = [w]
        resp = client.get("/agent/workers")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_heartbeat(self, client, server_app):
        resp = client.post("/agent/workers/heartbeat", json={
            "worker_id": "w-001", "telemetry": {"gpu_util": 0.5},
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_heartbeat_missing_worker_id(self, client):
        resp = client.post("/agent/workers/heartbeat", json={})
        assert resp.status_code == 400
        assert "worker_id required" in resp.json()["error"]

    def test_heartbeat_unknown_worker(self, client, server_app):
        server_app["lb"].heartbeat.return_value = False
        resp = client.post("/agent/workers/heartbeat", json={"worker_id": "ghost"})
        assert resp.status_code == 404
        assert "unknown worker" in resp.json()["error"]


# =====================================================================
# History endpoints
# =====================================================================

class TestHistoryEndpoints:
    def test_history_no_conversation(self, client):
        resp = client.get("/agent/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []
        assert data["streaming"] is None

    def test_history_with_conversation(self, client, server_app):
        server_app["chat"].read.return_value = [
            {"role": "user", "content": "hello"},
        ]
        resp = client.get("/agent/history?conversation=conv-1")
        assert resp.status_code == 200
        assert len(resp.json()["messages"]) == 1

    def test_history_with_active_stream(self, client, server_app):
        server_app["chat"].read.return_value = []
        server_app["tracker"].get_partial.return_value = ("task-42", "partial text")
        resp = client.get("/agent/history?conversation=conv-1")
        data = resp.json()
        assert data["streaming"]["task_id"] == "task-42"
        assert data["streaming"]["partial"] == "partial text"

    def test_clear_history(self, client, server_app):
        resp = client.delete("/agent/history?conversation=conv-1")
        assert resp.status_code == 200
        assert resp.json()["cleared"] is True
        server_app["chat"].clear.assert_called_once_with("conv-1")

    def test_clear_history_no_conversation(self, client, server_app):
        server_app["chat"].clear.reset_mock()
        resp = client.delete("/agent/history")
        assert resp.status_code == 200
        server_app["chat"].clear.assert_not_called()


# =====================================================================
# Task CRUD endpoints
# =====================================================================

class TestTaskEndpoints:
    def test_list_tasks(self, client, server_app):
        t = _make_task()
        server_app["queue"].list.return_value = [t]
        resp = client.get("/agent/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_tasks_with_filters(self, client, server_app):
        server_app["queue"].list.return_value = []
        resp = client.get("/agent/tasks?status=pending&project=myproj&root_only=true")
        assert resp.status_code == 200
        server_app["queue"].list.assert_called_with(
            status="pending", project="myproj", root_only=True,
        )

    def test_create_task(self, client, server_app):
        t = _make_task(title="New task")
        server_app["queue"].push.return_value = t
        resp = client.post("/agent/tasks", json={"title": "New task"})
        assert resp.status_code == 201
        assert resp.json()["title"] == "New task"

    def test_create_task_missing_title(self, client):
        resp = client.post("/agent/tasks", json={})
        assert resp.status_code == 400
        assert "title is required" in resp.json()["error"]

    def test_create_task_with_parent(self, client, server_app):
        server_app["queue"].resolve_root.return_value = "root-1"
        t = _make_task(parent_task="parent-1", root_task="root-1")
        server_app["queue"].push.return_value = t
        resp = client.post("/agent/tasks", json={
            "title": "Subtask",
            "parent_task": "parent-1",
        })
        assert resp.status_code == 201

    def test_get_task(self, client, server_app):
        t = _make_task()
        server_app["queue"].get.return_value = t
        resp = client.get("/agent/tasks/t001")
        assert resp.status_code == 200
        assert resp.json()["id"] == "t001"

    def test_get_task_not_found(self, client, server_app):
        server_app["queue"].get.return_value = None
        resp = client.get("/agent/tasks/missing")
        assert resp.status_code == 404

    def test_get_task_tree(self, client, server_app):
        t1 = _make_task(id="t1")
        t2 = _make_task(id="t2", parent_task="t1")
        server_app["queue"].list_tree.return_value = [t1, t2]
        resp = client.get("/agent/tasks/t1/tree")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_task_tree_not_found(self, client, server_app):
        server_app["queue"].list_tree.return_value = []
        resp = client.get("/agent/tasks/missing/tree")
        assert resp.status_code == 404

    def test_update_task(self, client, server_app):
        t = _make_task(title="Updated")
        server_app["queue"].get.return_value = t
        resp = client.patch("/agent/tasks/t001", json={"title": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated"

    def test_update_task_no_fields(self, client):
        resp = client.patch("/agent/tasks/t001", json={})
        assert resp.status_code == 400
        assert "no updatable fields" in resp.json()["error"]

    def test_update_task_filters_unknown_fields(self, client, server_app):
        t = _make_task()
        server_app["queue"].get.return_value = t
        resp = client.patch("/agent/tasks/t001", json={
            "title": "X",
            "unknown_field": "ignored",
        })
        assert resp.status_code == 200
        call_kwargs = server_app["queue"].update.call_args[1]
        assert "unknown_field" not in call_kwargs

    def test_update_task_not_found(self, client, server_app):
        server_app["queue"].get.return_value = None
        resp = client.patch("/agent/tasks/t001", json={"title": "X"})
        assert resp.status_code == 404


# =====================================================================
# Work result endpoint
# =====================================================================

class TestWorkResult:
    def test_result_success(self, client, server_app):
        t = _make_task(conversation="conv-1", ext={})
        server_app["queue"].get.return_value = t
        os.makedirs(server_app["config"].worker.tasks_dir, exist_ok=True)
        resp = client.post("/agent/work/result/t001", json={
            "result": "done!",
            "kind": "llm_complete",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_result_task_not_found(self, client, server_app):
        server_app["queue"].get.return_value = None
        resp = client.post("/agent/work/result/missing", json={"result": ""})
        assert resp.status_code == 404

    def test_result_with_error_and_retries(self, client, server_app):
        t = _make_task(retries=0, max_retries=3, conversation="conv-1", ext={})
        server_app["queue"].get.return_value = t
        os.makedirs(server_app["config"].worker.tasks_dir, exist_ok=True)
        resp = client.post("/agent/work/result/t001", json={
            "error": "something broke",
        })
        assert resp.status_code == 200
        server_app["queue"].update.assert_called()
        call_kwargs = server_app["queue"].update.call_args[1]
        assert call_kwargs["status"] == TaskStatus.READY
        assert call_kwargs["retries"] == 1

    def test_result_with_error_exhausted_retries(self, client, server_app):
        t = _make_task(
            retries=3, max_retries=3, conversation="conv-1",
            ext={}, spec_path="",
        )
        server_app["queue"].get.return_value = t
        os.makedirs(server_app["config"].worker.tasks_dir, exist_ok=True)
        resp = client.post("/agent/work/result/t001", json={
            "error": "fatal",
        })
        assert resp.status_code == 200
        call_kwargs = server_app["queue"].update.call_args[1]
        assert call_kwargs["status"] == TaskStatus.FAILED

    def test_result_with_error_appends_to_chat(self, client, server_app):
        t = _make_task(
            retries=3, max_retries=3, conversation="conv-1",
            ext={}, spec_path="",
        )
        server_app["queue"].get.return_value = t
        os.makedirs(server_app["config"].worker.tasks_dir, exist_ok=True)
        resp = client.post("/agent/work/result/t001", json={"error": "oops"})
        assert resp.status_code == 200
        server_app["chat"].append.assert_called()

    def test_result_scheduler_driven_no_chat(self, client, server_app):
        t = _make_task(
            retries=3, max_retries=3, conversation="conv-1",
            ext={"scheduler_driven": True}, spec_path="",
        )
        server_app["queue"].get.return_value = t
        os.makedirs(server_app["config"].worker.tasks_dir, exist_ok=True)
        server_app["chat"].append.reset_mock()
        resp = client.post("/agent/work/result/t001", json={"error": "oops"})
        assert resp.status_code == 200
        server_app["chat"].append.assert_not_called()

    def test_result_tool_call_appends(self, client, server_app):
        t = _make_task(
            title="tool: shell_run", conversation="conv-1", ext={},
            status=TaskStatus.IN_PROGRESS,
        )
        server_app["queue"].get.return_value = t
        os.makedirs(server_app["config"].worker.tasks_dir, exist_ok=True)
        resp = client.post("/agent/work/result/t001", json={
            "result": "output",
            "kind": "tool_call",
            "tool": "shell_run",
        })
        assert resp.status_code == 200

    def test_result_already_chained(self, client, server_app):
        t = _make_task(conversation="conv-1", ext={}, status="chained")
        server_app["queue"].get.return_value = t
        os.makedirs(server_app["config"].worker.tasks_dir, exist_ok=True)
        resp = client.post("/agent/work/result/t001", json={
            "result": "ok", "kind": "llm_complete",
        })
        assert resp.status_code == 200


# =====================================================================
# Specs endpoints
# =====================================================================

class TestSpecsEndpoints:
    def test_list_specs_empty(self, client, server_app):
        resp = client.get("/agent/specs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_specs(self, client, server_app):
        specs_dir = server_app["config"].scribe.specs_dir
        os.makedirs(specs_dir, exist_ok=True)
        for name in ["spec_a.md", "spec_b.md"]:
            with open(os.path.join(specs_dir, name), "w") as f:
                f.write(f"# {name}")
        resp = client.get("/agent/specs")
        assert resp.status_code == 200
        assert sorted(resp.json()) == ["spec_a.md", "spec_b.md"]

    def test_get_spec(self, client, server_app):
        specs_dir = server_app["config"].scribe.specs_dir
        os.makedirs(specs_dir, exist_ok=True)
        with open(os.path.join(specs_dir, "my.md"), "w") as f:
            f.write("content")
        resp = client.get("/agent/specs/my.md")
        assert resp.status_code == 200
        assert resp.json()["name"] == "my.md"
        assert resp.json()["content"] == "content"

    def test_get_spec_not_found(self, client):
        resp = client.get("/agent/specs/nonexistent.md")
        assert resp.status_code == 404


# =====================================================================
# Worktrees endpoint
# =====================================================================

class TestWorktrees:
    def test_list_worktrees(self, client, server_app):
        resp = client.get("/agent/worktrees")
        assert resp.status_code == 200


# =====================================================================
# Audit endpoints
# =====================================================================

class TestAuditEndpoints:
    def test_get_audit_not_found(self, client):
        resp = client.get("/agent/audit/nonexistent")
        assert resp.status_code == 404

    def test_get_audit(self, client, server_app):
        audit_dir = server_app["config"].audit.dir
        audit_id = "req-001"
        path = os.path.join(audit_dir, audit_id)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "audit.json"), "w") as f:
            json.dump({"request_id": audit_id, "events": []}, f)
        resp = client.get(f"/agent/audit/{audit_id}")
        assert resp.status_code == 200
        assert resp.json()["request_id"] == audit_id

    def test_list_audits_empty(self, client):
        resp = client.get("/agent/audit")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_audits(self, client, server_app):
        audit_dir = server_app["config"].audit.dir
        os.makedirs(audit_dir, exist_ok=True)
        for i in range(3):
            d = os.path.join(audit_dir, f"req-{i:03}")
            os.makedirs(d)
            with open(os.path.join(d, "audit.json"), "w") as f:
                json.dump({
                    "request_id": f"req-{i:03}",
                    "started_at_iso": "2025-01-01T00:00:00",
                    "total_duration_ms": 100,
                    "meta": {},
                }, f)
        resp = client.get("/agent/audit?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_audits_skips_malformed(self, client, server_app):
        audit_dir = server_app["config"].audit.dir
        os.makedirs(audit_dir, exist_ok=True)
        bad = os.path.join(audit_dir, "bad-audit")
        os.makedirs(bad)
        with open(os.path.join(bad, "audit.json"), "w") as f:
            f.write("not json {{{")
        resp = client.get("/agent/audit")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_audits_skips_latest_symlink(self, client, server_app):
        audit_dir = server_app["config"].audit.dir
        os.makedirs(audit_dir, exist_ok=True)
        latest = os.path.join(audit_dir, "latest")
        os.makedirs(latest)
        resp = client.get("/agent/audit")
        assert resp.status_code == 200
        assert resp.json() == []


# =====================================================================
# Status endpoint
# =====================================================================

class TestStatusEndpoint:
    def test_status(self, client, server_app):
        server_app["queue"].list.return_value = []
        server_app["events"].history = []
        resp = client.get("/agent/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "queue" in data
        assert data["active_provider"] == "test-provider"
        assert data["llm_backend"] == "openai"
        assert "workers" in data


# =====================================================================
# Events endpoint
# =====================================================================

class TestEventsEndpoint:
    def test_list_events_empty(self, client, server_app):
        server_app["events"].history = []
        resp = client.get("/agent/events")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_events(self, client, server_app):
        e = MagicMock()
        e.kind.value = "task_completed"
        e.source = "worker"
        e.data = {"task_id": "t1"}
        e.timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
        server_app["events"].history = [e]
        resp = client.get("/agent/events?limit=10")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["kind"] == "task_completed"


# =====================================================================
# Toast endpoint
# =====================================================================

class TestToastEndpoint:
    def test_toast_no_socketio(self, client, server_app):
        server_app["sio_ref"][0] = None
        resp = client.post("/agent/toast", json={"message": "Hello"})
        assert resp.status_code == 503
        assert "socketio not ready" in resp.json()["error"]

    def test_toast_success(self, client, server_app):
        sio = MagicMock()
        server_app["sio_ref"][0] = sio
        resp = client.post("/agent/toast", json={
            "message": "Test toast",
            "title": "Alert",
            "status": "warning",
            "duration": 3000,
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        sio.emit.assert_called_once()


# =====================================================================
# Config endpoints
# =====================================================================

class TestConfigEndpoints:
    def test_get_config(self, client):
        with patch("acai.orchestrator.config.config_to_dict", return_value={"workspace": "/tmp"}):
            resp = client.get("/agent/config")
            assert resp.status_code == 200

    def test_patch_config(self, client, server_app):
        from dataclasses import dataclass
        @dataclass
        class _FakeSandbox:
            image: str = ""
        server_app["config"].sandbox = _FakeSandbox()
        with patch("acai.orchestrator.config.config_to_dict", return_value={"sandbox": {}}):
            with patch("acai.orchestrator.config.save_config"):
                resp = client.patch("/agent/config", json={
                    "sandbox": {"image": "ubuntu:22.04"},
                })
                assert resp.status_code == 200
                assert server_app["config"].sandbox.image == "ubuntu:22.04"


# =====================================================================
# Version/Update endpoints
# =====================================================================

class TestVersionEndpoints:
    def test_get_version(self, client):
        with patch("acai.orchestrator.updater.get_latest_version", return_value="1.2.3"):
            with patch("acai.orchestrator.updater.needs_update", return_value=True):
                resp = client.get("/agent/version")
                assert resp.status_code == 200
                data = resp.json()
                assert "version" in data

    def test_get_version_no_latest(self, client):
        with patch("acai.orchestrator.updater.get_latest_version", return_value=None):
            resp = client.get("/agent/version")
            assert resp.status_code == 200
            data = resp.json()
            assert "version" in data
            assert "latest" not in data


# =====================================================================
# Tools namespaces endpoint
# =====================================================================

class TestToolNamespaces:
    def test_list_namespaces(self, client):
        resp = client.get("/agent/tools/namespaces")
        assert resp.status_code == 200


# =====================================================================
# Streaming SSE endpoint
# =====================================================================

class TestStreamEndpoint:
    def test_stream_with_done_event(self, client, server_app):
        q = _stdlib_queue.Queue()
        server_app["tracker"].subscribe.return_value = q
        server_app["tracker"].get_partial.return_value = (None, "")

        q.put({"event_type": "done", "data": {"task_id": "t1"}})

        resp = client.get("/agent/stream/stream-1")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert "event: done" in resp.text

    def test_stream_with_replay(self, client, server_app):
        q = _stdlib_queue.Queue()
        server_app["tracker"].subscribe.return_value = q
        server_app["tracker"].get_partial.return_value = ("task-42", "hello world")

        q.put({"event_type": "done", "data": {}})

        resp = client.get("/agent/stream/stream-1")
        assert "event: token" in resp.text
        assert "hello world" in resp.text

    def test_stream_error_event_stops(self, client, server_app):
        q = _stdlib_queue.Queue()
        server_app["tracker"].subscribe.return_value = q
        server_app["tracker"].get_partial.return_value = (None, "")

        q.put({"event_type": "error", "data": {"message": "fail"}})

        resp = client.get("/agent/stream/stream-1")
        assert "event: error" in resp.text


# =====================================================================
# Uber converse endpoint
# =====================================================================

class TestUberConverse:
    def test_missing_message(self, client):
        resp = client.post("/agent/uber/converse", json={})
        assert resp.status_code == 400
        assert "message is required" in resp.json()["error"]

    def test_uber_converse_timeout(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError("no workers"))
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        resp = client.post("/agent/uber/converse", json={"message": "hello"})
        assert resp.status_code == 200
        assert "No worker available" in resp.text

    def test_uber_converse_generic_error(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        resp = client.post("/agent/uber/converse", json={"message": "hello"})
        assert resp.status_code == 200
        assert "RuntimeError: boom" in resp.text

    def test_uber_converse_streams_events(self, client, server_app):
        mock_worker = MagicMock()
        mock_worker.url = "http://localhost:8080"

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(return_value=mock_worker)
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        async def _fake_run(work):
            yield {"event_type": "message", "data": {"text": "hi"}}

        mock_graph = MagicMock()
        mock_graph.run = _fake_run

        with patch("acai.orchestrator.server.UberGraph.from_work", return_value=mock_graph):
            resp = client.post("/agent/uber/converse", json={
                "message": "hello",
                "agent": "default",
            })
            assert resp.status_code == 200
            assert "event: message" in resp.text

    def test_uber_converse_with_provider_override(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        resp = client.post("/agent/uber/converse", json={
            "message": "hello",
            "provider": "test-provider",
            "model": "gpt-4",
        })
        assert resp.status_code == 200

    def test_uber_converse_provider_auto(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        resp = client.post("/agent/uber/converse", json={
            "message": "hello",
            "provider": "auto",
        })
        assert resp.status_code == 200

    def test_uber_converse_provider_not_found(self, client, server_app):
        server_app["config"].get_provider.return_value = None
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        resp = client.post("/agent/uber/converse", json={
            "message": "hello",
            "provider": "nonexistent",
        })
        assert resp.status_code == 200


# =====================================================================
# Think converse endpoint
# =====================================================================

class TestThinkConverse:
    def test_missing_message(self, client):
        resp = client.post("/agent/think/converse", json={})
        assert resp.status_code == 400
        assert "message is required" in resp.json()["error"]

    def test_think_converse_creates_conversation(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        mock_meta = MagicMock()
        mock_meta.id = "new-conv-id"
        server_app["chat"].create.return_value = mock_meta
        server_app["chat"].get_meta.return_value = {}

        resp = client.post("/agent/think/converse", json={"message": "think about this"})
        assert resp.status_code == 200
        assert "X-Conversation" in resp.headers
        server_app["chat"].create.assert_called_once()

    def test_think_converse_existing_conversation(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        server_app["chat"].get_meta.return_value = {"agent": "coder"}

        resp = client.post("/agent/think/converse", json={
            "message": "continue",
            "conversation": "existing-conv",
            "provider": "my-provider",
        })
        assert resp.status_code == 200
        server_app["chat"].create.assert_not_called()

    def test_think_converse_streams_events(self, client, server_app):
        mock_worker = MagicMock()
        mock_worker.url = "http://localhost:8080"
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(return_value=mock_worker)
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        mock_meta = MagicMock()
        mock_meta.id = "conv-test"
        server_app["chat"].create.return_value = mock_meta
        server_app["chat"].get_meta.return_value = {}

        async def _fake_run(work):
            yield {"event_type": "token", "data": {"token": "word"}}

        mock_graph = MagicMock()
        mock_graph.run = _fake_run

        with patch("acai.orchestrator.server.ThinkGraph.from_work", return_value=mock_graph):
            resp = client.post("/agent/think/converse", json={"message": "think"})
            assert resp.status_code == 200
            assert "event: meta" in resp.text
            assert "event: token" in resp.text

    def test_think_converse_generic_error(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=ValueError("bad input"))
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        mock_meta = MagicMock()
        mock_meta.id = "conv-err"
        server_app["chat"].create.return_value = mock_meta
        server_app["chat"].get_meta.return_value = {}

        resp = client.post("/agent/think/converse", json={"message": "think"})
        assert resp.status_code == 200
        assert "ValueError: bad input" in resp.text

    def test_think_converse_with_provider_and_model(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        mock_meta = MagicMock()
        mock_meta.id = "conv-prov"
        server_app["chat"].create.return_value = mock_meta
        server_app["chat"].get_meta.return_value = {}

        resp = client.post("/agent/think/converse", json={
            "message": "think",
            "provider": "test-provider",
            "model": "gpt-4",
        })
        assert resp.status_code == 200

    def test_think_converse_provider_auto(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        mock_meta = MagicMock()
        mock_meta.id = "conv-auto"
        server_app["chat"].create.return_value = mock_meta
        server_app["chat"].get_meta.return_value = {}

        resp = client.post("/agent/think/converse", json={
            "message": "think",
            "provider": "auto",
        })
        assert resp.status_code == 200

    def test_think_converse_update_meta_with_agent(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        server_app["chat"].get_meta.return_value = {"agent": "old"}

        resp = client.post("/agent/think/converse", json={
            "message": "continue",
            "conversation": "existing",
            "agent": "new-agent",
        })
        assert resp.status_code == 200
        server_app["chat"].update_meta.assert_called()


# =====================================================================
# Scribe converse endpoint
# =====================================================================

class TestScribeConverse:
    def test_missing_message(self, client):
        resp = client.post("/agent/scribe/converse", json={})
        assert resp.status_code == 400
        assert "message is required" in resp.json()["error"]

    def test_scribe_converse_timeout(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        mock_meta = MagicMock()
        mock_meta.id = "scribe-conv"
        server_app["chat"].create.return_value = mock_meta
        server_app["chat"].get_meta.return_value = {}

        resp = client.post("/agent/scribe/converse", json={"message": "scribe this"})
        assert resp.status_code == 200
        assert "No worker available" in resp.text

    def test_scribe_converse_generic_error(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("scribe fail"))
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        mock_meta = MagicMock()
        mock_meta.id = "scribe-conv"
        server_app["chat"].create.return_value = mock_meta
        server_app["chat"].get_meta.return_value = {}

        resp = client.post("/agent/scribe/converse", json={"message": "scribe"})
        assert resp.status_code == 200
        assert "RuntimeError: scribe fail" in resp.text

    def test_scribe_converse_streams_events(self, client, server_app):
        mock_worker = MagicMock()
        mock_worker.url = "http://localhost:8080"
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(return_value=mock_worker)
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        mock_meta = MagicMock()
        mock_meta.id = "scribe-conv"
        server_app["chat"].create.return_value = mock_meta
        server_app["chat"].get_meta.return_value = {}

        async def _fake_run(work):
            yield {"event_type": "message", "data": {"text": "scribed"}}

        mock_graph = MagicMock()
        mock_graph.run = _fake_run

        with patch("acai.orchestrator.server.ConverseScribeGraph.from_work", return_value=mock_graph):
            resp = client.post("/agent/scribe/converse", json={"message": "scribe it"})
            assert resp.status_code == 200
            assert "event: meta" in resp.text
            assert "event: message" in resp.text

    def test_scribe_converse_existing_conversation(self, client, server_app):
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        server_app["chat"].get_meta.return_value = {"agent": "scribe"}

        resp = client.post("/agent/scribe/converse", json={
            "message": "continue",
            "conversation": "existing-conv",
            "agent": "scribe-agent",
            "provider": "openai",
        })
        assert resp.status_code == 200
        server_app["chat"].create.assert_not_called()
        server_app["chat"].update_meta.assert_called()


# =====================================================================
# Run task endpoint
# =====================================================================

class TestRunTask:
    def test_run_task_not_found(self, client, server_app):
        server_app["queue"].get.return_value = None
        resp = client.post("/agent/tasks/missing/run")
        assert resp.status_code == 404

    def test_run_task_already_running(self, client, server_app):
        t = _make_task(status=TaskStatus.IN_PROGRESS)
        server_app["queue"].get.return_value = t
        resp = client.post("/agent/tasks/t001/run")
        assert resp.status_code == 409
        assert "already running" in resp.json()["error"]

    def test_run_task_timeout(self, client, server_app):
        t = _make_task(status=TaskStatus.READY, project="")
        server_app["queue"].get.return_value = t
        server_app["config"].active_provider.return_value = MagicMock(name="prov")
        server_app["chat"].task_history.return_value = []
        server_app["chat"].read.return_value = []

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        resp = client.post("/agent/tasks/t001/run")
        assert resp.status_code == 200
        assert "event: error" in resp.text
        assert "No worker available" in resp.text
        assert "event: task_status" in resp.text

    def test_run_task_generic_error(self, client, server_app):
        t = _make_task(status=TaskStatus.READY, project="")
        server_app["queue"].get.return_value = t
        server_app["config"].active_provider.return_value = MagicMock(name="prov")
        server_app["chat"].task_history.return_value = []
        server_app["chat"].read.return_value = []

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("kaboom"))
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        resp = client.post("/agent/tasks/t001/run")
        assert resp.status_code == 200
        assert "RuntimeError: kaboom" in resp.text
        assert "event: task_status" in resp.text

    def test_run_task_streams_events(self, client, server_app):
        t = _make_task(status=TaskStatus.READY, project="")
        server_app["queue"].get.return_value = t
        server_app["config"].active_provider.return_value = MagicMock(name="prov")
        server_app["chat"].task_history.return_value = []
        server_app["chat"].read.return_value = []

        mock_worker = MagicMock()
        mock_worker.url = "http://localhost:8080"
        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(return_value=mock_worker)
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        async def _fake_run(work):
            yield {"event_type": "message", "data": {"text": "result"}}

        mock_graph = MagicMock()
        mock_graph.run = _fake_run

        with patch("acai.orchestrator.server.get_graph", return_value=mock_graph):
            resp = client.post("/agent/tasks/t001/run")
            assert resp.status_code == 200
            assert "event: meta" in resp.text
            assert "event: message" in resp.text
            assert "event: task_status" in resp.text

    def test_run_task_retry(self, client, server_app):
        t = _make_task(status=TaskStatus.FAILED, project="")
        server_app["queue"].get.return_value = t
        server_app["config"].active_provider.return_value = MagicMock(name="prov")
        server_app["chat"].task_history.return_value = []
        server_app["chat"].read.return_value = []

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        resp = client.post("/agent/tasks/t001/run")
        assert resp.status_code == 200
        body = resp.text
        assert '"retry": true' in body or '"retry":true' in body

    def test_run_task_with_project(self, client, server_app):
        t = _make_task(status=TaskStatus.READY, project="myproj")
        server_app["queue"].get.return_value = t
        server_app["config"].active_provider.return_value = MagicMock(name="prov")
        server_app["chat"].task_history.return_value = []
        server_app["chat"].read.return_value = []

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        resp = client.post("/agent/tasks/t001/run")
        assert resp.status_code == 200

    def test_run_task_with_prior_history(self, client, server_app):
        t = _make_task(status=TaskStatus.READY, project="proj")
        server_app["queue"].get.return_value = t
        server_app["config"].active_provider.return_value = MagicMock(name="prov")
        server_app["chat"].task_history.return_value = ["/path/to/conv_0.json"]
        server_app["chat"].read_task_conversation.return_value = [
            {"role": "user", "content": "prior message"},
        ]
        server_app["chat"].read.return_value = []

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        resp = client.post("/agent/tasks/t001/run")
        assert resp.status_code == 200

    def test_run_task_with_description(self, client, server_app):
        t = _make_task(
            status=TaskStatus.READY, project="",
            title="Fix bug", description="There is a bug in module X",
        )
        server_app["queue"].get.return_value = t
        server_app["config"].active_provider.return_value = MagicMock(name="prov")
        server_app["chat"].task_history.return_value = []
        server_app["chat"].read.return_value = []

        async_ctx = MagicMock()
        async_ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        server_app["lb"].acquire.return_value = async_ctx

        resp = client.post("/agent/tasks/t001/run")
        assert resp.status_code == 200


# =====================================================================
# TTS endpoints
# =====================================================================

class TestTTSEndpoints:
    def test_tts_voices(self, client):
        resp = client.get("/agent/tts/voices")
        assert resp.status_code == 200

    def test_tts_ingest_catalog_missing(self, client):
        resp = client.post("/agent/tts/voices/catalog", json={})
        assert resp.status_code == 400
        assert "catalog dict is required" in resp.json()["error"]

    def test_tts_ingest_catalog_not_dict(self, client):
        resp = client.post("/agent/tts/voices/catalog", json={"catalog": "string"})
        assert resp.status_code == 400

    def test_tts_download_missing_voice(self, client, server_app):
        server_app["config"].tts.voice = ""
        resp = client.post("/agent/tts/download", json={"voice": ""})
        assert resp.status_code == 400
        assert "voice is required" in resp.json()["error"]

    def test_tts_synthesize_missing_text(self, client):
        resp = client.post("/agent/tts/synthesize", json={})
        assert resp.status_code in (200, 400)

    def test_tts_ingest_catalog_valid(self, client):
        resp = client.post("/agent/tts/voices/catalog", json={
            "catalog": {"voice1": {"name": "Voice 1"}},
        })
        assert resp.status_code == 200


# =====================================================================
# Orchestrator (reaper thread)
# =====================================================================

class TestOrchestrator:
    def test_reap_stuck_timeout_zero(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 0
        mock_config.queue.poll_interval = 1
        mock_queue = MagicMock()
        orc = Orchestrator(mock_config, mock_queue)
        orc._reap_stuck()
        mock_queue.list.assert_not_called()

    def test_reap_stuck_requeues_task(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 60
        mock_config.queue.poll_interval = 1
        mock_queue = MagicMock()

        task = _make_task(
            kind="task",
            conversation="",
            started_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            retries=0,
            max_retries=3,
        )
        mock_queue.list.return_value = [task]

        orc = Orchestrator(mock_config, mock_queue)
        orc._reap_stuck()

        mock_queue.update.assert_called_once()
        call_kwargs = mock_queue.update.call_args[1]
        assert call_kwargs["status"] == TaskStatus.READY
        assert call_kwargs["retries"] == 1

    def test_reap_stuck_marks_failed(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 60
        mock_queue = MagicMock()

        task = _make_task(
            kind="task",
            conversation="",
            started_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            retries=3,
            max_retries=3,
        )
        mock_queue.list.return_value = [task]

        orc = Orchestrator(mock_config, mock_queue)
        orc._reap_stuck()

        call_kwargs = mock_queue.update.call_args[1]
        assert call_kwargs["status"] == TaskStatus.FAILED
        assert "timed out" in call_kwargs["error_log"]

    def test_reap_stuck_skips_converse_kind(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 60
        mock_queue = MagicMock()

        task = _make_task(kind="converse", conversation="")
        mock_queue.list.return_value = [task]

        orc = Orchestrator(mock_config, mock_queue)
        orc._reap_stuck()
        mock_queue.update.assert_not_called()

    def test_reap_stuck_skips_think_kind(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 60
        mock_queue = MagicMock()

        task = _make_task(kind="think", conversation="")
        mock_queue.list.return_value = [task]

        orc = Orchestrator(mock_config, mock_queue)
        orc._reap_stuck()
        mock_queue.update.assert_not_called()

    def test_reap_stuck_skips_with_conversation(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 60
        mock_queue = MagicMock()

        task = _make_task(kind="task", conversation="conv-1")
        mock_queue.list.return_value = [task]

        orc = Orchestrator(mock_config, mock_queue)
        orc._reap_stuck()
        mock_queue.update.assert_not_called()

    def test_reap_stuck_skips_no_started_at(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 60
        mock_queue = MagicMock()

        task = _make_task(kind="task", conversation="", started_at=None)
        mock_queue.list.return_value = [task]

        orc = Orchestrator(mock_config, mock_queue)
        orc._reap_stuck()
        mock_queue.update.assert_not_called()

    def test_reap_stuck_skips_not_timed_out(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 9999999
        mock_queue = MagicMock()

        task = _make_task(
            kind="task", conversation="",
            started_at=datetime.now(timezone.utc),
        )
        mock_queue.list.return_value = [task]

        orc = Orchestrator(mock_config, mock_queue)
        orc._reap_stuck()
        mock_queue.update.assert_not_called()

    def test_reap_stuck_naive_started_at(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 60
        mock_queue = MagicMock()

        task = _make_task(
            kind="task", conversation="",
            started_at=datetime(2020, 1, 1),
            retries=0, max_retries=3,
        )
        mock_queue.list.return_value = [task]

        orc = Orchestrator(mock_config, mock_queue)
        orc._reap_stuck()
        mock_queue.update.assert_called_once()

    def test_reap_stuck_emits_socketio(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 60
        mock_queue = MagicMock()

        task = _make_task(
            kind="task", conversation="",
            started_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            retries=0, max_retries=3,
        )
        mock_queue.list.return_value = [task]

        sio = MagicMock()
        orc = Orchestrator(mock_config, mock_queue, socketio_ref=[sio])
        orc._reap_stuck()
        sio.emit.assert_called_once_with("task_timeout", {
            "task_id": "t001",
            "retries": 0,
            "max_retries": 3,
        })

    def test_reap_stuck_no_socketio(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 60
        mock_queue = MagicMock()

        task = _make_task(
            kind="task", conversation="",
            started_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            retries=0, max_retries=3,
        )
        mock_queue.list.return_value = [task]

        orc = Orchestrator(mock_config, mock_queue, socketio_ref=[None])
        orc._reap_stuck()

    def test_reap_stuck_failed_appends_chat(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 60
        mock_queue = MagicMock()
        mock_chat = MagicMock()

        task = _make_task(
            kind="task", conversation="",
            started_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            retries=3, max_retries=3,
            spec_path="/some/path/conversation.json",
        )
        mock_queue.list.return_value = [task]

        orc = Orchestrator(mock_config, mock_queue, chat=mock_chat)
        orc._reap_stuck()
        mock_chat.append.assert_called_once()
        call_args = mock_chat.append.call_args
        assert call_args[0][0] == "path"
        assert "[Error]" in call_args[0][1]["content"]

    def test_reap_stuck_failed_no_spec_path(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = 60
        mock_queue = MagicMock()
        mock_chat = MagicMock()

        task = _make_task(
            kind="task", conversation="",
            started_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            retries=3, max_retries=3,
            spec_path="",
        )
        mock_queue.list.return_value = [task]

        orc = Orchestrator(mock_config, mock_queue, chat=mock_chat)
        orc._reap_stuck()
        mock_chat.append.assert_not_called()

    def test_reap_stuck_negative_timeout(self):
        from acai.orchestrator.server import Orchestrator
        mock_config = MagicMock()
        mock_config.queue.task_timeout = -1
        mock_queue = MagicMock()
        orc = Orchestrator(mock_config, mock_queue)
        orc._reap_stuck()
        mock_queue.list.assert_not_called()


# =====================================================================
# setup_socketio
# =====================================================================

class TestSetupSocketio:
    def test_setup_socketio_handlers(self):
        from acai.orchestrator.server import setup_socketio
        sio = MagicMock()
        mock_config = MagicMock()
        mock_config.active_provider.return_value = MagicMock(
            name="p", backend="b", endpoint="e",
        )
        mock_queue = MagicMock()
        mock_queue.list.return_value = []
        mock_events = MagicMock()
        mock_events.history = []

        setup_socketio(sio, mock_config, mock_queue, mock_events)
        assert sio.on.call_count >= 2

    def test_setup_socketio_with_load_balancer(self):
        from acai.orchestrator.server import setup_socketio
        sio = MagicMock()
        mock_config = MagicMock()
        mock_config.active_provider.return_value = MagicMock(
            name="p", backend="b", endpoint="e",
        )
        mock_queue = MagicMock()
        mock_queue.list.return_value = []
        mock_events = MagicMock()
        mock_events.history = []
        mock_lb = MagicMock()
        mock_lb.list_workers.return_value = []

        setup_socketio(sio, mock_config, mock_queue, mock_events, load_balancer=mock_lb)
        assert sio.on.call_count >= 3

    def test_setup_socketio_no_app_starts_bg_task(self):
        from acai.orchestrator.server import setup_socketio
        sio = MagicMock()
        mock_config = MagicMock()
        mock_config.active_provider.return_value = MagicMock(
            name="p", backend="b", endpoint="e",
        )
        mock_queue = MagicMock()
        mock_queue.list.return_value = []
        mock_events = MagicMock()
        mock_events.history = []

        setup_socketio(sio, mock_config, mock_queue, mock_events, app=None)
        sio.start_background_task.assert_called_once()

    def test_setup_socketio_with_app_registers_startup(self):
        from acai.orchestrator.server import setup_socketio
        sio = MagicMock()
        mock_config = MagicMock()
        mock_config.active_provider.return_value = MagicMock(
            name="p", backend="b", endpoint="e",
        )
        mock_queue = MagicMock()
        mock_queue.list.return_value = []
        mock_events = MagicMock()
        mock_events.history = []
        mock_app = MagicMock()

        setup_socketio(sio, mock_config, mock_queue, mock_events, app=mock_app)
        mock_app.on_event.assert_called_with("startup")
        sio.start_background_task.assert_not_called()
