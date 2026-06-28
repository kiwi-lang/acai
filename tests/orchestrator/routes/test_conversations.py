"""Unit tests for acai/orchestrator/routes/conversations.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acai.orchestrator.routes import RouterDeps
from acai.orchestrator.routes.conversations import create_conversations_router


@pytest.fixture
def mock_deps(tmp_path):
    deps = MagicMock(spec=RouterDeps)
    deps.config = MagicMock()
    deps.config.workspace = str(tmp_path)
    deps.config.get_provider.return_value = MagicMock(name="vllm", model_slug="llama3")
    deps.config.active_provider.return_value = MagicMock(name="vllm", model_slug="llama3", context_window=128000)
    deps.chat = MagicMock()
    deps.queue = MagicMock()
    deps.projects = MagicMock()
    deps.agent_store = MagicMock()
    deps.tool_registry = MagicMock()
    deps.tracker = MagicMock()
    deps.load_balancer = MagicMock()
    deps.workflows_dir = str(tmp_path / "workflows")
    deps.builtin_wf_dir = str(tmp_path / "builtin")
    return deps


@pytest.fixture
def mock_scheduler():
    scheduler = MagicMock()
    scheduler.default.return_value = MagicMock(context_window=128000)
    return scheduler


@pytest.fixture
def mock_audit():
    audit = MagicMock()
    audit.record = MagicMock()
    audit.finalize = MagicMock()
    audit.client_summary.return_value = {}
    return audit


@pytest.fixture
def client(mock_deps, mock_scheduler, mock_audit):
    app = FastAPI()
    router = create_conversations_router(
        mock_deps,
        scheduler=mock_scheduler,
        make_audit=MagicMock(return_value=mock_audit),
    )
    app.include_router(router)
    return TestClient(app)


class TestListConversations:
    def test_list_all(self, client, mock_deps):
        mock_deps.chat.list.return_value = [
            {"id": "conv-1", "title": "Hello"},
            {"id": "conv-2", "title": "World"},
        ]
        mock_deps.projects.get.return_value = None
        resp = client.get("/conversations")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_by_project(self, client, mock_deps):
        mock_deps.chat.list.return_value = [{"id": "c1", "project": "myproj"}]
        mock_deps.projects.get.return_value = None
        resp = client.get("/conversations?project=myproj")
        assert resp.status_code == 200
        mock_deps.chat.list.assert_called_with(project="myproj", task_id="")


class TestCreateConversation:
    def test_create(self, client, mock_deps):
        meta = MagicMock()
        meta.id = "new-conv"
        meta.to_dict.return_value = {"id": "new-conv", "title": "hi"}
        mock_deps.chat.create.return_value = meta
        mock_deps.projects.get.return_value = None

        resp = client.post("/conversations", json={
            "title": "hi",
            "project": "proj1",
            "agent": "coder",
        })
        assert resp.status_code == 201
        assert resp.json()["id"] == "new-conv"

    def test_create_with_default_agent(self, client, mock_deps):
        meta = MagicMock()
        meta.id = "c1"
        meta.to_dict.return_value = {"id": "c1"}
        mock_deps.chat.create.return_value = meta
        mock_deps.projects.get.return_value = MagicMock(refiner="specialist")

        resp = client.post("/conversations", json={"project": "myproj"})
        assert resp.status_code == 201
        mock_deps.chat.create.assert_called_once()
        call_kwargs = mock_deps.chat.create.call_args[1]
        assert call_kwargs["agent"] == "specialist"


class TestUpdateConversation:
    def test_update(self, client, mock_deps):
        mock_deps.chat.update_meta.return_value = {"id": "c1", "title": "updated"}
        mock_deps.projects.get.return_value = None

        resp = client.patch("/conversations/c1", json={"title": "updated"})
        assert resp.status_code == 200
        mock_deps.chat.update_meta.assert_called_once()

    def test_update_no_fields(self, client):
        resp = client.patch("/conversations/c1", json={"invalid_field": "x"})
        assert resp.status_code == 400

    def test_update_not_found(self, client, mock_deps):
        mock_deps.chat.update_meta.return_value = None
        resp = client.patch("/conversations/c1", json={"title": "x"})
        assert resp.status_code == 404

    def test_update_tags_string(self, client, mock_deps):
        mock_deps.chat.update_meta.return_value = {"id": "c1", "tags": ["a", "b"]}
        mock_deps.projects.get.return_value = None
        resp = client.patch("/conversations/c1", json={"tags": "a, b"})
        assert resp.status_code == 200
        call_kwargs = mock_deps.chat.update_meta.call_args[1]
        assert call_kwargs["tags"] == ["a", "b"]


class TestGetConversation:
    def test_found(self, client, mock_deps):
        mock_deps.chat.get_meta.return_value = {"id": "c1", "title": "Test"}
        mock_deps.projects.get.return_value = None
        resp = client.get("/conversations/c1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "c1"

    def test_not_found(self, client, mock_deps):
        mock_deps.chat.get_meta.return_value = None
        resp = client.get("/conversations/missing")
        assert resp.status_code == 404


class TestDeleteConversation:
    def test_delete(self, client, mock_deps):
        resp = client.delete("/conversations/c1")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        mock_deps.chat.delete.assert_called_once_with("c1")


class TestGetGraphs:
    def test_returns_graphs(self, client, tmp_path):
        with patch("acai.orchestrator.routes.conversations.list_graphs") as mock_lg:
            mock_lg.return_value = [
                {"kind": "converse", "label": "Chat", "description": "Basic chat"},
            ]
            resp = client.get("/graphs")
            assert resp.status_code == 200
            data = resp.json()
            assert any(g["kind"] == "converse" for g in data)

    def test_includes_workflow_graphs(self, client, mock_deps, tmp_path):
        wf_dir = tmp_path / "workflows" / "my-wf"
        wf_dir.mkdir(parents=True)
        (wf_dir / "definition.json").write_text(json.dumps({
            "name": "My Workflow",
            "description": "Custom",
        }))
        mock_deps.workflows_dir = str(tmp_path / "workflows")

        app = FastAPI()
        router = create_conversations_router(
            mock_deps,
            scheduler=MagicMock(default=MagicMock(return_value=None)),
            make_audit=MagicMock(),
        )
        app.include_router(router)
        cl = TestClient(app)

        with patch("acai.orchestrator.routes.conversations.list_graphs") as mock_lg:
            mock_lg.return_value = []
            resp = cl.get("/graphs")
            assert resp.status_code == 200
            data = resp.json()
            assert any("workflow:my-wf" == g["kind"] for g in data)


class TestConverse:
    def test_missing_message(self, client):
        resp = client.post("/converse", json={"conversation": "c1"})
        assert resp.status_code == 400
        assert "message is required" in resp.json()["error"]

    def test_workflow_not_found(self, client):
        resp = client.post("/converse", json={
            "message": "hi",
            "graph": "workflow:nonexistent",
        })
        assert resp.status_code == 404


class TestContextStats:
    def test_stats(self, client, mock_deps):
        mock_deps.chat.read.return_value = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi there!"},
        ]
        resp = client.get("/conversations/c1/context-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message_count"] == 2
        assert data["estimated_tokens"] > 0
        assert "max_context" in data


class TestInflight:
    def test_no_inflight(self, client, mock_deps):
        mock_deps.queue.list.return_value = []
        resp = client.get("/conversations/c1/inflight")
        assert resp.status_code == 200
        assert resp.json()["inflight"] is False

    def test_has_inflight(self, client, mock_deps):
        import os
        task = MagicMock()
        task.id = "task-1"
        task.status = "in_progress"
        task.spec_path = os.path.join("/data", "c1", "conversation.json")
        mock_deps.queue.list.side_effect = [
            [],  # PENDING
            [],  # READY
            [task],  # IN_PROGRESS
        ]
        resp = client.get("/conversations/c1/inflight")
        assert resp.status_code == 200
        assert resp.json()["inflight"] is True
        assert resp.json()["task_id"] == "task-1"

    def test_spec_path_not_conversation_json_skipped(self, client, mock_deps):
        """Tasks whose spec_path isn't conversation.json are ignored."""
        task = MagicMock()
        task.id = "task-2"
        task.spec_path = "/data/c1/other.json"
        mock_deps.queue.list.side_effect = [
            [task],  # PENDING
            [],
            [],
        ]
        resp = client.get("/conversations/c1/inflight")
        assert resp.json()["inflight"] is False

    def test_spec_path_none_skipped(self, client, mock_deps):
        """Tasks with no spec_path are ignored."""
        task = MagicMock()
        task.id = "task-3"
        task.spec_path = None
        mock_deps.queue.list.side_effect = [
            [task],
            [],
            [],
        ]
        resp = client.get("/conversations/c1/inflight")
        assert resp.json()["inflight"] is False

    def test_wrong_conv_id_skipped(self, client, mock_deps):
        """Task with conversation.json but for a different conv is ignored."""
        import os
        task = MagicMock()
        task.id = "task-4"
        task.spec_path = os.path.join("/data", "other-conv", "conversation.json")
        mock_deps.queue.list.side_effect = [
            [task],
            [],
            [],
        ]
        resp = client.get("/conversations/c1/inflight")
        assert resp.json()["inflight"] is False


