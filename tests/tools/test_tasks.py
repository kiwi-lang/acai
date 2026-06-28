"""Unit tests for acai/tools/tasks.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from acai.tools.tasks import (
    Task,
    _require_client,
    create,
    get,
    list_tasks,
    mark_ready,
    update,
)

import pytest

_CTX = "acai.tools.tasks.current_context"
_CLIENT = "acai.tools.tasks.current_client"


class TestTask:
    def test_dataclass_fields(self):
        t = Task(title="root", context="ctx", subtasks=[])
        assert t.title == "root"
        assert t.context == "ctx"
        assert t.subtasks == []

    def test_nested_subtasks(self):
        child = Task(title="child", context="c", subtasks=[])
        parent = Task(title="parent", context="p", subtasks=[child])
        assert len(parent.subtasks) == 1
        assert parent.subtasks[0].title == "child"


class TestRequireClient:
    def test_returns_client_when_present(self):
        mock_client = MagicMock()
        with patch(_CLIENT, return_value=mock_client):
            assert _require_client() is mock_client

    def test_raises_when_no_client(self):
        with patch(_CLIENT, return_value=None):
            with pytest.raises(RuntimeError, match="no orchestrator client"):
                _require_client()


class TestCreate:
    def test_basic_create(self):
        mock_client = MagicMock()
        mock_client.post.return_value = {"id": "t-1", "title": "do stuff"}
        mock_ctx = MagicMock()
        mock_ctx.project = "myproj"

        with patch(_CLIENT, return_value=mock_client), \
             patch(_CTX, return_value=mock_ctx):
            result = json.loads(create(title="do stuff"))

        assert result == {"id": "t-1", "title": "do stuff"}
        mock_client.post.assert_called_once_with("/tasks", {
            "title": "do stuff",
            "description": "",
            "project": "myproj",
            "priority": 0,
            "agent": "",
            "kind": "task",
        })

    def test_explicit_project_overrides_context(self):
        mock_client = MagicMock()
        mock_client.post.return_value = {"id": "t-2"}
        mock_ctx = MagicMock()
        mock_ctx.project = "default-proj"

        with patch(_CLIENT, return_value=mock_client), \
             patch(_CTX, return_value=mock_ctx):
            json.loads(create(title="t", project="explicit"))

        call_payload = mock_client.post.call_args[0][1]
        assert call_payload["project"] == "explicit"

    def test_no_context_uses_empty_project(self):
        mock_client = MagicMock()
        mock_client.post.return_value = {"id": "t-3"}

        with patch(_CLIENT, return_value=mock_client), \
             patch(_CTX, return_value=None):
            json.loads(create(title="t"))

        call_payload = mock_client.post.call_args[0][1]
        assert call_payload["project"] == ""

    def test_all_params_forwarded(self):
        mock_client = MagicMock()
        mock_client.post.return_value = {"id": "t-4"}

        with patch(_CLIENT, return_value=mock_client), \
             patch(_CTX, return_value=None):
            json.loads(create(
                title="big task",
                description="details",
                project="proj",
                priority=5,
                agent="coder",
                kind="work",
            ))

        call_payload = mock_client.post.call_args[0][1]
        assert call_payload == {
            "title": "big task",
            "description": "details",
            "project": "proj",
            "priority": 5,
            "agent": "coder",
            "kind": "work",
        }

    def test_error_returns_json(self):
        with patch(_CLIENT, side_effect=RuntimeError("boom")):
            result = json.loads(create(title="fail"))

        assert "error" in result
        assert "boom" in result["error"]

    def test_client_exception_caught(self):
        mock_client = MagicMock()
        mock_client.post.side_effect = ConnectionError("offline")

        with patch(_CLIENT, return_value=mock_client), \
             patch(_CTX, return_value=None):
            result = json.loads(create(title="t"))

        assert "error" in result
        assert "offline" in result["error"]


class TestUpdate:
    def test_update_title(self):
        mock_client = MagicMock()
        mock_client.patch.return_value = {"ok": True}

        with patch(_CLIENT, return_value=mock_client):
            result = json.loads(update(task_id="t-1", title="new title"))

        assert result == {"ok": True}
        mock_client.patch.assert_called_once_with(
            "/tasks/t-1", {"title": "new title"}
        )

    def test_update_multiple_fields(self):
        mock_client = MagicMock()
        mock_client.patch.return_value = {"ok": True}

        with patch(_CLIENT, return_value=mock_client):
            json.loads(update(
                task_id="t-2",
                title="t",
                description="d",
                status="completed",
                priority=3,
                agent="coder",
            ))

        call_fields = mock_client.patch.call_args[0][1]
        assert call_fields == {
            "title": "t",
            "description": "d",
            "status": "completed",
            "priority": 3,
            "agent": "coder",
        }

    def test_no_fields_returns_error(self):
        result = json.loads(update(task_id="t-1"))
        assert result == {"error": "no fields to update"}

    def test_priority_zero_is_included(self):
        mock_client = MagicMock()
        mock_client.patch.return_value = {"ok": True}

        with patch(_CLIENT, return_value=mock_client):
            json.loads(update(task_id="t-1", priority=0))

        call_fields = mock_client.patch.call_args[0][1]
        assert "priority" in call_fields
        assert call_fields["priority"] == 0

    def test_negative_priority_excluded(self):
        mock_client = MagicMock()
        mock_client.patch.return_value = {"ok": True}

        with patch(_CLIENT, return_value=mock_client):
            json.loads(update(task_id="t-1", title="x", priority=-1))

        call_fields = mock_client.patch.call_args[0][1]
        assert "priority" not in call_fields

    def test_error_returns_json(self):
        mock_client = MagicMock()
        mock_client.patch.side_effect = Exception("server error")

        with patch(_CLIENT, return_value=mock_client):
            result = json.loads(update(task_id="t-1", title="x"))

        assert "error" in result
        assert "server error" in result["error"]

    def test_no_client_returns_error(self):
        with patch(_CLIENT, return_value=None):
            result = json.loads(update(task_id="t-1", status="ready"))

        assert "error" in result
        assert "no orchestrator client" in result["error"]


class TestListTasks:
    def test_list_all(self):
        mock_client = MagicMock()
        mock_client.get.return_value = [{"id": "t-1"}, {"id": "t-2"}]

        with patch(_CLIENT, return_value=mock_client):
            result = json.loads(list_tasks())

        assert len(result) == 2
        mock_client.get.assert_called_once_with("/tasks", {})

    def test_filter_by_project(self):
        mock_client = MagicMock()
        mock_client.get.return_value = [{"id": "t-1"}]

        with patch(_CLIENT, return_value=mock_client):
            json.loads(list_tasks(project="myproj"))

        mock_client.get.assert_called_once_with(
            "/tasks", {"project": "myproj"}
        )

    def test_filter_by_status(self):
        mock_client = MagicMock()
        mock_client.get.return_value = []

        with patch(_CLIENT, return_value=mock_client):
            json.loads(list_tasks(status="pending"))

        mock_client.get.assert_called_once_with(
            "/tasks", {"status": "pending"}
        )

    def test_filter_both(self):
        mock_client = MagicMock()
        mock_client.get.return_value = []

        with patch(_CLIENT, return_value=mock_client):
            json.loads(list_tasks(project="p", status="ready"))

        mock_client.get.assert_called_once_with(
            "/tasks", {"project": "p", "status": "ready"}
        )

    def test_error_returns_json(self):
        with patch(_CLIENT, return_value=None):
            result = json.loads(list_tasks())

        assert "error" in result

    def test_client_exception(self):
        mock_client = MagicMock()
        mock_client.get.side_effect = TimeoutError("slow")

        with patch(_CLIENT, return_value=mock_client):
            result = json.loads(list_tasks())

        assert "error" in result
        assert "slow" in result["error"]


class TestGet:
    def test_get_task(self):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "id": "t-42",
            "title": "important",
            "status": "pending",
        }

        with patch(_CLIENT, return_value=mock_client):
            result = json.loads(get(task_id="t-42"))

        assert result["id"] == "t-42"
        assert result["title"] == "important"
        mock_client.get.assert_called_once_with("/tasks/t-42")

    def test_error_returns_json(self):
        with patch(_CLIENT, return_value=None):
            result = json.loads(get(task_id="t-99"))

        assert "error" in result

    def test_client_exception(self):
        mock_client = MagicMock()
        mock_client.get.side_effect = ValueError("bad id")

        with patch(_CLIENT, return_value=mock_client):
            result = json.loads(get(task_id="bad"))

        assert "error" in result
        assert "bad id" in result["error"]


class TestMarkReady:
    def test_mark_ready_success(self):
        mock_client = MagicMock()
        mock_client.patch.return_value = {"id": "t-5", "status": "ready"}

        with patch(_CLIENT, return_value=mock_client):
            result = json.loads(mark_ready(task_id="t-5"))

        assert result["status"] == "ready"
        mock_client.patch.assert_called_once_with(
            "/tasks/t-5", {"status": "ready"}
        )

    def test_error_returns_json(self):
        with patch(_CLIENT, return_value=None):
            result = json.loads(mark_ready(task_id="t-1"))

        assert "error" in result

    def test_client_exception(self):
        mock_client = MagicMock()
        mock_client.patch.side_effect = ConnectionError("down")

        with patch(_CLIENT, return_value=mock_client):
            result = json.loads(mark_ready(task_id="t-1"))

        assert "error" in result
        assert "down" in result["error"]
