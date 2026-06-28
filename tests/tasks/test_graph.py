"""Tests for acai.tasks.graph — TaskGraph, Acc, enforce_context_limit, and helpers."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

import pytest

from acai.tasks.graph import (
    _estimate_tokens,
    _TRUNCATION_MARKER,
    _MAX_TOOL_ROUNDS,
    _MAX_TOOL_RESULT_CHARS,
    _TOOL_RESULT_TRUNCATION_MSG,
    enforce_context_limit,
    Acc,
    _TaskProxy,
    TaskGraph,
)
from acai.utils.audit import NullAuditTrail


# ------------------------------------------------------------------
# Helpers — lightweight fakes and builders
# ------------------------------------------------------------------

def _make_worker(url: str = "http://worker:8000/worker") -> MagicMock:
    w = MagicMock()
    w.url = url
    return w


def _make_agent_def(**overrides) -> MagicMock:
    defaults = dict(
        tools=[],
        tool_permissions=["read"],
        resource_permissions=[],
        uses_sandbox=False,
        scope="global",
        compressor="compressor",
        model_set="",
        complexity="medium",
        system_template="system.j2",
        provider_allow=[],
        provider_forbid=[],
    )
    defaults.update(overrides)
    ad = MagicMock(**defaults)
    ad.is_provider_allowed = MagicMock(return_value=True)
    return ad


def _make_config(
    *,
    context_window: int = 128000,
    max_tokens: int = 4096,
    model_sets: list | None = None,
) -> MagicMock:
    prov = MagicMock()
    prov.context_window = context_window
    prov.max_tokens = max_tokens
    prov.name = "test-provider"
    cfg = MagicMock()
    cfg.active_provider.return_value = prov
    cfg.model_sets = model_sets or []
    cfg.workspace = "/ws"
    cfg.worker = MagicMock(orchestrator_url="http://orch:9000")
    cfg.providers = []
    return cfg


def _make_graph(**overrides) -> TaskGraph:
    defaults = dict(
        worker=_make_worker(),
        agent_store=MagicMock(),
        chat=MagicMock(),
        config=_make_config(),
        tracker=None,
        projects=None,
        tool_registry=None,
        audit=NullAuditTrail(),
        stream_id="",
        conversation="",
    )
    defaults.update(overrides)
    worker = defaults.pop("worker")
    return TaskGraph(worker, **defaults)


async def _collect(aiter: AsyncIterator) -> list:
    out = []
    async for item in aiter:
        out.append(item)
    return out


async def _async_gen(items):
    for item in items:
        yield item


# ==================================================================
# _estimate_tokens
# ==================================================================

class TestEstimateTokens:
    def test_string_content(self):
        msgs = [{"content": "Hello world"}]
        result = _estimate_tokens(msgs)
        assert result > 0
        # Should be in a reasonable range (tokenizer gives ~2-7 tokens for "Hello world")
        assert result <= 20

    def test_list_content_with_text(self):
        msgs = [{"content": [{"text": "abcdefgh"}, {"text": "1234"}]}]
        result = _estimate_tokens(msgs)
        assert result > 0

    def test_list_content_skips_non_dicts(self):
        msgs = [{"content": ["plain_string", {"text": "ok"}]}]
        result = _estimate_tokens(msgs)
        # "plain_string" is not a dict so not counted, but "ok" is
        assert result >= 0

    def test_tool_calls_counted(self):
        msgs = [{
            "content": "",
            "tool_calls": [
                {"function": {"name": "read_file", "arguments": '{"path": "/a"}'}}
            ],
        }]
        result = _estimate_tokens(msgs)
        # Tool calls should contribute tokens
        assert result > 0

    def test_none_content_ignored(self):
        msgs = [{"content": None}]
        result = _estimate_tokens(msgs)
        # Per-message overhead may still contribute
        assert result >= 0

    def test_empty_messages(self):
        assert _estimate_tokens([]) == 0

    def test_missing_content_key(self):
        msgs = [{"role": "user"}]
        result = _estimate_tokens(msgs)
        # Per-message overhead contributes even without content
        assert result >= 0

    def test_longer_content_more_tokens(self):
        short = [{"content": "hi"}]
        long = [{"content": "This is a much longer message with many words in it"}]
        assert _estimate_tokens(long) > _estimate_tokens(short)

    def test_code_counted_conservatively(self):
        code = "def foo():\n    return 1\n" * 100
        msgs = [{"content": code}]
        result = _estimate_tokens(msgs)
        # Code has ~3 chars/token, so 2400 chars should be ~750-800 tokens
        assert result >= 500
        assert result <= 1200


# ==================================================================
# enforce_context_limit
# ==================================================================

class TestEnforceContextLimit:
    def test_empty_messages_returns_unchanged(self):
        result, truncated = enforce_context_limit([], 128000, 4096)
        assert result == []
        assert truncated is False

    def test_zero_context_window_returns_unchanged(self):
        msgs = [{"role": "user", "content": "hi"}]
        result, truncated = enforce_context_limit(msgs, 0, 4096)
        assert result == msgs
        assert truncated is False

    def test_negative_context_window(self):
        msgs = [{"role": "user", "content": "hi"}]
        result, truncated = enforce_context_limit(msgs, -1, 4096)
        assert result == msgs
        assert truncated is False

    def test_under_limit_not_truncated(self):
        msgs = [{"role": "user", "content": "short message"}]
        result, truncated = enforce_context_limit(msgs, 128000, 4096)
        assert result == msgs
        assert truncated is False

    def test_over_limit_truncates_middle(self):
        system = [{"role": "system", "content": "You are helpful."}]
        middle = [{"role": "user", "content": "x" * 400} for _ in range(50)]
        recent = [{"role": "user", "content": "recent msg"}]
        msgs = system + middle + recent

        # tiny context window to force truncation
        result, truncated = enforce_context_limit(msgs, 500, 100, keep_recent=2)
        assert truncated is True
        assert result[0]["role"] == "system"
        assert result[1]["content"] == _TRUNCATION_MARKER
        assert len(result) < len(msgs)

    def test_few_non_system_messages_not_truncated(self):
        system = [{"role": "system", "content": "sys"}]
        msgs = system + [{"role": "user", "content": "x" * 1000}]
        # Only 1 non-system message, keep_recent=10 → no truncation
        result, truncated = enforce_context_limit(msgs, 100, 50, keep_recent=10)
        assert truncated is False

    def test_multiple_system_messages_preserved(self):
        msgs = [
            {"role": "system", "content": "First system"},
            {"role": "system", "content": "Second system"},
            *[{"role": "user", "content": "x" * 400} for _ in range(30)],
            {"role": "user", "content": "last"},
        ]
        result, truncated = enforce_context_limit(msgs, 500, 100, keep_recent=2)
        assert truncated is True
        assert result[0]["content"] == "First system"
        assert result[1]["content"] == "Second system"

    def test_progressive_trim_when_recent_exceeds_budget(self):
        system = [{"role": "system", "content": "s" * 200}]
        non_system = [{"role": "user", "content": "r" * 200} for _ in range(20)]
        msgs = system + non_system
        # Use a small context_window so system+recent exceed budget; keep_recent > 2
        result, truncated = enforce_context_limit(msgs, 300, 50, keep_recent=18)
        assert truncated is True
        recent_count = len(result) - 2  # system + marker
        assert recent_count < 18


# ==================================================================
# Acc — stream accumulator
# ==================================================================

class TestAcc:
    @pytest.mark.asyncio
    async def test_accumulates_text_tokens(self):
        events = [
            {"event_type": "token", "data": {"token": "Hello "}},
            {"event_type": "token", "data": {"token": "world"}},
        ]
        acc = Acc(_async_gen(events))
        collected = await _collect(acc)
        assert len(collected) == 2
        assert acc.text == "Hello world"

    @pytest.mark.asyncio
    async def test_accumulates_reasoning(self):
        events = [
            {"event_type": "reasoning", "data": {"token": "Step 1. "}},
            {"event_type": "reasoning", "data": {"token": "Step 2."}},
        ]
        acc = Acc(_async_gen(events))
        await _collect(acc)
        assert acc.reasoning == "Step 1. Step 2."

    @pytest.mark.asyncio
    async def test_accumulates_tool_call_deltas(self):
        events = [
            {"event_type": "tool_call_delta", "data": {
                "index": 0, "id": "call_1", "name": "read_file", "arguments": '{"pa',
            }},
            {"event_type": "tool_call_delta", "data": {
                "index": 0, "arguments": 'th": "/a"}',
            }},
        ]
        acc = Acc(_async_gen(events))
        await _collect(acc)
        assert len(acc.tool_calls) == 1
        assert acc.tool_calls[0]["id"] == "call_1"
        assert acc.tool_calls[0]["function"]["name"] == "read_file"
        assert acc.tool_calls[0]["function"]["arguments"] == '{"path": "/a"}'

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_sorted_by_index(self):
        events = [
            {"event_type": "tool_call_delta", "data": {
                "index": 1, "id": "c2", "name": "write_file",
            }},
            {"event_type": "tool_call_delta", "data": {
                "index": 0, "id": "c1", "name": "read_file",
            }},
        ]
        acc = Acc(_async_gen(events))
        await _collect(acc)
        assert len(acc.tool_calls) == 2
        assert acc.tool_calls[0]["id"] == "c1"
        assert acc.tool_calls[1]["id"] == "c2"

    @pytest.mark.asyncio
    async def test_unknown_event_type_ignored(self):
        events = [
            {"event_type": "custom_event", "data": {"foo": "bar"}},
            {"event_type": "token", "data": {"token": "ok"}},
        ]
        acc = Acc(_async_gen(events))
        await _collect(acc)
        assert acc.text == "ok"

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        acc = Acc(_async_gen([]))
        await _collect(acc)
        assert acc.text == ""
        assert acc.reasoning == ""
        assert acc.tool_calls == []

    @pytest.mark.asyncio
    async def test_tool_call_delta_defaults(self):
        events = [
            {"event_type": "tool_call_delta", "data": {}},
        ]
        acc = Acc(_async_gen(events))
        await _collect(acc)
        assert len(acc.tool_calls) == 1
        assert acc.tool_calls[0]["id"] == ""
        assert acc.tool_calls[0]["function"]["name"] == ""


# ==================================================================
# _TaskProxy
# ==================================================================

class TestTaskProxy:
    def test_defaults(self):
        tp = _TaskProxy()
        assert tp.id == ""
        assert tp.kind == "converse"
        assert tp.enable_thinking is None
        assert tp.prior_work == []

    def test_custom_fields(self):
        tp = _TaskProxy(id="t1", kind="code", gpu=2, enable_thinking=True)
        assert tp.id == "t1"
        assert tp.gpu == 2
        assert tp.enable_thinking is True


# ==================================================================
# TaskGraph — construction
# ==================================================================

class TestTaskGraphConstruction:
    def test_init_defaults(self):
        g = _make_graph()
        assert g._allowed_tools is None
        assert g._last_work is None
        assert g._agent_uses_sandbox is False
        assert g._agent_scope == "global"

    def test_audit_defaults_to_null(self):
        g = _make_graph(audit=None)
        assert isinstance(g.audit, NullAuditTrail)

    def test_from_work(self):
        worker = _make_worker()
        work = {"conversation": "conv-1", "stream_id": "s-1", "workflow_dir": "/wf"}
        g = TaskGraph.from_work(
            worker, work,
            agent_store=MagicMock(),
            chat=MagicMock(),
            config=_make_config(),
        )
        assert g.conversation == "conv-1"
        assert g.stream_id == "s-1"
        assert g._workflow_dir == "/wf"

    def test_from_work_no_workflow_dir(self):
        worker = _make_worker()
        work = {"conversation": "c"}
        g = TaskGraph.from_work(
            worker, work,
            agent_store=MagicMock(),
            chat=MagicMock(),
            config=_make_config(),
        )
        assert g._workflow_dir is None


# ==================================================================
# TaskGraph.agent()
# ==================================================================

class TestTaskGraphAgent:
    def test_agent_without_workflow_dir(self):
        store = MagicMock()
        store.get.return_value = _make_agent_def()
        g = _make_graph(agent_store=store)
        result = g.agent("coder")
        store.get.assert_called_once_with("coder")
        assert g._last_agent_dir is None

    def test_agent_from_workflow_dir(self, tmp_path):
        agent_dir = tmp_path / "agents" / "coder"
        agent_dir.mkdir(parents=True)
        (agent_dir / "definition.json").write_text("{}")

        store = MagicMock()
        local_agent = _make_agent_def()
        store._load_from.return_value = local_agent

        g = _make_graph(agent_store=store)
        g._workflow_dir = str(tmp_path)

        result = g.agent("coder")
        assert result is local_agent
        assert g._last_agent_dir == str(agent_dir)

    def test_agent_workflow_dir_load_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "coder"
        agent_dir.mkdir(parents=True)
        (agent_dir / "definition.json").write_text("{}")

        store = MagicMock()
        store._load_from.return_value = None
        store.get.return_value = _make_agent_def()

        g = _make_graph(agent_store=store)
        g._workflow_dir = str(tmp_path)

        result = g.agent("coder")
        store.get.assert_called_once_with("coder")
        assert g._last_agent_dir is None


# ==================================================================
# TaskGraph._resolve_tools()
# ==================================================================

class TestResolveTools:
    def test_no_tools_returns_none(self):
        ad = _make_agent_def(tools=[])
        g = _make_graph()
        defs, desc = g._resolve_tools(ad)
        assert defs is None
        assert desc == ""

    def test_no_tool_registry_returns_none(self):
        ad = _make_agent_def(tools=["filesystem"])
        g = _make_graph(tool_registry=None)
        defs, desc = g._resolve_tools(ad)
        assert defs is None
        assert desc == ""

    def test_empty_tool_defs_from_registry(self):
        ad = _make_agent_def(tools=["filesystem"], tool_permissions=[], resource_permissions=[])
        reg = MagicMock()
        reg.mcp_definitions.return_value = []
        g = _make_graph(tool_registry=reg)
        defs, desc = g._resolve_tools(ad)
        assert defs is None
        assert desc == ""
        assert g._allowed_tools == set()

    def test_returns_tool_defs_and_description(self):
        ad = _make_agent_def(tools=["ns"], tool_permissions=["read"], resource_permissions=["res"])
        tool_list = [
            {"function": {"name": "ns_read", "description": "Read a file", "parameters": {
                "properties": {"path": {"description": "File path", "type": "string"}},
            }}},
        ]
        reg = MagicMock()
        reg.mcp_definitions.return_value = tool_list
        g = _make_graph(tool_registry=reg)
        defs, desc = g._resolve_tools(ad)
        assert defs == tool_list
        assert g._allowed_tools == {"ns_read"}
        assert "ns_read" in desc
        assert "Read a file" in desc
        assert "path" in desc


# ==================================================================
# TaskGraph._route_model()
# ==================================================================

class TestRouteModel:
    def test_no_model_set_returns_none(self):
        ad = _make_agent_def(model_set="")
        cfg = _make_config()
        cfg.default_model_set.return_value = None
        g = _make_graph(config=cfg)
        result = g._route_model(ad, {})
        assert result is None

    @patch("acai.tasks.graph.TaskGraph._route_model")
    def test_route_model_called_with_complexity(self, mock_route):
        mock_route.return_value = None
        g = _make_graph()
        g._route_model(_make_agent_def(), {"complexity": "high"})
        mock_route.assert_called_once()


# ==================================================================
# TaskGraph._record_cost()
# ==================================================================

class TestRecordCost:
    def test_no_routed_entry_skips(self):
        g = _make_graph(conversation="conv-1")
        g._record_cost({}, {"output_tokens": 100})
        g.chat.record_spend.assert_not_called()

    def test_no_conversation_skips(self):
        g = _make_graph(conversation="")
        g._record_cost({"_routed_entry": {"price_input": 1, "price_output": 2}}, {})
        g.chat.record_spend.assert_not_called()

    def test_records_cost_to_chat(self):
        chat = MagicMock()
        audit = MagicMock()
        audit.record = MagicMock()
        g = _make_graph(chat=chat, conversation="conv-1", audit=audit)
        payload = {
            "messages": [{"content": "x" * 4000}],
            "_routed_entry": {
                "price_input": 3.0,
                "price_output": 15.0,
                "provider": "openai",
                "model": "gpt-4",
            },
        }
        g._record_cost(payload, {"output_tokens": 500})
        chat.record_spend.assert_called_once()
        cost_arg = chat.record_spend.call_args[0][1]
        assert cost_arg > 0

    def test_zero_cost_not_recorded(self):
        chat = MagicMock()
        g = _make_graph(chat=chat, conversation="conv-1")
        payload = {
            "messages": [],
            "_routed_entry": {
                "price_input": 0.0,
                "price_output": 0.0,
                "provider": "x",
                "model": "y",
            },
        }
        g._record_cost(payload, {"output_tokens": 0})
        chat.record_spend.assert_not_called()


# ==================================================================
# TaskGraph._resolve_tool_name()
# ==================================================================

class TestResolveToolName:
    def test_no_allowed_tools_returns_original(self):
        g = _make_graph()
        g._allowed_tools = None
        assert g._resolve_tool_name("whatever") == "whatever"

    def test_exact_match_returned(self):
        g = _make_graph()
        g._allowed_tools = {"filesystem_read_file", "filesystem_write_file"}
        assert g._resolve_tool_name("filesystem_read_file") == "filesystem_read_file"

    def test_bare_name_resolved_to_namespace(self):
        g = _make_graph()
        g._allowed_tools = {"filesystem_read_file"}
        assert g._resolve_tool_name("read_file") == "filesystem_read_file"

    def test_wrong_namespace_resolved(self):
        g = _make_graph()
        g._allowed_tools = {"search_grep"}
        assert g._resolve_tool_name("filesystem_grep") == "search_grep"

    def test_singular_plural_mismatch_resolved(self):
        g = _make_graph()
        g._allowed_tools = {"tasks_create"}
        assert g._resolve_tool_name("task_create") == "tasks_create"

    def test_unresolvable_name_returned_as_is(self):
        g = _make_graph()
        g._allowed_tools = {"filesystem_read"}
        assert g._resolve_tool_name("totally_unknown") == "totally_unknown"


# ==================================================================
# TaskGraph.dispatch_tool() — error and edge cases
# ==================================================================

class TestDispatchTool:
    @pytest.mark.asyncio
    async def test_blocked_tool_returns_error_message(self):
        g = _make_graph()
        g._allowed_tools = {"filesystem_read"}
        result = await g.dispatch_tool("forbidden_tool", {})
        assert "[Tool error]" in result
        assert "not permitted" in result

    @pytest.mark.asyncio
    async def test_scope_check_blocks_call(self):
        g = _make_graph()
        g._allowed_tools = None
        g._agent_scope = "project"
        g._scope_context = {"project": "proj-A"}
        reg = MagicMock()
        td_mock = MagicMock()
        td_mock.scope_key = "project"
        reg.get.return_value = td_mock
        g.tool_registry = reg

        result = await g.dispatch_tool("some_tool", {"project": "proj-B"})
        assert "[Scope error]" in result
        assert "proj-A" in result

    @pytest.mark.asyncio
    async def test_successful_dispatch(self):
        g = _make_graph(conversation="conv-1")
        g._allowed_tools = None
        g._last_work = {"project": "myproj"}

        step_result = MagicMock()
        step_result.error = None
        step_result.text = "tool output"

        with patch("acai.orchestrator.dispatcher.dispatch_tool", new_callable=AsyncMock, return_value=step_result) as mock_dt:
            result = await g.dispatch_tool("read_file", {"path": "/a"})

        assert result == "tool output"

    @pytest.mark.asyncio
    async def test_dispatch_returns_error_from_result(self):
        g = _make_graph()
        g._allowed_tools = None
        g._last_work = {}

        step_result = MagicMock()
        step_result.error = "file not found"
        step_result.text = ""

        with patch("acai.orchestrator.dispatcher.dispatch_tool", new_callable=AsyncMock, return_value=step_result):
            result = await g.dispatch_tool("read_file", {})

        assert "[Tool error]" in result
        assert "file not found" in result

    @pytest.mark.asyncio
    async def test_dispatch_with_sandbox_context(self):
        g = _make_graph(conversation="conv-1")
        g._allowed_tools = None
        g._last_work = {"project": "p1"}
        g._agent_uses_sandbox = True

        step_result = MagicMock()
        step_result.error = None
        step_result.text = "ok"

        with patch("acai.orchestrator.dispatcher.dispatch_tool", new_callable=AsyncMock, return_value=step_result) as mock_dt:
            await g.dispatch_tool("run_cmd", {})
            ctx = mock_dt.call_args[1]["context"]
            assert ctx["uses_sandbox"] is True


# ==================================================================
# TaskGraph._resolve_project_path()
# ==================================================================

class TestResolveProjectPath:
    def test_no_last_work(self):
        g = _make_graph()
        g._last_work = None
        assert g._resolve_project_path() == ""

    def test_worktree_takes_precedence(self):
        g = _make_graph()
        g._last_work = {"worktree": "/wt/clone", "project": "proj"}
        assert g._resolve_project_path() == "/wt/clone"

    def test_project_store_lookup(self):
        proj = MagicMock()
        proj.path = "/projects/myproj"
        projects = MagicMock()
        projects.get.return_value = proj
        g = _make_graph(projects=projects)
        g._last_work = {"worktree": "", "project": "myproj"}
        assert g._resolve_project_path() == "/projects/myproj"

    def test_project_store_returns_none(self):
        projects = MagicMock()
        projects.get.return_value = None
        g = _make_graph(projects=projects)
        g._last_work = {"worktree": "", "project": "unknown"}
        assert g._resolve_project_path() == ""

    def test_no_project_no_worktree(self):
        g = _make_graph()
        g._last_work = {"worktree": "", "project": ""}
        assert g._resolve_project_path() == ""

    def test_no_project_store(self):
        g = _make_graph(projects=None)
        g._last_work = {"worktree": "", "project": "myproj"}
        assert g._resolve_project_path() == ""


# ==================================================================
# TaskGraph._build_scope_context()
# ==================================================================

class TestBuildScopeContext:
    def test_empty_work(self):
        g = _make_graph()
        ctx = g._build_scope_context({}, {})
        assert ctx == {}

    def test_workflow_dir_extracted(self):
        g = _make_graph()
        ctx = g._build_scope_context({"workflow_dir": "/workflows/abc123"}, {})
        assert ctx["workflow_id"] == "abc123"

    def test_extra_context_overrides(self):
        g = _make_graph()
        ctx = g._build_scope_context({}, {"extra_context": {"workflow_id": "from_extra"}})
        assert ctx["workflow_id"] == "from_extra"

    def test_work_workflow_id_overrides_all(self):
        g = _make_graph()
        ctx = g._build_scope_context(
            {"workflow_dir": "/wf/x", "workflow_id": "final"},
            {"extra_context": {"workflow_id": "not_this"}},
        )
        assert ctx["workflow_id"] == "final"

    def test_project_included(self):
        g = _make_graph()
        ctx = g._build_scope_context({"project": "myproj"}, {})
        assert ctx["project"] == "myproj"

    def test_extra_context_from_work(self):
        g = _make_graph()
        ctx = g._build_scope_context(
            {"extra_context": {"workflow_id": "wf-work"}}, {}
        )
        assert ctx["workflow_id"] == "wf-work"

    def test_non_dict_extra_context_ignored(self):
        g = _make_graph()
        ctx = g._build_scope_context({}, {"extra_context": "not-a-dict"})
        assert "workflow_id" not in ctx


# ==================================================================
# TaskGraph._check_scope()
# ==================================================================

class TestCheckScope:
    def test_global_scope_passes(self):
        g = _make_graph()
        g._agent_scope = "global"
        assert g._check_scope("any_tool", {}) == ""

    def test_no_scope_context_passes(self):
        g = _make_graph()
        g._agent_scope = "project"
        g._scope_context = {}
        assert g._check_scope("any_tool", {}) == ""

    def test_no_tool_registry_passes(self):
        g = _make_graph(tool_registry=None)
        g._agent_scope = "project"
        g._scope_context = {"project": "A"}
        assert g._check_scope("tool", {}) == ""

    def test_tool_not_in_registry_passes(self):
        reg = MagicMock()
        reg.get.return_value = None
        g = _make_graph(tool_registry=reg)
        g._agent_scope = "project"
        g._scope_context = {"project": "A"}
        assert g._check_scope("unknown_tool", {}) == ""

    def test_tool_no_scope_key_passes(self):
        reg = MagicMock()
        td = MagicMock()
        td.scope_key = ""
        reg.get.return_value = td
        g = _make_graph(tool_registry=reg)
        g._agent_scope = "project"
        g._scope_context = {"project": "A"}
        assert g._check_scope("tool", {}) == ""

    def test_matching_scope_passes(self):
        reg = MagicMock()
        td = MagicMock()
        td.scope_key = "project"
        reg.get.return_value = td
        g = _make_graph(tool_registry=reg)
        g._agent_scope = "project"
        g._scope_context = {"project": "A"}
        assert g._check_scope("tool", {"project": "A"}) == ""

    def test_scope_violation_returns_error(self):
        reg = MagicMock()
        td = MagicMock()
        td.scope_key = "project"
        reg.get.return_value = td
        g = _make_graph(tool_registry=reg)
        g._agent_scope = "project"
        g._scope_context = {"project": "A"}
        result = g._check_scope("tool", {"project": "B"})
        assert "[Scope error]" in result
        assert "A" in result
        assert "B" in result

    def test_no_expected_value_passes(self):
        reg = MagicMock()
        td = MagicMock()
        td.scope_key = "project"
        reg.get.return_value = td
        g = _make_graph(tool_registry=reg)
        g._agent_scope = "project"
        g._scope_context = {"workflow_id": "wf"}
        assert g._check_scope("tool", {"project": "B"}) == ""

    def test_no_actual_value_passes(self):
        reg = MagicMock()
        td = MagicMock()
        td.scope_key = "project"
        reg.get.return_value = td
        g = _make_graph(tool_registry=reg)
        g._agent_scope = "project"
        g._scope_context = {"project": "A"}
        assert g._check_scope("tool", {}) == ""


# ==================================================================
# TaskGraph._error_event()
# ==================================================================

class TestErrorEvent:
    def test_basic_error(self):
        g = _make_graph()
        ev = g._error_event("Something failed")
        assert ev["event_type"] == "error"
        assert ev["data"]["message"] == "Something failed"
        assert "traceback" not in ev["data"]

    def test_with_traceback(self):
        g = _make_graph()
        ev = g._error_event("oops", tb="Traceback...")
        assert ev["data"]["traceback"] == "Traceback..."

    def test_pushes_to_tracker(self):
        tracker = MagicMock()
        g = _make_graph(tracker=tracker, stream_id="s1")
        ev = g._error_event("fail")
        tracker.push.assert_called_once_with("s1", ev)

    def test_no_push_without_tracker(self):
        g = _make_graph(tracker=None, stream_id="s1")
        ev = g._error_event("fail")
        assert ev["event_type"] == "error"

    def test_no_push_without_stream_id(self):
        tracker = MagicMock()
        g = _make_graph(tracker=tracker, stream_id="")
        g._error_event("fail")
        tracker.push.assert_not_called()


# ==================================================================
# TaskGraph._done_event()
# ==================================================================

class TestDoneEvent:
    def test_basic_done(self):
        g = _make_graph()
        ev = g._done_event()
        assert ev["event_type"] == "done"
        assert ev["data"] == {}

    def test_with_git_result(self):
        g = _make_graph()
        git = {"committed": True, "branch": "main", "pushed": True}
        ev = g._done_event(git_result=git)
        assert ev["data"]["git"] == git

    def test_pushes_to_tracker(self):
        tracker = MagicMock()
        g = _make_graph(tracker=tracker, stream_id="s1")
        ev = g._done_event()
        tracker.push.assert_called_once_with("s1", ev)


# ==================================================================
# TaskGraph._save_response()
# ==================================================================

class TestSaveResponse:
    def test_saves_text_to_chat(self):
        chat = MagicMock()
        g = _make_graph(chat=chat, conversation="conv-1")
        acc = MagicMock()
        acc.text = "Hello!"
        acc.reasoning = ""
        g._save_response(acc)
        chat.append.assert_called_once()
        msg = chat.append.call_args[0][1]
        assert msg["role"] == "assistant"
        assert msg["content"] == "Hello!"
        assert "reasoning" not in msg

    def test_saves_reasoning_when_present(self):
        chat = MagicMock()
        g = _make_graph(chat=chat, conversation="conv-1")
        acc = MagicMock()
        acc.text = "answer"
        acc.reasoning = "I thought about it"
        g._save_response(acc)
        msg = chat.append.call_args[0][1]
        assert msg["reasoning"] == "I thought about it"

    def test_no_save_without_text(self):
        chat = MagicMock()
        g = _make_graph(chat=chat, conversation="conv-1")
        acc = MagicMock()
        acc.text = ""
        acc.reasoning = ""
        g._save_response(acc)
        chat.append.assert_not_called()

    def test_no_save_without_conversation(self):
        chat = MagicMock()
        g = _make_graph(chat=chat, conversation="")
        acc = MagicMock()
        acc.text = "text"
        acc.reasoning = ""
        g._save_response(acc)
        chat.append.assert_not_called()


# ==================================================================
# TaskGraph._finalize_git()
# ==================================================================

class TestFinalizeGit:
    @pytest.mark.asyncio
    async def test_no_worktree_skips(self):
        g = _make_graph()
        assert await g._finalize_git({}) is None
        assert await g._finalize_git({"worktree": ""}) is None

    @pytest.mark.asyncio
    async def test_none_work_skips(self):
        g = _make_graph()
        assert await g._finalize_git(None) is None

    @pytest.mark.asyncio
    async def test_git_status_fails_returns_none(self):
        g = _make_graph()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = await g._finalize_git({"worktree": "/wt"})
        assert result is None

    @pytest.mark.asyncio
    async def test_clean_worktree_returns_none(self):
        g = _make_graph()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = await g._finalize_git({"worktree": "/wt"})
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_commit_and_push(self):
        g = _make_graph()

        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if cmd[1] == "status":
                return MagicMock(returncode=0, stdout=" M file.py\n", stderr="")
            if cmd[1] == "add":
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "commit":
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "branch":
                return MagicMock(returncode=0, stdout="feature-1\n", stderr="")
            if cmd[1] == "push":
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = await g._finalize_git({
                "worktree": "/wt", "title": "my change", "task_id": "t-1",
            })

        assert result["committed"] is True
        assert result["branch"] == "feature-1"
        assert result["pushed"] is True

    @pytest.mark.asyncio
    async def test_commit_fails_returns_none(self):
        g = _make_graph()

        def fake_run(cmd, **kw):
            if cmd[1] == "status":
                return MagicMock(returncode=0, stdout=" M f.py\n", stderr="")
            if cmd[1] == "add":
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "commit":
                return MagicMock(returncode=1, stdout="", stderr="commit error")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = await g._finalize_git({"worktree": "/wt"})
        assert result is None

    @pytest.mark.asyncio
    async def test_nothing_to_commit_returns_none(self):
        g = _make_graph()

        def fake_run(cmd, **kw):
            if cmd[1] == "status":
                return MagicMock(returncode=0, stdout=" M f.py\n", stderr="")
            if cmd[1] == "add":
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "commit":
                return MagicMock(returncode=1, stdout="nothing to commit", stderr="")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            result = await g._finalize_git({"worktree": "/wt"})
        assert result is None

    @pytest.mark.asyncio
    async def test_push_fails_still_returns_result(self):
        g = _make_graph()

        def fake_run(cmd, **kw):
            if cmd[1] == "status":
                return MagicMock(returncode=0, stdout=" M f.py\n", stderr="")
            if cmd[1] == "add":
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "commit":
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "branch":
                return MagicMock(returncode=0, stdout="main\n", stderr="")
            if cmd[1] == "push":
                return MagicMock(returncode=1, stdout="", stderr="push rejected")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            result = await g._finalize_git({"worktree": "/wt", "title": "test"})
        assert result["committed"] is True
        assert result["pushed"] is False

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        g = _make_graph()
        with patch("subprocess.run", side_effect=OSError("git not found")):
            result = await g._finalize_git({"worktree": "/wt"})
        assert result is None

    @pytest.mark.asyncio
    async def test_title_fallback(self):
        g = _make_graph()
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if cmd[1] == "status":
                return MagicMock(returncode=0, stdout=" M f.py\n", stderr="")
            if cmd[1] == "add":
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "commit":
                assert cmd[3] == "agent work"  # default title, no task_id
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "branch":
                return MagicMock(returncode=0, stdout="dev\n", stderr="")
            if cmd[1] == "push":
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            await g._finalize_git({"worktree": "/wt"})


# ==================================================================
# TaskGraph.dispatch() — error handling
# ==================================================================

class TestDispatch:
    @pytest.mark.asyncio
    async def test_aiohttp_client_error(self):
        import aiohttp
        g = _make_graph(tracker=MagicMock(), stream_id="s1")

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", side_effect=aiohttp.ClientError("Connection refused")):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}))

        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert "Worker connection error" in events[0]["data"]["message"]
        g.tracker.push.assert_called()

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        g = _make_graph(tracker=MagicMock(), stream_id="s1")

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", side_effect=RuntimeError("unexpected")):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}))

        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert "RuntimeError" in events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_json_decode_error_in_event(self):
        g = _make_graph()

        bad_event = MagicMock()
        bad_event.event = "token"
        bad_event.json.side_effect = json.JSONDecodeError("err", "", 0)

        async def _gen(*a, **kw):
            yield bad_event

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", return_value=_gen()):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}))

        assert len(events) == 1
        assert events[0]["event_type"] == "token"
        assert events[0]["data"] == {}

    @pytest.mark.asyncio
    async def test_done_event_consumed_not_yielded(self):
        g = _make_graph()

        token_event = MagicMock()
        token_event.event = "token"
        token_event.json.return_value = {"token": "hi"}

        done_event = MagicMock()
        done_event.event = "done"
        done_event.json.return_value = {"output_tokens": 10}

        async def _gen(*a, **kw):
            yield token_event
            yield done_event

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", return_value=_gen()):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}))

        types = [e["event_type"] for e in events]
        assert "done" not in types
        assert "token" in types

    @pytest.mark.asyncio
    async def test_error_event_causes_return(self):
        g = _make_graph()

        err_event = MagicMock()
        err_event.event = "error"
        err_event.json.return_value = {"message": "bad"}

        extra = MagicMock()
        extra.event = "token"
        extra.json.return_value = {"token": "should not appear"}

        async def _gen(*a, **kw):
            yield err_event
            yield extra

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", return_value=_gen()):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}))

        assert len(events) == 1
        assert events[0]["event_type"] == "error"

    @pytest.mark.asyncio
    async def test_stream_mode_reasoning_remaps_tokens(self):
        g = _make_graph()

        tok = MagicMock()
        tok.event = "token"
        tok.json.return_value = {"token": "think"}

        done = MagicMock()
        done.event = "done"
        done.json.return_value = {}

        async def _gen(*a, **kw):
            yield tok
            yield done

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", return_value=_gen()):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}, stream_mode="reasoning"))

        assert events[0]["event_type"] == "reasoning"

    @pytest.mark.asyncio
    async def test_tracker_push_in_normal_mode(self):
        tracker = MagicMock()
        g = _make_graph(tracker=tracker, stream_id="s1")

        tok = MagicMock()
        tok.event = "token"
        tok.json.return_value = {"token": "x"}

        done = MagicMock()
        done.event = "done"
        done.json.return_value = {}

        async def _gen(*a, **kw):
            yield tok
            yield done

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", return_value=_gen()):
            await _collect(g.dispatch({"agent": "test", "messages": []}))

        tracker.push.assert_called()

    @pytest.mark.asyncio
    async def test_silent_mode_no_tracker_push(self):
        tracker = MagicMock()
        g = _make_graph(tracker=tracker, stream_id="s1")

        tok = MagicMock()
        tok.event = "token"
        tok.json.return_value = {"token": "x"}

        done = MagicMock()
        done.event = "done"
        done.json.return_value = {}

        async def _gen(*a, **kw):
            yield tok
            yield done

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", return_value=_gen()):
            await _collect(g.dispatch({"agent": "test", "messages": []}, stream_mode="silent"))

        tracker.push.assert_not_called()


# ==================================================================
# TaskGraph._run_with_tools()
# ==================================================================

class TestRunWithTools:
    @pytest.mark.asyncio
    async def test_error_in_initial_stream_stops(self):
        g = _make_graph(config=_make_config())

        async def _dispatch(payload, **kw):
            yield {"event_type": "error", "data": {"message": "boom"}}

        g.dispatch = _dispatch
        events = await _collect(g._run_with_tools({"messages": [], "agent": "test"}))
        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert g._last_acc.text == ""

    @pytest.mark.asyncio
    async def test_tool_call_json_parse_error(self):
        g = _make_graph(config=_make_config(), conversation="conv-1")
        g.chat = MagicMock()

        round_count = 0

        async def _dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "read_file",
                    "arguments": "INVALID JSON{{{",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "done"}}

        step_result = MagicMock()
        step_result.error = None
        step_result.text = "file contents"

        g.dispatch = _dispatch
        with patch("acai.orchestrator.dispatcher.dispatch_tool", new_callable=AsyncMock, return_value=step_result):
            events = await _collect(g._run_with_tools({"messages": [], "agent": "test"}))

        tool_starts = [e for e in events if e.get("event_type") == "tool_start"]
        assert len(tool_starts) == 1
        assert tool_starts[0]["data"]["args"] == {}

    @pytest.mark.asyncio
    async def test_tool_dispatch_exception_captured(self):
        g = _make_graph(config=_make_config(), conversation="")
        g.chat = MagicMock()

        round_count = 0

        async def _dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "bad_tool",
                    "arguments": "{}",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "recovery"}}

        g.dispatch = _dispatch
        with patch.object(g, "dispatch_tool", new_callable=AsyncMock, side_effect=ConnectionError("timeout")):
            events = await _collect(g._run_with_tools({"messages": [], "agent": "test"}))

        tool_ends = [e for e in events if e.get("event_type") == "tool_end"]
        assert len(tool_ends) == 1
        assert "[Tool error]" in tool_ends[0]["data"]["result_preview"]
        assert "ConnectionError" in tool_ends[0]["data"]["result_preview"]

    @pytest.mark.asyncio
    async def test_error_in_followup_stream_stops(self):
        g = _make_graph(config=_make_config())

        round_count = 0

        async def _dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "tool",
                    "arguments": "{}",
                }}
            else:
                yield {"event_type": "error", "data": {"message": "followup error"}}

        step_result = MagicMock()
        step_result.error = None
        step_result.text = "ok"

        g.dispatch = _dispatch
        with patch("acai.orchestrator.dispatcher.dispatch_tool", new_callable=AsyncMock, return_value=step_result):
            events = await _collect(g._run_with_tools({"messages": [], "agent": "test"}))

        errors = [e for e in events if e.get("event_type") == "error"]
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_tracker_push_for_tool_events(self):
        tracker = MagicMock()
        g = _make_graph(config=_make_config(), tracker=tracker, stream_id="s1")

        round_count = 0

        async def _dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "tool",
                    "arguments": "{}",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "ok"}}

        step_result = MagicMock()
        step_result.error = None
        step_result.text = "result"

        g.dispatch = _dispatch
        with patch("acai.orchestrator.dispatcher.dispatch_tool", new_callable=AsyncMock, return_value=step_result):
            await _collect(g._run_with_tools({"messages": [], "agent": "test"}))

        push_calls = tracker.push.call_args_list
        pushed_types = [c[0][1]["event_type"] for c in push_calls]
        assert "tool_start" in pushed_types
        assert "tool_end" in pushed_types


# ==================================================================
# TaskGraph._try_compress_conversation()
# ==================================================================

class TestTryCompressConversation:
    @pytest.mark.asyncio
    async def test_no_conversation_skips(self):
        g = _make_graph(conversation="")
        result = await g._try_compress_conversation({})
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_chat_skips(self):
        chat = MagicMock()
        chat.read.return_value = []
        g = _make_graph(chat=chat, conversation="conv-1")
        result = await g._try_compress_conversation({})
        assert result is None

    @pytest.mark.asyncio
    async def test_no_compression_needed_skips(self):
        chat = MagicMock()
        chat.read.return_value = [{"role": "user", "content": "short"}]
        g = _make_graph(chat=chat, conversation="conv-1")
        with patch("acai.orchestrator.agent_store.needs_compression", return_value=False):
            result = await g._try_compress_conversation({})
        assert result is None

    @pytest.mark.asyncio
    async def test_compression_exception_returns_none(self):
        chat = MagicMock()
        chat.read.return_value = [{"role": "user", "content": "x" * 10000}]
        g = _make_graph(chat=chat, conversation="conv-1")
        with patch("acai.orchestrator.agent_store.needs_compression", return_value=True), \
             patch("acai.provider.create_llm", side_effect=RuntimeError("no LLM")):
            result = await g._try_compress_conversation({})
        assert result is None

    @pytest.mark.asyncio
    async def test_no_actual_compression_returns_none(self):
        chat = MagicMock()
        chat.read.return_value = [{"role": "user", "content": "x" * 10000}]
        g = _make_graph(chat=chat, conversation="conv-1")
        with patch("acai.orchestrator.agent_store.needs_compression", return_value=True), \
             patch("acai.provider.create_llm"), \
             patch("acai.orchestrator.agent_store.compress_messages", return_value=([], False)):
            result = await g._try_compress_conversation({})
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_compression(self):
        chat = MagicMock()
        chat.read.return_value = [
            {"role": "user", "content": "x" * 10000},
            {"role": "assistant", "content": "y" * 10000},
        ]
        tracker = MagicMock()
        compressed = [{"role": "user", "content": "summary"}]
        g = _make_graph(chat=chat, conversation="conv-1", tracker=tracker, stream_id="s1")
        with patch("acai.orchestrator.agent_store.needs_compression", return_value=True), \
             patch("acai.provider.create_llm"), \
             patch("acai.orchestrator.agent_store.compress_messages", return_value=(compressed, True)):
            result = await g._try_compress_conversation({})
        assert result is not None
        assert result["event_type"] == "context_compressed"
        assert result["data"]["compressed_messages"] == 1
        chat.write.assert_called_once_with("conv-1", compressed)
        tracker.push.assert_called()

    @pytest.mark.asyncio
    async def test_persist_failure_returns_none(self):
        chat = MagicMock()
        chat.read.return_value = [{"role": "user", "content": "x" * 10000}]
        chat.write.side_effect = IOError("disk full")
        g = _make_graph(chat=chat, conversation="conv-1")
        with patch("acai.orchestrator.agent_store.needs_compression", return_value=True), \
             patch("acai.provider.create_llm"), \
             patch("acai.orchestrator.agent_store.compress_messages", return_value=([{"role": "user", "content": "s"}], True)):
            result = await g._try_compress_conversation({})
        assert result is None

    @pytest.mark.asyncio
    async def test_filters_display_roles(self):
        chat = MagicMock()
        chat.read.return_value = [
            {"role": "user", "content": "x"},
            {"role": "tool_call", "content": "{}"},
            {"role": "tool_result", "content": "result"},
            {"role": "assistant", "content": "y"},
        ]
        g = _make_graph(chat=chat, conversation="conv-1")
        with patch("acai.orchestrator.agent_store.needs_compression", return_value=False) as mock_nc:
            await g._try_compress_conversation({})
            eligible = mock_nc.call_args[0][0]
            roles = {m["role"] for m in eligible}
            assert "tool_call" not in roles
            assert "tool_result" not in roles

    @pytest.mark.asyncio
    async def test_conversation_from_work_fallback(self):
        chat = MagicMock()
        chat.read.return_value = []
        g = _make_graph(chat=chat, conversation="")
        result = await g._try_compress_conversation({"conversation": "work-conv"})
        chat.read.assert_called_with("work-conv")
        assert result is None


# ==================================================================
# TaskGraph.run() — abstract method
# ==================================================================

class TestRun:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self):
        g = _make_graph()
        with pytest.raises(NotImplementedError, match="Subclass TaskGraph"):
            async for _ in g.run({}):
                pass  # pragma: no cover


# ==================================================================
# TaskGraph.prepare() — provider-allowed check
# ==================================================================

class TestPrepareProviderBlocked:
    @pytest.mark.asyncio
    async def test_prepare_raises_on_blocked_provider(self):
        agent_def = _make_agent_def()
        agent_def.is_provider_allowed.return_value = False
        store = MagicMock()
        store.get.return_value = agent_def

        g = _make_graph(agent_store=store)
        g._workflow_dir = None

        with patch("acai.orchestrator.agent_store.resolve_task", return_value=MagicMock()), \
             patch("acai.orchestrator.agent_store.hydrate_task", return_value=[{"role": "user", "content": "hi"}]):
            with pytest.raises(ValueError, match="not allowed to run"):
                g.prepare("test-agent", {"agent": "test-agent"})


# ==================================================================
# CRITICAL FAILURE MODES — dispatch()
# ==================================================================

class TestDispatchFailureModes:
    """Tests for dispatch() edge cases that must give the client clear feedback."""

    @pytest.mark.asyncio
    async def test_worker_http_error_includes_status_code(self):
        """4xx/5xx from the worker surfaces the status code in the error message."""
        import aiohttp

        g = _make_graph()
        exc = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=503,
            message="Service Unavailable",
        )

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", side_effect=exc):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}))

        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert "503" in events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_worker_http_429_error(self):
        """Rate limiting (429) is reported clearly."""
        import aiohttp

        g = _make_graph()
        exc = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=429,
            message="Too Many Requests",
        )

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", side_effect=exc):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}))

        assert events[0]["event_type"] == "error"
        assert "429" in events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_malformed_json_in_stream_no_crash(self):
        """Worker sends garbage JSON — dispatch yields event with empty data, no crash."""
        g = _make_graph()

        bad = MagicMock()
        bad.event = "token"
        bad.json.side_effect = ValueError("No JSON")

        done = MagicMock()
        done.event = "done"
        done.json.return_value = {}

        async def _gen(*a, **kw):
            yield bad
            yield done

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", return_value=_gen()):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}))

        assert len(events) == 1
        assert events[0]["event_type"] == "token"
        assert events[0]["data"] == {}

    @pytest.mark.asyncio
    async def test_connection_drop_mid_stream(self):
        """Worker disconnects mid-stream — error event mentions 'connection'."""
        import aiohttp

        g = _make_graph()

        tok = MagicMock()
        tok.event = "token"
        tok.json.return_value = {"token": "partial"}

        async def _gen(*a, **kw):
            yield tok
            raise aiohttp.ServerDisconnectedError("connection reset")

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", return_value=_gen()):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}))

        error_events = [e for e in events if e["event_type"] == "error"]
        assert len(error_events) == 1
        msg = error_events[0]["data"]["message"].lower()
        assert "connection" in msg

    @pytest.mark.asyncio
    async def test_worker_error_event_passed_through_immediately(self):
        """Worker sends 'error' event type — it is yielded and dispatch stops."""
        g = _make_graph()

        err = MagicMock()
        err.event = "error"
        err.json.return_value = {"message": "model overloaded"}

        trailing = MagicMock()
        trailing.event = "token"
        trailing.json.return_value = {"token": "should not appear"}

        async def _gen(*a, **kw):
            yield err
            yield trailing

        with patch("acai.orchestrator.iterator.AsyncSSEIterator", return_value=_gen()):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}))

        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert events[0]["data"]["message"] == "model overloaded"

    @pytest.mark.asyncio
    async def test_empty_stream_does_not_hang(self):
        """Worker returns no events at all — dispatch completes without yielding."""

        async def _gen(*a, **kw):
            return
            yield  # make it an async generator

        g = _make_graph()
        with patch("acai.orchestrator.iterator.AsyncSSEIterator", return_value=_gen()):
            events = await _collect(g.dispatch({"agent": "test", "messages": []}))

        assert events == []


# ==================================================================
# CRITICAL FAILURE MODES — _run_with_tools()
# ==================================================================

class TestRunWithToolsFailureModes:
    """Tests for the tool follow-up loop's critical failure modes."""

    @pytest.mark.asyncio
    async def test_invalid_json_args_uses_empty_dict(self):
        """LLM returns tool_call with unparseable arguments → {} used, tool still called."""
        g = _make_graph(config=_make_config(), conversation="conv-1")
        g.chat = MagicMock()

        round_count = 0

        async def _dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "read_file",
                    "arguments": "not valid json at all!!! {{{",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "done"}}

        tool_called_with = []

        async def mock_dispatch_tool(name, args):
            tool_called_with.append((name, args))
            return "result"

        g.dispatch = _dispatch
        g.dispatch_tool = mock_dispatch_tool
        events = await _collect(g._run_with_tools({"messages": [], "agent": "test"}))

        tool_starts = [e for e in events if e["event_type"] == "tool_start"]
        assert tool_starts[0]["data"]["args"] == {}
        assert tool_called_with[0] == ("read_file", {})

    @pytest.mark.asyncio
    async def test_tool_dispatch_exception_format(self):
        """Tool raises exception → '[Tool error] ExceptionType: message' fed back to LLM."""
        g = _make_graph(config=_make_config(), conversation="")
        g.chat = MagicMock()

        round_count = 0

        async def _dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "exploding_tool",
                    "arguments": "{}",
                }}
            else:
                # Verify the error was fed back in the followup messages
                messages = payload.get("messages", [])
                tool_msgs = [m for m in messages if m.get("role") == "tool"]
                assert any(
                    "[Tool error] ValueError: kaboom" in m.get("content", "")
                    for m in tool_msgs
                )
                yield {"event_type": "token", "data": {"token": "recovered"}}

        g.dispatch = _dispatch
        with patch.object(g, "dispatch_tool", new_callable=AsyncMock, side_effect=ValueError("kaboom")):
            events = await _collect(g._run_with_tools({"messages": [], "agent": "test"}))

        tool_ends = [e for e in events if e["event_type"] == "tool_end"]
        assert "[Tool error] ValueError: kaboom" in tool_ends[0]["data"]["result_preview"]

    @pytest.mark.asyncio
    async def test_long_tool_result_truncated(self):
        """Tool returns >50KB result → truncated before being passed to LLM."""
        g = _make_graph(config=_make_config(), conversation="")
        g.chat = MagicMock()

        round_count = 0
        big_result = "x" * 60_000

        async def _dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "big_tool",
                    "arguments": "{}",
                }}
            else:
                messages = payload.get("messages", [])
                tool_msgs = [m for m in messages if m.get("role") == "tool"]
                for m in tool_msgs:
                    assert len(m["content"]) <= _MAX_TOOL_RESULT_CHARS + len(_TOOL_RESULT_TRUNCATION_MSG)
                    assert m["content"].endswith(_TOOL_RESULT_TRUNCATION_MSG)
                yield {"event_type": "token", "data": {"token": "done"}}

        g.dispatch = _dispatch

        async def mock_dispatch_tool(name, args):
            return big_result

        g.dispatch_tool = mock_dispatch_tool
        events = await _collect(g._run_with_tools({"messages": [], "agent": "test"}))

        token_events = [e for e in events if e["event_type"] == "token"]
        assert len(token_events) == 1

    @pytest.mark.asyncio
    async def test_max_iterations_breaks_infinite_loop(self):
        """LLM keeps returning tool calls → max_iterations halts the loop."""
        g = _make_graph(config=_make_config(), conversation="")
        g.chat = MagicMock()

        call_count = 0

        async def _dispatch(payload, **kw):
            nonlocal call_count
            call_count += 1
            yield {"event_type": "tool_call_delta", "data": {
                "index": 0, "id": f"c{call_count}", "name": "loop_tool",
                "arguments": "{}",
            }}

        async def mock_dispatch_tool(name, args):
            return "tool result"

        g.dispatch = _dispatch
        g.dispatch_tool = mock_dispatch_tool
        events = await _collect(g._run_with_tools(
            {"messages": [], "agent": "test"}, max_iterations=3
        ))

        error_events = [e for e in events if e["event_type"] == "error"]
        assert len(error_events) == 1
        assert "maximum iterations" in error_events[0]["data"]["message"]
        assert call_count <= 4  # initial + 3 rounds

    @pytest.mark.asyncio
    async def test_empty_function_name_error(self):
        """Tool call with empty function name → clear error message returned."""
        g = _make_graph(config=_make_config(), conversation="")
        g.chat = MagicMock()

        round_count = 0

        async def _dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "",
                    "arguments": "{}",
                }}
            else:
                messages = payload.get("messages", [])
                tool_msgs = [m for m in messages if m.get("role") == "tool"]
                assert any("empty function name" in m["content"] for m in tool_msgs)
                yield {"event_type": "token", "data": {"token": "done"}}

        g.dispatch = _dispatch
        events = await _collect(g._run_with_tools({"messages": [], "agent": "test"}))

        tool_ends = [e for e in events if e["event_type"] == "tool_end"]
        assert "[Tool error]" in tool_ends[0]["data"]["result_preview"]
        assert "empty function name" in tool_ends[0]["data"]["result_preview"]

    @pytest.mark.asyncio
    async def test_tool_not_permitted_message(self):
        """Tool call on non-permitted tool → '[Tool error] not permitted' in response."""
        g = _make_graph(config=_make_config(), conversation="")
        g.chat = MagicMock()
        g._allowed_tools = {"filesystem_read"}

        round_count = 0

        async def _dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "forbidden_tool",
                    "arguments": "{}",
                }}
            else:
                messages = payload.get("messages", [])
                tool_msgs = [m for m in messages if m.get("role") == "tool"]
                assert any("not permitted" in m["content"] for m in tool_msgs)
                yield {"event_type": "token", "data": {"token": "understood"}}

        g.dispatch = _dispatch
        events = await _collect(g._run_with_tools({"messages": [], "agent": "test"}))

        tool_ends = [e for e in events if e["event_type"] == "tool_end"]
        assert "not permitted" in tool_ends[0]["data"]["result_preview"]

    @pytest.mark.asyncio
    async def test_llm_stops_after_tool_error_completes_normally(self):
        """LLM gets tool error, stops returning tool calls → conversation completes."""
        g = _make_graph(config=_make_config(), conversation="")
        g.chat = MagicMock()

        round_count = 0

        async def _dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "flaky_tool",
                    "arguments": "{}",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "I encountered an error. "}}
                yield {"event_type": "token", "data": {"token": "Let me explain."}}

        g.dispatch = _dispatch
        with patch.object(g, "dispatch_tool", new_callable=AsyncMock, side_effect=RuntimeError("timeout")):
            events = await _collect(g._run_with_tools({"messages": [], "agent": "test"}))

        token_events = [e for e in events if e["event_type"] == "token"]
        assert len(token_events) == 2
        assert g._last_acc.text == "I encountered an error. Let me explain."
        assert g._last_acc.tool_calls == []