# ---------------------------------------------------------------------------
# _json_body edge cases
# ---------------------------------------------------------------------------


class TestJsonBodyEdgeCases:
    """Malformed request bodies fall back to empty dict gracefully."""

    def test_create_with_malformed_json_uses_defaults(self, client, mock_deps):
        meta = MagicMock()
        meta.id = "c-fallback"
        meta.to_dict.return_value = {"id": "c-fallback"}
        mock_deps.chat.create.return_value = meta
        mock_deps.projects.get.return_value = None

        resp = client.post(
            "/conversations",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 201
        kw = mock_deps.chat.create.call_args[1]
        assert kw["title"] == ""
        assert kw["project"] == ""

    def test_update_with_malformed_json_returns_400(self, client):
        resp = client.patch(
            "/conversations/c1",
            content=b"{bad",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "no updatable fields"


# ---------------------------------------------------------------------------
# _enrich_conversation_dict
# ---------------------------------------------------------------------------


class TestEnrichConversation:
    """Cover the enrichment path that adds 'refiner' when a project is found."""

    def test_list_enriches_with_refiner(self, client, mock_deps):
        mock_deps.chat.list.return_value = [{"id": "c1", "project": "myproj"}]
        mock_deps.projects.get.return_value = MagicMock(refiner="specialist")

        resp = client.get("/conversations")
        assert resp.status_code == 200
        assert resp.json()[0]["refiner"] == "specialist"

    def test_enrich_falls_back_to_refiner_when_empty(self, client, mock_deps):
        mock_deps.chat.list.return_value = [{"id": "c1", "project": "myproj"}]
        mock_deps.projects.get.return_value = MagicMock(refiner="")

        resp = client.get("/conversations")
        assert resp.json()[0]["refiner"] == "refiner"

    def test_enrich_no_project_key_unchanged(self, client, mock_deps):
        mock_deps.chat.list.return_value = [{"id": "c1"}]
        mock_deps.projects.get.return_value = None

        resp = client.get("/conversations")
        assert "refiner" not in resp.json()[0]


# ---------------------------------------------------------------------------
# _default_agent_for_project
# ---------------------------------------------------------------------------


class TestDefaultAgentForProject:
    """Verify agent resolution: empty→'default', unknown→'refiner', etc."""

    def _create(self, client, project="", agent=""):
        payload = {"project": project}
        if agent:
            payload["agent"] = agent
        return client.post("/conversations", json=payload)

    def test_empty_project_returns_default(self, client, mock_deps):
        meta = MagicMock(id="c1")
        meta.to_dict.return_value = {"id": "c1"}
        mock_deps.chat.create.return_value = meta
        mock_deps.projects.get.return_value = None

        self._create(client, project="")
        kw = mock_deps.chat.create.call_args[1]
        assert kw["agent"] == "default"

    def test_whitespace_project_returns_default(self, client, mock_deps):
        meta = MagicMock(id="c1")
        meta.to_dict.return_value = {"id": "c1"}
        mock_deps.chat.create.return_value = meta
        mock_deps.projects.get.return_value = None

        self._create(client, project="   ")
        kw = mock_deps.chat.create.call_args[1]
        assert kw["agent"] == "default"

    def test_project_not_found_returns_refiner(self, client, mock_deps):
        meta = MagicMock(id="c1")
        meta.to_dict.return_value = {"id": "c1"}
        mock_deps.chat.create.return_value = meta
        mock_deps.projects.get.return_value = None

        self._create(client, project="unknown")
        kw = mock_deps.chat.create.call_args[1]
        assert kw["agent"] == "refiner"

    def test_project_with_empty_refiner_returns_refiner(self, client, mock_deps):
        meta = MagicMock(id="c1")
        meta.to_dict.return_value = {"id": "c1"}
        mock_deps.chat.create.return_value = meta
        mock_deps.projects.get.return_value = MagicMock(refiner="")

        self._create(client, project="myproj")
        kw = mock_deps.chat.create.call_args[1]
        assert kw["agent"] == "refiner"

    def test_project_with_none_refiner_returns_refiner(self, client, mock_deps):
        meta = MagicMock(id="c1")
        meta.to_dict.return_value = {"id": "c1"}
        mock_deps.chat.create.return_value = meta
        mock_deps.projects.get.return_value = MagicMock(refiner=None)

        self._create(client, project="myproj")
        kw = mock_deps.chat.create.call_args[1]
        assert kw["agent"] == "refiner"


# ---------------------------------------------------------------------------
# get_graphs edge cases
# ---------------------------------------------------------------------------


class TestGetGraphsEdgeCases:
    def _make_client(self, mock_deps):
        app = FastAPI()
        router = create_conversations_router(
            mock_deps,
            scheduler=MagicMock(default=MagicMock(return_value=None)),
            make_audit=MagicMock(),
        )
        app.include_router(router)
        return TestClient(app)

    def test_skips_entry_without_definition_json(self, mock_deps, tmp_path):
        (tmp_path / "workflows" / "no-def").mkdir(parents=True)
        mock_deps.workflows_dir = str(tmp_path / "workflows")
        mock_deps.builtin_wf_dir = str(tmp_path / "builtin")

        with patch("acai.orchestrator.routes.conversations.list_graphs", return_value=[]):
            resp = self._make_client(mock_deps).get("/graphs")
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    def test_skips_duplicate_workflow(self, mock_deps, tmp_path):
        wf_dir = tmp_path / "workflows" / "dup-wf"
        wf_dir.mkdir(parents=True)
        (wf_dir / "definition.json").write_text(json.dumps({"name": "Dup"}))
        mock_deps.workflows_dir = str(tmp_path / "workflows")
        mock_deps.builtin_wf_dir = str(tmp_path / "builtin")

        existing = [{"kind": "workflow:dup-wf", "label": "Already", "description": "x"}]
        with patch("acai.orchestrator.routes.conversations.list_graphs", return_value=existing):
            resp = self._make_client(mock_deps).get("/graphs")
        data = resp.json()
        assert sum(1 for g in data if g["kind"] == "workflow:dup-wf") == 1

    def test_malformed_definition_json_skipped_gracefully(self, mock_deps, tmp_path):
        wf_dir = tmp_path / "workflows" / "bad-wf"
        wf_dir.mkdir(parents=True)
        (wf_dir / "definition.json").write_text("not valid json {{{")
        mock_deps.workflows_dir = str(tmp_path / "workflows")
        mock_deps.builtin_wf_dir = str(tmp_path / "builtin")

        with patch("acai.orchestrator.routes.conversations.list_graphs", return_value=[]):
            resp = self._make_client(mock_deps).get("/graphs")
        assert resp.status_code == 200
        assert len(resp.json()) == 0


# ---------------------------------------------------------------------------
# /converse — streaming, error handling, and edge cases
# ---------------------------------------------------------------------------


def _parse_sse(content: str) -> list[dict]:
    """Parse SSE text into a list of {event, data} dicts."""
    events = []
    for block in content.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_type = "message"
        data = {}
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        events.append({"event": event_type, "data": data})
    return events


def _streaming_client(mock_deps, mock_audit):
    """Build a TestClient wired up with the given mocks."""
    app = FastAPI()
    router = create_conversations_router(
        mock_deps,
        scheduler=MagicMock(default=MagicMock(return_value=None)),
        make_audit=MagicMock(return_value=mock_audit),
    )
    app.include_router(router)
    return TestClient(app)


def _setup_successful_stream(mock_deps, events=None):
    """Wire load_balancer and get_graph for a successful stream."""
    if events is None:
        events = [{"event_type": "message", "data": {"text": "hi"}}]

    async def mock_run(work):
        for e in events:
            yield e

    mock_graph = MagicMock()
    mock_graph.run = mock_run

    mock_worker = MagicMock()
    mock_worker.url = "http://worker:8000"
    mock_acm = AsyncMock()
    mock_acm.__aenter__.return_value = mock_worker
    mock_acm.__aexit__.return_value = False
    mock_deps.load_balancer.acquire.return_value = mock_acm

    return mock_graph


class TestConverseStreaming:
    """Test the SSE stream: success, timeouts, exceptions, audit events."""

    def test_successful_stream_yields_meta_and_events(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")
        mock_graph = _setup_successful_stream(mock_deps)

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            resp = cl.post("/converse", json={"message": "hello"})

        events = _parse_sse(resp.text)
        meta = [e for e in events if e["event"] == "meta"]
        msgs = [e for e in events if e["event"] == "message"]
        assert len(meta) == 1
        assert meta[0]["data"]["conversation"] == "c1"
        assert len(msgs) == 1
        assert msgs[0]["data"]["text"] == "hi"
        assert resp.headers["X-Conversation"] == "c1"

    def test_timeout_error_yields_clear_client_message(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.side_effect = TimeoutError("no workers")
        mock_deps.load_balancer.acquire.return_value = mock_acm

        cl = _streaming_client(mock_deps, mock_audit)
        resp = cl.post("/converse", json={"message": "hello"})

        events = _parse_sse(resp.text)
        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "timeout" in errors[0]["data"]["message"].lower()
        assert "worker" in errors[0]["data"]["message"].lower()
        mock_audit.record.assert_any_call("error", phase="server", error="worker timeout")
        mock_audit.finalize.assert_called_once()

    def test_general_exception_includes_type_and_traceback(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.side_effect = RuntimeError("something broke")
        mock_deps.load_balancer.acquire.return_value = mock_acm

        cl = _streaming_client(mock_deps, mock_audit)
        resp = cl.post("/converse", json={"message": "hello"})

        events = _parse_sse(resp.text)
        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "RuntimeError" in errors[0]["data"]["message"]
        assert "something broke" in errors[0]["data"]["message"]
        assert "traceback" in errors[0]["data"]
        assert len(errors[0]["data"]["traceback"]) > 0
        mock_audit.finalize.assert_called_once()

    def test_audit_complete_emitted_when_request_id_present(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")
        mock_audit.client_summary.return_value = {"request_id": "req-42", "dur": 1.0}
        mock_graph = _setup_successful_stream(mock_deps)

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            resp = cl.post("/converse", json={"message": "hello"})

        events = _parse_sse(resp.text)
        audit_evts = [e for e in events if e["event"] == "audit_complete"]
        assert len(audit_evts) == 1
        assert audit_evts[0]["data"]["request_id"] == "req-42"

    def test_no_audit_complete_without_request_id(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")
        mock_audit.client_summary.return_value = {}
        mock_graph = _setup_successful_stream(mock_deps)

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            resp = cl.post("/converse", json={"message": "hello"})

        events = _parse_sse(resp.text)
        assert not any(e["event"] == "audit_complete" for e in events)

    def test_exception_during_graph_run(self, mock_deps, mock_audit):
        """Error raised mid-stream still yields an error SSE event."""
        mock_deps.chat.create.return_value = MagicMock(id="c1")

        async def exploding_run(work):
            yield {"event_type": "message", "data": {"text": "partial"}}
            raise ConnectionError("lost backend")

        mock_graph = MagicMock()
        mock_graph.run = exploding_run

        mock_worker = MagicMock(url="http://w:8000")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.return_value = mock_worker
        mock_acm.__aexit__.return_value = False
        mock_deps.load_balancer.acquire.return_value = mock_acm

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            resp = cl.post("/converse", json={"message": "hello"})

        events = _parse_sse(resp.text)
        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "ConnectionError" in errors[0]["data"]["message"]
        assert "lost backend" in errors[0]["data"]["message"]


class TestConverseEdgeCases:
    """Non-streaming branches: ephemeral, provider override, task context."""

    def test_ephemeral_generates_conversation_id(self, mock_deps, mock_audit):
        mock_graph = _setup_successful_stream(mock_deps)

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            resp = cl.post("/converse", json={
                "message": "hello",
                "ephemeral": True,
            })

        events = _parse_sse(resp.text)
        meta = [e for e in events if e["event"] == "meta"][0]
        assert meta["data"]["conversation"].startswith("ephemeral-")
        mock_deps.chat.create.assert_not_called()

    def test_ephemeral_with_existing_conversation(self, mock_deps, mock_audit):
        mock_graph = _setup_successful_stream(mock_deps)

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            resp = cl.post("/converse", json={
                "message": "hello",
                "ephemeral": True,
                "conversation": "my-eph-conv",
            })

        events = _parse_sse(resp.text)
        meta = [e for e in events if e["event"] == "meta"][0]
        assert meta["data"]["conversation"] == "my-eph-conv"

    def test_auto_creates_conversation_when_not_provided(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="auto-c1")
        mock_graph = _setup_successful_stream(mock_deps)

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            resp = cl.post("/converse", json={"message": "hello"})

        mock_deps.chat.create.assert_called_once()
        events = _parse_sse(resp.text)
        meta = [e for e in events if e["event"] == "meta"][0]
        assert meta["data"]["conversation"] == "auto-c1"

    def test_existing_conversation_reused(self, mock_deps, mock_audit):
        mock_graph = _setup_successful_stream(mock_deps)

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            resp = cl.post("/converse", json={
                "message": "hello",
                "conversation": "existing-conv",
            })

        mock_deps.chat.create.assert_not_called()
        events = _parse_sse(resp.text)
        meta = [e for e in events if e["event"] == "meta"][0]
        assert meta["data"]["conversation"] == "existing-conv"

    def test_provider_override_set_for_named_provider(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")
        prov = MagicMock()
        prov.name = "openai"
        prov.model_slug = "gpt-4"
        mock_deps.config.get_provider.return_value = prov

        captured_work = {}

        async def capture_run(work):
            captured_work.update(work)
            yield {"event_type": "message", "data": {}}

        mock_graph = MagicMock()
        mock_graph.run = capture_run

        mock_worker = MagicMock(url="http://w:8000")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.return_value = mock_worker
        mock_acm.__aexit__.return_value = False
        mock_deps.load_balancer.acquire.return_value = mock_acm

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            cl.post("/converse", json={
                "message": "hello",
                "provider": "openai",
                "model": "gpt-4-turbo",
            })

        assert captured_work["provider_override"]["name"] == "openai"
        assert captured_work["provider_override"]["model"] == "gpt-4-turbo"

    def test_provider_auto_no_override(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")

        captured_work = {}

        async def capture_run(work):
            captured_work.update(work)
            yield {"event_type": "message", "data": {}}

        mock_graph = MagicMock()
        mock_graph.run = capture_run

        mock_worker = MagicMock(url="http://w:8000")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.return_value = mock_worker
        mock_acm.__aexit__.return_value = False
        mock_deps.load_balancer.acquire.return_value = mock_acm

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            cl.post("/converse", json={"message": "hello", "provider": "auto"})

        assert captured_work["provider_override"] is None

    def test_provider_not_found_no_override(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")
        mock_deps.config.get_provider.return_value = None

        captured_work = {}

        async def capture_run(work):
            captured_work.update(work)
            yield {"event_type": "message", "data": {}}

        mock_graph = MagicMock()
        mock_graph.run = capture_run

        mock_worker = MagicMock(url="http://w:8000")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.return_value = mock_worker
        mock_acm.__aexit__.return_value = False
        mock_deps.load_balancer.acquire.return_value = mock_acm

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            cl.post("/converse", json={"message": "hello", "provider": "nonexistent"})

        assert captured_work["provider_override"] is None

    def test_task_context_enrichment(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")
        task_obj = MagicMock()
        task_obj.id = "task-42"
        task_obj.title = "Fix bug"
        task_obj.description = "It's broken"
        task_obj.kind = "bugfix"
        task_obj.status = "in_progress"
        task_obj.priority = "high"
        task_obj.agent = "coder"
        mock_deps.queue.get.return_value = task_obj

        captured_work = {}

        async def capture_run(work):
            captured_work.update(work)
            yield {"event_type": "message", "data": {}}

        mock_graph = MagicMock()
        mock_graph.run = capture_run

        mock_worker = MagicMock(url="http://w:8000")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.return_value = mock_worker
        mock_acm.__aexit__.return_value = False
        mock_deps.load_balancer.acquire.return_value = mock_acm

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            cl.post("/converse", json={"message": "hello", "task_id": "task-42"})

        ctx = captured_work["extra_context"]["current_task"]
        assert ctx["id"] == "task-42"
        assert ctx["title"] == "Fix bug"
        assert ctx["status"] == "in_progress"

    def test_non_dict_context_ignored(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")
        mock_deps.queue.get.return_value = None

        captured_work = {}

        async def capture_run(work):
            captured_work.update(work)
            yield {"event_type": "message", "data": {}}

        mock_graph = MagicMock()
        mock_graph.run = capture_run

        mock_worker = MagicMock(url="http://w:8000")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.return_value = mock_worker
        mock_acm.__aexit__.return_value = False
        mock_deps.load_balancer.acquire.return_value = mock_acm

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            cl.post("/converse", json={
                "message": "hello",
                "context": "not-a-dict",
            })

        assert "extra_context" not in captured_work

    def test_dict_context_passed_through(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")
        mock_deps.queue.get.return_value = None

        captured_work = {}

        async def capture_run(work):
            captured_work.update(work)
            yield {"event_type": "message", "data": {}}

        mock_graph = MagicMock()
        mock_graph.run = capture_run

        mock_worker = MagicMock(url="http://w:8000")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.return_value = mock_worker
        mock_acm.__aexit__.return_value = False
        mock_deps.load_balancer.acquire.return_value = mock_acm

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            cl.post("/converse", json={
                "message": "hello",
                "context": {"key": "value"},
            })

        assert captured_work["extra_context"] == {"key": "value"}

    def test_enable_thinking_passed_through(self, mock_deps, mock_audit):
        mock_deps.chat.create.return_value = MagicMock(id="c1")

        captured_work = {}

        async def capture_run(work):
            captured_work.update(work)
            yield {"event_type": "message", "data": {}}

        mock_graph = MagicMock()
        mock_graph.run = capture_run

        mock_worker = MagicMock(url="http://w:8000")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.return_value = mock_worker
        mock_acm.__aexit__.return_value = False
        mock_deps.load_balancer.acquire.return_value = mock_acm

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            cl.post("/converse", json={
                "message": "hello",
                "enable_thinking": True,
            })

        assert captured_work["enable_thinking"] is True


# ---------------------------------------------------------------------------
# /converse — workflow from builtin directory
# ---------------------------------------------------------------------------


class TestConverseWorkflow:
    """Verify workflow resolution falls back to builtin_wf_dir."""

    def test_workflow_found_in_builtin_dir(self, mock_deps, mock_audit, tmp_path):
        builtin_dir = tmp_path / "builtin" / "my-wf"
        builtin_dir.mkdir(parents=True)
        (builtin_dir / "definition.json").write_text(json.dumps({
            "name": "Builtin WF",
            "steps": [],
        }))
        mock_deps.workflows_dir = str(tmp_path / "workflows")
        mock_deps.builtin_wf_dir = str(tmp_path / "builtin")

        captured_work = {}

        async def capture_run(work):
            captured_work.update(work)
            yield {"event_type": "message", "data": {}}

        mock_graph = MagicMock()
        mock_graph.run = capture_run

        mock_worker = MagicMock(url="http://w:8000")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.return_value = mock_worker
        mock_acm.__aexit__.return_value = False
        mock_deps.load_balancer.acquire.return_value = mock_acm
        mock_deps.chat.create.return_value = MagicMock(id="c1")

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            resp = cl.post("/converse", json={
                "message": "hello",
                "graph": "workflow:my-wf",
            })

        assert resp.status_code == 200
        assert captured_work["workflow_spec"]["name"] == "Builtin WF"
        assert captured_work["workflow_dir"] == str(builtin_dir)

    def test_workflow_found_in_user_dir(self, mock_deps, mock_audit, tmp_path):
        user_dir = tmp_path / "workflows" / "user-wf"
        user_dir.mkdir(parents=True)
        (user_dir / "definition.json").write_text(json.dumps({
            "name": "User WF",
            "steps": [],
        }))
        mock_deps.workflows_dir = str(tmp_path / "workflows")
        mock_deps.builtin_wf_dir = str(tmp_path / "builtin")

        captured_work = {}

        async def capture_run(work):
            captured_work.update(work)
            yield {"event_type": "message", "data": {}}

        mock_graph = MagicMock()
        mock_graph.run = capture_run

        mock_worker = MagicMock(url="http://w:8000")
        mock_acm = AsyncMock()
        mock_acm.__aenter__.return_value = mock_worker
        mock_acm.__aexit__.return_value = False
        mock_deps.load_balancer.acquire.return_value = mock_acm
        mock_deps.chat.create.return_value = MagicMock(id="c1")

        with patch("acai.orchestrator.routes.conversations.get_graph", return_value=mock_graph):
            cl = _streaming_client(mock_deps, mock_audit)
            resp = cl.post("/converse", json={
                "message": "hello",
                "graph": "workflow:user-wf",
            })

        assert resp.status_code == 200
        assert captured_work["workflow_spec"]["name"] == "User WF"


# ---------------------------------------------------------------------------
# /conversations/{conv_id}/context-stats edge case
# ---------------------------------------------------------------------------


class TestContextStatsEdgeCases:
    def test_scheduler_default_none_uses_active_provider(self, mock_deps, tmp_path):
        """When scheduler.default() returns None, active_provider is used."""
        mock_deps.chat.read.return_value = [{"role": "user", "content": "hi"}]
        scheduler = MagicMock()
        scheduler.default.return_value = None
        mock_deps.config.active_provider.return_value = MagicMock(context_window=32000)

        app = FastAPI()
        router = create_conversations_router(
            mock_deps,
            scheduler=scheduler,
            make_audit=MagicMock(),
        )
        app.include_router(router)
        cl = TestClient(app)

        resp = cl.get("/conversations/c1/context-stats")
        assert resp.status_code == 200
        assert resp.json()["max_context"] == 32000

    def test_empty_conversation_zero_tokens(self, client, mock_deps):
        mock_deps.chat.read.return_value = []
        resp = client.get("/conversations/c1/context-stats")
        assert resp.status_code == 200
        assert resp.json()["estimated_tokens"] == 0
        assert resp.json()["message_count"] == 0
