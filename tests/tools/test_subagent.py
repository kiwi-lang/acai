"""Tests for acai.tools.subagent — subagent tool definitions and constants."""

from __future__ import annotations

import json

from acai.tools.subagent import (
    SUBAGENT_TOOLS,
    spawn_agent,
    spawn_agent_async,
    await_task,
    check_task,
    run_task,
)


class TestConstants:
    def test_subagent_tools_set(self):
        assert "subagent_spawn_agent" in SUBAGENT_TOOLS

    def test_set_is_frozenset(self):
        assert isinstance(SUBAGENT_TOOLS, frozenset)


class TestToolBodies:
    """These are marker bodies — never actually executed during agent runs."""

    def test_spawn_agent_returns_error(self):
        result = json.loads(spawn_agent("helper", "do thing"))
        assert "error" in result

    def test_spawn_agent_async_returns_error(self):
        result = json.loads(spawn_agent_async("helper", "do thing"))
        assert "error" in result

    def test_await_task_returns_error(self):
        result = json.loads(await_task("task-123"))
        assert "error" in result

    def test_check_task_returns_error(self):
        result = json.loads(check_task("task-123"))
        assert "error" in result

    def test_run_task_returns_error(self):
        result = json.loads(run_task("index_knowledge"))
        assert "error" in result


class TestToolSignatures:
    def test_spawn_agent_params(self):
        import inspect
        sig = inspect.signature(spawn_agent)
        params = list(sig.parameters.keys())
        assert "agent" in params
        assert "message" in params
        assert "context" in params
        assert "max_iterations" in params

    def test_spawn_agent_async_params(self):
        import inspect
        sig = inspect.signature(spawn_agent_async)
        params = list(sig.parameters.keys())
        assert "agent" in params
        assert "message" in params

    def test_await_task_params(self):
        import inspect
        sig = inspect.signature(await_task)
        params = list(sig.parameters.keys())
        assert "task_id" in params
        assert "timeout" in params

    def test_check_task_params(self):
        import inspect
        sig = inspect.signature(check_task)
        assert "task_id" in list(inspect.signature(check_task).parameters.keys())

    def test_run_task_params(self):
        import inspect
        sig = inspect.signature(run_task)
        params = list(sig.parameters.keys())
        assert "name" in params
        assert "params" in params


class TestToolMetadata:
    def test_spawn_agent_metadata(self):
        meta = getattr(spawn_agent, "_tool_meta", {})
        assert "execute" in meta.get("permissions", ())
        assert "agents:run" in meta.get("resources", ())

    def test_await_task_metadata(self):
        meta = getattr(await_task, "_tool_meta", {})
        assert "read" in meta.get("permissions", ())

    def test_run_task_metadata(self):
        meta = getattr(run_task, "_tool_meta", {})
        assert "execute" in meta.get("permissions", ())