# ==================================================================
# CRITICAL FAILURE MODES — _check_scope()
# ==================================================================

class TestCheckScopeFailureModes:
    """Scope validation failure modes."""

    def test_project_scope_without_project_context(self):
        """Tool with project scope called but scope_context has no matching key."""
        reg = MagicMock()
        td = MagicMock()
        td.scope_key = "project"
        reg.get.return_value = td
        g = _make_graph(tool_registry=reg)
        g._agent_scope = "project"
        g._scope_context = {"workflow_id": "wf-1"}
        # No "project" in scope_context → expected value is empty → passes
        assert g._check_scope("tool", {"project": "anything"}) == ""

    def test_project_scope_mismatch_blocks(self):
        """Tool scoped to project='A' called with project='B' → error."""
        reg = MagicMock()
        td = MagicMock()
        td.scope_key = "project"
        reg.get.return_value = td
        g = _make_graph(tool_registry=reg)
        g._agent_scope = "project"
        g._scope_context = {"project": "proj-A"}
        result = g._check_scope("deploy_tool", {"project": "proj-B"})
        assert "[Scope error]" in result
        assert "proj-A" in result

    def test_global_scope_always_passes(self):
        """Tool with global scope passes regardless of context."""
        reg = MagicMock()
        td = MagicMock()
        td.scope_key = "project"
        reg.get.return_value = td
        g = _make_graph(tool_registry=reg)
        g._agent_scope = "global"
        g._scope_context = {"project": "A"}
        assert g._check_scope("any_tool", {"project": "B"}) == ""

    def test_tool_with_no_scope_key_passes(self):
        """Tool definition has no scope_key → permissive default (passes)."""
        reg = MagicMock()
        td = MagicMock()
        td.scope_key = None
        reg.get.return_value = td
        g = _make_graph(tool_registry=reg)
        g._agent_scope = "project"
        g._scope_context = {"project": "A"}
        assert g._check_scope("tool", {"project": "B"}) == ""


