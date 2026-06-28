"""Unit tests for acai/tools/session.py."""

from __future__ import annotations

import json
import os

from acai.tools.session import todo_write, todo_read


class TestTodoWrite:
    def test_write_valid_todos(self, tmp_path):
        todos = json.dumps([
            {"content": "Write tests", "status": "pending"},
            {"content": "Fix bug", "status": "completed"},
        ])
        result = json.loads(todo_write(str(tmp_path), todos))
        assert result["ok"] is True
        assert result["count"] == 2
        assert os.path.isfile(result["path"])

        with open(result["path"]) as f:
            data = json.load(f)
        assert len(data) == 2
        assert data[0]["content"] == "Write tests"

    def test_write_custom_filename(self, tmp_path):
        todos = json.dumps([{"task": "hello"}])
        result = json.loads(todo_write(str(tmp_path), todos, filename="custom.json"))
        assert result["ok"] is True
        assert "custom.json" in result["path"]

    def test_write_empty_list(self, tmp_path):
        result = json.loads(todo_write(str(tmp_path), "[]"))
        assert result["ok"] is True
        assert result["count"] == 0

    def test_invalid_json(self, tmp_path):
        result = json.loads(todo_write(str(tmp_path), "not json"))
        assert "error" in result
        assert "invalid JSON" in result["error"]

    def test_not_a_list(self, tmp_path):
        result = json.loads(todo_write(str(tmp_path), '{"key": "value"}'))
        assert "error" in result
        assert "must be a JSON array" in result["error"]

    def test_creates_parent_directory(self, tmp_path):
        deep_dir = tmp_path / "deep" / "nested"
        todos = json.dumps([{"task": "test"}])
        result = json.loads(todo_write(str(deep_dir), todos))
        assert result["ok"] is True


class TestTodoRead:
    def test_read_existing(self, tmp_path):
        path = tmp_path / ".acai-session-todos.json"
        path.write_text(json.dumps([{"task": "read me"}]))

        result = json.loads(todo_read(str(tmp_path)))
        assert result["exists"] is True
        assert len(result["todos"]) == 1
        assert result["todos"][0]["task"] == "read me"

    def test_read_nonexistent(self, tmp_path):
        result = json.loads(todo_read(str(tmp_path)))
        assert result["exists"] is False
        assert result["todos"] == []

    def test_read_custom_filename(self, tmp_path):
        path = tmp_path / "my-todos.json"
        path.write_text(json.dumps([{"x": 1}]))

        result = json.loads(todo_read(str(tmp_path), filename="my-todos.json"))
        assert result["exists"] is True
        assert result["todos"] == [{"x": 1}]

    def test_default_filename(self, tmp_path):
        result = json.loads(todo_read(str(tmp_path)))
        assert ".acai-session-todos.json" in result["path"]