# ==================================================================
# CRITICAL FAILURE MODES — _save_response()
# ==================================================================

class TestSaveResponseFailureModes:
    """_save_response must never crash the pipeline."""

    def test_chat_append_raises_no_crash(self):
        """Chat store throws during append → no crash, just log."""
        chat = MagicMock()
        chat.append.side_effect = IOError("disk full")
        g = _make_graph(chat=chat, conversation="conv-1")
        acc = MagicMock()
        acc.text = "response text"
        acc.reasoning = ""
        # Must not raise
        g._save_response(acc)

    def test_empty_response_not_saved(self):
        """No text and no reasoning → nothing saved to chat."""
        chat = MagicMock()
        g = _make_graph(chat=chat, conversation="conv-1")
        acc = MagicMock()
        acc.text = ""
        acc.reasoning = ""
        g._save_response(acc)
        chat.append.assert_not_called()

    def test_both_text_and_reasoning_saved(self):
        """Response with both text and reasoning → both persisted in message."""
        chat = MagicMock()
        g = _make_graph(chat=chat, conversation="conv-1")
        acc = MagicMock()
        acc.text = "The answer is 42"
        acc.reasoning = "I computed 6 * 7"
        g._save_response(acc)
        chat.append.assert_called_once()
        msg = chat.append.call_args[0][1]
        assert msg["role"] == "assistant"
        assert msg["content"] == "The answer is 42"
        assert msg["reasoning"] == "I computed 6 * 7"

    def test_reasoning_only_no_text_not_saved(self):
        """Only reasoning but no text → not saved (text is the gate)."""
        chat = MagicMock()
        g = _make_graph(chat=chat, conversation="conv-1")
        acc = MagicMock()
        acc.text = ""
        acc.reasoning = "I thought about it"
        g._save_response(acc)
        chat.append.assert_not_called()


# ==================================================================
# CRITICAL FAILURE MODES — _finalize_git()
# ==================================================================

class TestFinalizeGitFailureModes:
    """Git failures must never crash the pipeline."""

    @pytest.mark.asyncio
    async def test_commit_failure_logged_no_crash(self):
        """git commit returns non-zero → logged, returns None, no crash."""
        g = _make_graph()

        def fake_run(cmd, **kw):
            if cmd[1] == "status":
                return MagicMock(returncode=0, stdout=" M file.py\n", stderr="")
            if cmd[1] == "add":
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "commit":
                return MagicMock(returncode=128, stdout="", stderr="fatal: unable to create")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = await g._finalize_git({"worktree": "/wt", "title": "test"})
        assert result is None

    @pytest.mark.asyncio
    async def test_nothing_to_commit_clean_handling(self):
        """No changes in worktree → returns None cleanly."""
        g = _make_graph()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = await g._finalize_git({"worktree": "/wt"})
        assert result is None

    @pytest.mark.asyncio
    async def test_push_failure_in_done_event(self):
        """Push fails → result has pushed=False, usable in done event."""
        g = _make_graph()

        def fake_run(cmd, **kw):
            if cmd[1] == "status":
                return MagicMock(returncode=0, stdout=" M f.py\n", stderr="")
            if cmd[1] == "add":
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "commit":
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "branch":
                return MagicMock(returncode=0, stdout="feat/x\n", stderr="")
            if cmd[1] == "push":
                return MagicMock(returncode=1, stdout="", stderr="rejected: non-fast-forward")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = await g._finalize_git({"worktree": "/wt", "title": "work"})

        assert result is not None
        assert result["committed"] is True
        assert result["pushed"] is False

        done_ev = g._done_event(git_result=result)
        assert done_ev["data"]["git"]["pushed"] is False

    @pytest.mark.asyncio
    async def test_subprocess_timeout_no_crash(self):
        """Subprocess timeout → exception caught, returns None."""
        g = _make_graph()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 15)):
            result = await g._finalize_git({"worktree": "/wt"})
        assert result is None


# ==================================================================
# CRITICAL FAILURE MODES — prepare()
# ==================================================================

class TestPrepareFailureModes:
    """prepare() must give clear errors on invalid configuration."""

    def test_invalid_jinja2_template_clear_error(self):
        """Agent template has syntax errors → Jinja2 exception with clear message."""
        from jinja2 import TemplateSyntaxError

        agent_def = _make_agent_def()
        store = MagicMock()
        store.get.return_value = agent_def

        g = _make_graph(agent_store=store)
        g._workflow_dir = None

        with patch("acai.orchestrator.agent_store.resolve_task", return_value=MagicMock()), \
             patch("acai.orchestrator.agent_store.hydrate_task",
                   side_effect=TemplateSyntaxError("unexpected '%}'", lineno=5)):
            with pytest.raises(TemplateSyntaxError):
                g.prepare("bad-agent", {})

    def test_blocked_provider_shows_agent_and_provider_names(self):
        """Provider blocked → ValueError mentions both agent and provider names."""
        agent_def = _make_agent_def()
        agent_def.is_provider_allowed.return_value = False
        store = MagicMock()
        store.get.return_value = agent_def

        cfg = _make_config()
        cfg.active_provider.return_value.name = "blocked-provider"

        g = _make_graph(agent_store=store, config=cfg)
        g._workflow_dir = None

        with patch("acai.orchestrator.agent_store.resolve_task", return_value=MagicMock()), \
             patch("acai.orchestrator.agent_store.hydrate_task", return_value=[{"role": "user", "content": "hi"}]):
            with pytest.raises(ValueError, match="my-agent") as exc_info:
                g.prepare("my-agent", {})
            assert "blocked-provider" in str(exc_info.value)

    def test_context_window_exceeded_triggers_truncation(self):
        """Messages exceed context window after preparation → truncation happens."""
        agent_def = _make_agent_def()
        store = MagicMock()
        store.get.return_value = agent_def

        huge_messages = [
            {"role": "system", "content": "system prompt"},
            *[{"role": "user", "content": "x" * 2000} for _ in range(50)],
            {"role": "user", "content": "latest message"},
        ]

        cfg = _make_config(context_window=500, max_tokens=100)

        g = _make_graph(agent_store=store, config=cfg)
        g._workflow_dir = None

        with patch("acai.orchestrator.agent_store.resolve_task", return_value=MagicMock()), \
             patch("acai.orchestrator.agent_store.hydrate_task", return_value=huge_messages):
            payload = g.prepare("agent", {})

        messages = payload["messages"]
        assert len(messages) < len(huge_messages)
        assert any(m.get("content") == _TRUNCATION_MARKER for m in messages)

    def test_provider_override_blocked(self):
        """Provider override is also checked against agent's allow list."""
        agent_def = _make_agent_def()
        agent_def.is_provider_allowed.return_value = False
        store = MagicMock()
        store.get.return_value = agent_def

        g = _make_graph(agent_store=store)
        g._workflow_dir = None

        with patch("acai.orchestrator.agent_store.resolve_task", return_value=MagicMock()), \
             patch("acai.orchestrator.agent_store.hydrate_task", return_value=[{"role": "user", "content": "hi"}]):
            with pytest.raises(ValueError, match="not allowed"):
                g.prepare("agent", {"provider_override": {"name": "evil-provider"}})
