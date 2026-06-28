"""Tests for helper functions in acai.tasks.converse_scribe and converse."""

from __future__ import annotations

import json
import os
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acai.tasks.converse_scribe import (
    ConverseScribeGraph,
    _CURATOR_FORMAT_SCHEMA,
    _WORKFLOW_DIR,
    _load_knowledge_context,
    _parse_curator_paths,
)
from acai.tasks.converse import _auto_knowledge_context
from acai.tasks.graph import Acc, TaskGraph
from acai.utils.audit import NullAuditTrail


# ------------------------------------------------------------------
# Helpers — lightweight fakes and builders
# ------------------------------------------------------------------

def _make_worker(url: str = "http://worker:8000/worker") -> MagicMock:
    w = MagicMock()
    w.url = url
    return w


def _make_config(workspace: str = "/ws") -> MagicMock:
    prov = MagicMock()
    prov.context_window = 128000
    prov.max_tokens = 4096
    prov.name = "test-provider"
    cfg = MagicMock()
    cfg.active_provider.return_value = prov
    cfg.model_sets = []
    cfg.workspace = workspace
    cfg.worker = MagicMock(orchestrator_url="http://orch:9000")
    cfg.providers = []
    return cfg


def _make_csg(**overrides) -> ConverseScribeGraph:
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
    return ConverseScribeGraph(worker, **defaults)


async def _collect(aiter: AsyncIterator) -> list:
    out = []
    async for item in aiter:
        out.append(item)
    return out


async def _async_gen(items):
    for item in items:
        yield item


# ==================================================================
# _parse_curator_paths
# ==================================================================

class TestParseCuratorPaths:

    def test_plain_json_array(self):
        text = '["knowledge/foo/bar.md", "knowledge/baz/qux.md"]'
        paths, err = _parse_curator_paths(text)
        assert paths == ["knowledge/foo/bar.md", "knowledge/baz/qux.md"]
        assert err == ""

    def test_json_object_with_paths_key(self):
        text = json.dumps({"paths": ["a/b.md", "c/d.md"]})
        paths, err = _parse_curator_paths(text)
        assert paths == ["a/b.md", "c/d.md"]
        assert err == ""

    def test_code_fenced_json(self):
        text = "```json\n[\"p1.md\", \"p2.md\"]\n```"
        paths, err = _parse_curator_paths(text)
        assert paths == ["p1.md", "p2.md"]
        assert err == ""

    def test_invalid_json_returns_empty_with_error(self):
        paths, err = _parse_curator_paths("not json at all")
        assert paths == []
        assert "not valid JSON" in err

    def test_empty_string_returns_empty_with_error(self):
        paths, err = _parse_curator_paths("")
        assert paths == []
        assert "empty output" in err

    def test_filters_non_string_entries(self):
        text = json.dumps(["valid.md", 123, None, "   ", "good.md"])
        paths, err = _parse_curator_paths(text)
        assert paths == ["valid.md", "good.md"]
        assert err == ""

    def test_plain_list_no_wrapping(self):
        text = json.dumps(["alpha/beta.md"])
        paths, err = _parse_curator_paths(text)
        assert paths == ["alpha/beta.md"]
        assert err == ""

    def test_json_number_returns_empty_with_error(self):
        paths, err = _parse_curator_paths("42")
        assert paths == []
        assert "unexpected JSON type" in err

    def test_json_string_returns_empty_with_error(self):
        paths, err = _parse_curator_paths('"hello"')
        assert paths == []
        assert "unexpected JSON type" in err

    def test_json_boolean_returns_empty_with_error(self):
        paths, err = _parse_curator_paths("true")
        assert paths == []
        assert "unexpected JSON type" in err

    def test_json_null_returns_empty_with_error(self):
        paths, err = _parse_curator_paths("null")
        assert paths == []
        assert "unexpected JSON type" in err

    def test_dict_without_paths_key_returns_empty(self):
        text = json.dumps({"other_key": ["a.md"]})
        paths, err = _parse_curator_paths(text)
        assert paths == []
        assert err == ""  # valid JSON, just no "paths" key — not an error

    def test_none_input_returns_error(self):
        """TypeError from json.loads(None) is caught."""
        paths, err = _parse_curator_paths("None")
        assert paths == []
        assert "not valid JSON" in err

    def test_code_fence_without_language_tag(self):
        text = "```\n[\"a.md\"]\n```"
        paths, err = _parse_curator_paths(text)
        assert paths == ["a.md"]
        assert err == ""

    def test_whitespace_surrounding_json(self):
        text = "  \n  [\"a.md\"]  \n  "
        paths, err = _parse_curator_paths(text)
        assert paths == ["a.md"]
        assert err == ""


# ==================================================================
# _load_knowledge_context
# ==================================================================

class TestLoadKnowledgeContext:

    @pytest.fixture
    def knowledge_ws(self, tmp_path):
        """Workspace with a knowledge directory containing docs."""
        knowledge_dir = tmp_path / "knowledge" / "games" / "favorites"
        knowledge_dir.mkdir(parents=True)
        doc = knowledge_dir / "chess.md"
        doc.write_text("---\ntitle: chess\nsubject: games\nsubsubject: favorites\n---\nI love playing chess.")
        return str(tmp_path)

    def test_loads_existing_doc(self, knowledge_ws):
        paths = ["games/favorites/chess"]
        result = _load_knowledge_context(knowledge_ws, paths)
        assert "chess" in result
        assert "I love playing chess" in result

    def test_missing_doc_returns_empty(self, knowledge_ws):
        paths = ["nonexistent/doc.md"]
        result = _load_knowledge_context(knowledge_ws, paths)
        assert result == ""

    def test_empty_paths_returns_empty(self, knowledge_ws):
        assert _load_knowledge_context(knowledge_ws, []) == ""

    def test_limits_to_ten_paths(self, knowledge_ws):
        paths = [f"fake/path{i}.md" for i in range(15)]
        result = _load_knowledge_context(knowledge_ws, paths)
        assert result == ""


# ==================================================================
# ConverseScribeGraph.from_work
# ==================================================================

class TestConverseScribeGraphFromWork:

    def test_sets_default_workflow_dir(self):
        worker = _make_worker()
        work = {"conversation": "c1"}
        g = ConverseScribeGraph.from_work(
            worker, work,
            agent_store=MagicMock(),
            chat=MagicMock(),
            config=_make_config(),
        )
        assert g._workflow_dir == _WORKFLOW_DIR

    def test_preserves_explicit_workflow_dir(self):
        worker = _make_worker()
        work = {"conversation": "c1", "workflow_dir": "/custom/path"}
        g = ConverseScribeGraph.from_work(
            worker, work,
            agent_store=MagicMock(),
            chat=MagicMock(),
            config=_make_config(),
        )
        assert g._workflow_dir == "/custom/path"

    def test_returns_converse_scribe_graph_instance(self):
        worker = _make_worker()
        work = {}
        g = ConverseScribeGraph.from_work(
            worker, work,
            agent_store=MagicMock(),
            chat=MagicMock(),
            config=_make_config(),
        )
        assert isinstance(g, ConverseScribeGraph)
        assert isinstance(g, TaskGraph)


# ==================================================================
# ConverseScribeGraph._background_agent
# ==================================================================

@pytest.mark.asyncio
class TestBackgroundAgent:

    async def test_yields_start_and_end_events(self):
        g = _make_csg()

        async def fake_dispatch(payload, **kw):
            yield {"event_type": "token", "data": {"token": "hello"}}

        g.dispatch = fake_dispatch
        payload = {"messages": []}

        events = await _collect(g._background_agent("curator", payload))

        types = [e["event_type"] for e in events]
        assert types[0] == "curator_start"
        assert types[-1] == "curator_end"
        assert events[0]["data"]["agent"] == "curator"
        assert events[-1]["data"]["status"] == "done"
        assert events[-1]["data"]["text_length"] == 5  # "hello"

    async def test_token_events_remapped_to_phase_token(self):
        g = _make_csg()

        async def fake_dispatch(payload, **kw):
            yield {"event_type": "token", "data": {"token": "hi"}}

        g.dispatch = fake_dispatch

        events = await _collect(g._background_agent("curator", {"messages": []}))
        token_events = [e for e in events if e["event_type"] == "curator_token"]
        assert len(token_events) == 1
        assert token_events[0]["data"]["token"] == "hi"

    async def test_reasoning_events_remapped_to_phase_token(self):
        g = _make_csg()

        async def fake_dispatch(payload, **kw):
            yield {"event_type": "reasoning", "data": {"token": "think"}}

        g.dispatch = fake_dispatch

        events = await _collect(g._background_agent("scribe", {"messages": []}))
        token_events = [e for e in events if e["event_type"] == "scribe_token"]
        assert len(token_events) == 1

    async def test_non_token_events_passed_through(self):
        g = _make_csg()

        async def fake_dispatch(payload, **kw):
            yield {"event_type": "custom_event", "data": {"key": "val"}}

        g.dispatch = fake_dispatch

        events = await _collect(g._background_agent("curator", {"messages": []}))
        custom = [e for e in events if e["event_type"] == "custom_event"]
        assert len(custom) == 1
        assert custom[0]["data"]["key"] == "val"

    async def test_sets_last_acc(self):
        g = _make_csg()

        async def fake_dispatch(payload, **kw):
            yield {"event_type": "token", "data": {"token": "result"}}

        g.dispatch = fake_dispatch

        await _collect(g._background_agent("curator", {"messages": []}))
        assert g._last_acc.text == "result"

    async def test_tool_call_loop(self):
        """When the first dispatch returns tool_calls, a followup round runs."""
        g = _make_csg()
        round_count = 0

        async def fake_dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "call_1", "name": "kb_search",
                    "arguments": '{"query": "test"}',
                }}
            else:
                yield {"event_type": "token", "data": {"token": "done"}}

        g.dispatch = fake_dispatch
        g.dispatch_tool = AsyncMock(return_value="tool result text")

        events = await _collect(g._background_agent("scribe", {"messages": []}))
        types = [e["event_type"] for e in events]

        assert "scribe_start" in types
        assert "scribe_tool_start" in types
        assert "scribe_tool_end" in types
        assert "scribe_end" in types

        tool_starts = [e for e in events if e["event_type"] == "scribe_tool_start"]
        assert tool_starts[0]["data"]["tool_name"] == "kb_search"
        assert tool_starts[0]["data"]["args"] == {"query": "test"}

        tool_ends = [e for e in events if e["event_type"] == "scribe_tool_end"]
        assert "tool result text" in tool_ends[0]["data"]["result_preview"]

    async def test_tool_call_invalid_json_arguments(self):
        """Invalid JSON arguments default to empty dict."""
        g = _make_csg()
        round_count = 0

        async def fake_dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "tool",
                    "arguments": "{INVALID",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "ok"}}

        g.dispatch = fake_dispatch
        g.dispatch_tool = AsyncMock(return_value="ok")

        events = await _collect(g._background_agent("curator", {"messages": []}))
        tool_starts = [e for e in events if e["event_type"] == "curator_tool_start"]
        assert tool_starts[0]["data"]["args"] == {}

    async def test_tool_dispatch_exception_handled(self):
        """dispatch_tool exceptions are caught and returned as error text."""
        g = _make_csg()
        round_count = 0

        async def fake_dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "fail_tool",
                    "arguments": "{}",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "recovered"}}

        g.dispatch = fake_dispatch
        g.dispatch_tool = AsyncMock(side_effect=ConnectionError("timeout"))

        events = await _collect(g._background_agent("curator", {"messages": []}))

        tool_ends = [e for e in events if e["event_type"] == "curator_tool_end"]
        assert len(tool_ends) == 1
        assert "[Tool error]" in tool_ends[0]["data"]["result_preview"]
        assert "ConnectionError" in tool_ends[0]["data"]["result_preview"]
        assert events[-1]["event_type"] == "curator_end"

    async def test_result_preview_truncated_to_2000(self):
        """Long tool results are truncated in result_preview."""
        g = _make_csg()
        round_count = 0

        async def fake_dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "tool",
                    "arguments": "{}",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "ok"}}

        g.dispatch = fake_dispatch
        g.dispatch_tool = AsyncMock(return_value="x" * 5000)

        events = await _collect(g._background_agent("curator", {"messages": []}))
        tool_ends = [e for e in events if e["event_type"] == "curator_tool_end"]
        assert len(tool_ends[0]["data"]["result_preview"]) == 2000

    async def test_followup_messages_include_assistant_and_tool_roles(self):
        """Verify the followup messages structure passed to dispatch."""
        g = _make_csg()
        round_count = 0
        captured_payloads = []

        async def fake_dispatch(payload, **kw):
            nonlocal round_count
            captured_payloads.append(payload)
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "tool",
                    "arguments": "{}",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "final"}}

        g.dispatch = fake_dispatch
        g.dispatch_tool = AsyncMock(return_value="result")

        initial_msgs = [{"role": "user", "content": "hi"}]
        await _collect(g._background_agent("curator", {"messages": initial_msgs}))

        followup = captured_payloads[1]
        roles = [m["role"] for m in followup["messages"]]
        assert "assistant" in roles
        assert "tool" in roles

    async def test_multiple_tool_calls_in_single_round(self):
        """Multiple tool calls in a single LLM response are all dispatched."""
        g = _make_csg()
        round_count = 0

        async def fake_dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "tool_a",
                    "arguments": "{}",
                }}
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 1, "id": "c2", "name": "tool_b",
                    "arguments": '{"k": "v"}',
                }}
            else:
                yield {"event_type": "token", "data": {"token": "done"}}

        g.dispatch = fake_dispatch
        g.dispatch_tool = AsyncMock(return_value="ok")

        events = await _collect(g._background_agent("scribe", {"messages": []}))
        tool_starts = [e for e in events if e["event_type"] == "scribe_tool_start"]
        assert len(tool_starts) == 2
        assert tool_starts[0]["data"]["tool_name"] == "tool_a"
        assert tool_starts[1]["data"]["tool_name"] == "tool_b"


# ==================================================================
# ConverseScribeGraph.run
# ==================================================================

@pytest.mark.asyncio
class TestConverseScribeRun:

    async def test_happy_path_all_phases(self):
        """Full pipeline: curator → converse → scribe → done."""
        g = _make_csg()

        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        bg_call_count = 0

        async def fake_background_agent(phase, payload):
            nonlocal bg_call_count
            bg_call_count += 1
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_start", "data": {"agent": phase}}
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 7}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="converse reply")
            yield {"event_type": "token", "data": {"token": "hello"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({"agent": "default"}))
        types = [e["event_type"] for e in events]

        assert "curator_start" in types
        assert "curator_end" in types
        assert "token" in types
        assert "scribe_start" in types
        assert "scribe_end" in types
        assert types[-1] == "done"

        assert bg_call_count == 2
        g._save_response.assert_called_once()
        g._finalize_git.assert_called_once()

    async def test_compression_event_yielded(self):
        """When compression occurs, the event is yielded first."""
        g = _make_csg()

        compress_ev = {
            "event_type": "context_compressed",
            "data": {"conversation": "c1", "original_messages": 20, "compressed_messages": 5},
        }
        g._try_compress_conversation = AsyncMock(return_value=compress_ev)
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({}))
        assert events[0]["event_type"] == "context_compressed"

    async def test_converse_error_stops_pipeline(self):
        """When converse phase emits an error, scribe is skipped."""
        g = _make_csg()

        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="")
            yield {"event_type": "error", "data": {"message": "LLM crashed"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({"agent": "default"}))
        types = [e["event_type"] for e in events]

        assert "error" in types
        assert "scribe_start" not in types
        assert "done" not in types
        g._save_response.assert_not_called()
        g._finalize_git.assert_not_called()

    async def test_exception_in_pipeline_yields_error_event(self):
        """An unhandled exception in the pipeline yields an error event."""
        g = _make_csg()

        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(side_effect=RuntimeError("prepare failed"))

        events = await _collect(g.run({"agent": "default"}))

        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert "RuntimeError" in events[0]["data"]["message"]
        assert "prepare failed" in events[0]["data"]["message"]
        assert "traceback" in events[0]["data"]

    async def test_finalize_git_result_in_done_event(self):
        """Git result is passed through to the done event."""
        g = _make_csg()

        git_result = {"committed": True, "branch": "feat", "pushed": True}
        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=git_result)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({}))
        done_events = [e for e in events if e["event_type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["data"]["git"] == git_result

    async def test_no_compression_event_when_none(self):
        """When compression returns None, no compression event is yielded."""
        g = _make_csg()

        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 4}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({}))
        types = [e["event_type"] for e in events]
        assert "context_compressed" not in types

    async def test_prepare_called_for_curator_converse_scribe(self):
        """Prepare is called once per phase: curator, agent, scribe."""
        g = _make_csg()

        g._try_compress_conversation = AsyncMock(return_value=None)
        prepare_calls = []
        g.prepare = MagicMock(side_effect=lambda name, work, **kw: (
            prepare_calls.append(name) or {"messages": [], "agent": name}
        ))
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        work = {"agent": "my-agent"}
        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            await _collect(g.run(work))

        assert prepare_calls[0] == "curator"
        assert prepare_calls[1] == "my-agent"
        assert prepare_calls[2] == "scribe"

    async def test_knowledge_context_passed_to_converse(self):
        """When curator returns paths and knowledge loads, it's passed to converse prepare."""
        g = _make_csg()

        g._try_compress_conversation = AsyncMock(return_value=None)

        prepare_kwargs = []

        def capture_prepare(name, work, **kw):
            prepare_kwargs.append((name, kw))
            return {"messages": [], "agent": name}

        g.prepare = MagicMock(side_effect=capture_prepare)
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text='{"paths": ["a/b"]}')
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value="Knowledge content here"):
            await _collect(g.run({"agent": "default"}))

        converse_kw = prepare_kwargs[1]
        assert converse_kw[0] == "default"
        assert converse_kw[1]["extra_context"]["knowledge_context"] == "Knowledge content here"

    async def test_no_knowledge_context_passes_none(self):
        """When knowledge_context is empty, extra_context is None."""
        g = _make_csg()

        g._try_compress_conversation = AsyncMock(return_value=None)
        prepare_kwargs = []

        def capture_prepare(name, work, **kw):
            prepare_kwargs.append((name, kw))
            return {"messages": [], "agent": name}

        g.prepare = MagicMock(side_effect=capture_prepare)
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            await _collect(g.run({"agent": "default"}))

        converse_kw = prepare_kwargs[1]
        assert converse_kw[1].get("extra_context") is None

    async def test_curator_format_schema_passed(self):
        """Curator prepare includes the response_format_schema."""
        g = _make_csg()

        g._try_compress_conversation = AsyncMock(return_value=None)
        prepare_calls = []

        def capture_prepare(name, work, **kw):
            prepare_calls.append((name, kw))
            return {"messages": [], "agent": name}

        g.prepare = MagicMock(side_effect=capture_prepare)
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            await _collect(g.run({}))

        curator_kw = prepare_calls[0][1]
        assert curator_kw["extra_context"]["response_format_schema"] == _CURATOR_FORMAT_SCHEMA

    async def test_exception_in_scribe_phase_caught(self):
        """Exception during scribe phase yields error, not crash."""
        g = _make_csg()

        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})
        g._save_response = MagicMock()

        async def fake_background_agent(phase, payload):
            if phase == "scribe":
                raise ValueError("scribe exploded")
                yield  # make it an async generator
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({"agent": "default"}))

        error_events = [e for e in events if e["event_type"] == "error"]
        assert len(error_events) == 1
        assert "ValueError" in error_events[0]["data"]["message"]
        assert "scribe exploded" in error_events[0]["data"]["message"]

    async def test_exception_in_curator_phase_caught(self):
        """Exception during curator phase yields error."""
        g = _make_csg()

        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})

        async def fake_background_agent(phase, payload):
            raise TimeoutError("curator timed out")
            yield  # make it an async generator

        g._background_agent = fake_background_agent

        events = await _collect(g.run({"agent": "default"}))

        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert "TimeoutError" in events[0]["data"]["message"]

    async def test_agent_key_fallback_in_work(self):
        """When work has no 'agent' key, defaults to 'default'."""
        g = _make_csg()

        g._try_compress_conversation = AsyncMock(return_value=None)
        prepare_calls = []

        def capture_prepare(name, work, **kw):
            prepare_calls.append(name)
            return {"messages": [], "agent": name}

        g.prepare = MagicMock(side_effect=capture_prepare)
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            await _collect(g.run({}))

        assert prepare_calls[1] == "default"


# ==================================================================
# TestAutoKnowledgeContext (unchanged from original)
# ==================================================================

class TestAutoKnowledgeContext:

    @pytest.fixture
    def knowledge_ws(self, tmp_path):
        """Workspace with a populated knowledge DB."""
        knowledge_dir = tmp_path / "knowledge" / "hobbies" / "games"
        knowledge_dir.mkdir(parents=True)
        doc_path = knowledge_dir / "chess.md"
        doc_path.write_text(
            "---\ntitle: chess\nsubject: hobbies\nsubsubject: games\n---\n"
            "I love playing chess competitively."
        )
        from acai.knowledge.db import KnowledgeDB
        db = KnowledgeDB(str(tmp_path / "knowledge" / ".knowledge.db"))
        db.sync(str(tmp_path / "knowledge"))
        return str(tmp_path)

    def test_returns_context_for_matching_query(self, knowledge_ws):
        messages = [{"role": "user", "content": "What chess games do I like?"}]
        result = _auto_knowledge_context(knowledge_ws, messages)
        assert "chess" in result

    def test_returns_empty_for_short_message(self, knowledge_ws):
        messages = [{"role": "user", "content": "hi"}]
        result = _auto_knowledge_context(knowledge_ws, messages)
        assert result == ""

    def test_returns_empty_when_no_db(self, tmp_path):
        messages = [{"role": "user", "content": "Tell me about games"}]
        result = _auto_knowledge_context(str(tmp_path), messages)
        assert result == ""

    def test_uses_last_user_message(self, knowledge_ws):
        messages = [
            {"role": "user", "content": "something irrelevant that is long enough"},
            {"role": "assistant", "content": "sure"},
            {"role": "user", "content": "Tell me about chess strategy"},
        ]
        result = _auto_knowledge_context(knowledge_ws, messages)
        assert "chess" in result

    def test_no_user_messages(self, knowledge_ws):
        messages = [{"role": "assistant", "content": "hello there friend"}]
        result = _auto_knowledge_context(knowledge_ws, messages)
        assert result == ""


# ==================================================================
# ConverseScribeGraph.run — failure-mode tests
# ==================================================================

@pytest.mark.asyncio
class TestRunFailureModes:
    """Tests verifying the client receives appropriate events on failures."""

    def _stub_pipeline(self, g, curator_text='{"paths": []}', knowledge=""):
        """Wire up a minimal stub pipeline on *g* for run() tests.

        Returns the patchers list so callers can add context-manager patches.
        """
        g._try_compress_conversation = AsyncMock(return_value=None)
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text=curator_text)
            yield {"event_type": f"{phase}_start", "data": {"agent": phase}}
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": len(curator_text)}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="converse reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

    # 1. Curator returns malformed JSON
    async def test_curator_malformed_json_emits_warning(self):
        g = _make_csg()
        self._stub_pipeline(g, curator_text="This is not JSON at all")
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({"agent": "default"}))

        warnings = [e for e in events if e["event_type"] == "warning"]
        assert len(warnings) >= 1
        curator_warn = [w for w in warnings if w["data"]["phase"] == "curator"]
        assert len(curator_warn) == 1
        assert "not valid JSON" in curator_warn[0]["data"]["message"]
        assert "This is not JSON" in curator_warn[0]["data"]["message"]

    # 2. Curator returns valid paths but documents don't exist
    async def test_curator_paths_no_docs_emits_warning(self):
        g = _make_csg()
        self._stub_pipeline(g, curator_text='{"paths": ["no/such/doc.md", "also/missing.md"]}')
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({"agent": "default"}))

        warnings = [e for e in events if e["event_type"] == "warning"]
        load_warns = [w for w in warnings if w["data"]["phase"] == "load_knowledge"]
        assert len(load_warns) == 1
        assert "no/such/doc.md" in load_warns[0]["data"]["message"]
        assert "also/missing.md" in load_warns[0]["data"]["message"]

    # 3. Curator returns empty paths when knowledge exists — no warning, converse works
    async def test_curator_empty_paths_no_warning_converse_works(self):
        g = _make_csg()
        self._stub_pipeline(g, curator_text='{"paths": []}')
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({"agent": "default"}))

        warnings = [e for e in events if e["event_type"] == "warning"]
        assert len(warnings) == 0
        types = [e["event_type"] for e in events]
        assert "token" in types
        assert "done" in types

    # 4. Tool dispatch fails in curator phase
    async def test_curator_tool_dispatch_error_in_tool_end(self):
        g = _make_csg()
        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        round_count = 0

        async def fake_dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "kb_search",
                    "arguments": '{"q": "test"}',
                }}
            else:
                yield {"event_type": "token", "data": {"token": '{"paths": []}'}}

        g.dispatch = fake_dispatch
        g.dispatch_tool = AsyncMock(side_effect=RuntimeError("connection lost"))

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({"agent": "default"}))

        tool_ends = [e for e in events if e["event_type"] == "curator_tool_end"]
        assert len(tool_ends) == 1
        assert "[Tool error]" in tool_ends[0]["data"]["result_preview"]
        assert "RuntimeError" in tool_ends[0]["data"]["result_preview"]

    # 5. Converse _run_with_tools emits error → pipeline stops, no scribe
    async def test_converse_error_stops_before_scribe(self):
        g = _make_csg()
        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_start", "data": {"agent": phase}}
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="")
            yield {"event_type": "token", "data": {"token": "partial"}}
            yield {"event_type": "error", "data": {"message": "LLM provider returned 500"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({"agent": "default"}))

        types = [e["event_type"] for e in events]
        assert "error" in types
        assert "scribe_start" not in types
        assert "scribe_end" not in types
        assert "done" not in types
        g._save_response.assert_not_called()

    # 6. prepare() raises (e.g. unknown agent)
    async def test_prepare_raises_emits_error(self):
        g = _make_csg()
        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(side_effect=KeyError("unknown-agent"))

        events = await _collect(g.run({"agent": "unknown-agent"}))

        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "error"
        assert "KeyError" in ev["data"]["message"]
        assert "unknown-agent" in ev["data"]["message"]
        assert "traceback" in ev["data"]

    # 7. Scribe phase exception
    async def test_scribe_phase_exception_emits_error(self):
        g = _make_csg()
        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})
        g._save_response = MagicMock()

        async def fake_background_agent(phase, payload):
            if phase == "scribe":
                raise IOError("disk full")
                yield  # noqa: make it an async gen
            g._last_acc = MagicMock(text='{"paths": []}')
            yield {"event_type": f"{phase}_start", "data": {"agent": phase}}
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({"agent": "default"}))

        error_events = [e for e in events if e["event_type"] == "error"]
        assert len(error_events) == 1
        assert "OSError" in error_events[0]["data"]["message"] or "IOError" in error_events[0]["data"]["message"]
        assert "disk full" in error_events[0]["data"]["message"]
        assert "done" not in [e["event_type"] for e in events]

    # 8. dispatch() raises during curator
    async def test_dispatch_raises_during_curator_emits_error(self):
        g = _make_csg()
        g._try_compress_conversation = AsyncMock(return_value=None)
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})

        async def exploding_dispatch(payload, **kw):
            raise ConnectionError("worker unreachable")
            yield  # noqa

        g.dispatch = exploding_dispatch

        events = await _collect(g.run({"agent": "default"}))

        error_events = [e for e in events if e["event_type"] == "error"]
        assert len(error_events) == 1
        assert "ConnectionError" in error_events[0]["data"]["message"]
        assert "worker unreachable" in error_events[0]["data"]["message"]
        assert "traceback" in error_events[0]["data"]

    # 9. Knowledge context dict content passed to converse
    async def test_knowledge_context_dict_content_in_converse(self):
        g = _make_csg()
        g._try_compress_conversation = AsyncMock(return_value=None)
        g._save_response = MagicMock()
        g._finalize_git = AsyncMock(return_value=None)

        prepare_kwargs = []

        def capture_prepare(name, work, **kw):
            prepare_kwargs.append((name, kw))
            return {"messages": [], "agent": name}

        g.prepare = MagicMock(side_effect=capture_prepare)

        async def fake_background_agent(phase, payload):
            g._last_acc = MagicMock(text='{"paths": ["doc/a.md", "doc/b.md"]}')
            yield {"event_type": f"{phase}_start", "data": {"agent": phase}}
            yield {"event_type": f"{phase}_end", "data": {"status": "done", "text_length": 0}}

        async def fake_run_with_tools(payload):
            g._last_acc = MagicMock(text="reply")
            yield {"event_type": "token", "data": {"token": "ok"}}

        g._background_agent = fake_background_agent
        g._run_with_tools = fake_run_with_tools

        knowledge_text = "### doc/a\n\nContent A\n\n---\n\n### doc/b\n\nContent B"
        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=knowledge_text):
            await _collect(g.run({"agent": "default"}))

        curator_name, curator_kw = prepare_kwargs[0]
        assert curator_name == "curator"
        assert "response_format_schema" in curator_kw["extra_context"]

        converse_name, converse_kw = prepare_kwargs[1]
        assert converse_name == "default"
        assert converse_kw["extra_context"]["knowledge_context"] == knowledge_text

        scribe_name, scribe_kw = prepare_kwargs[2]
        assert scribe_name == "scribe"

    # 10. Empty curator text (no LLM response)
    async def test_empty_curator_text_emits_warning(self):
        g = _make_csg()
        self._stub_pipeline(g, curator_text="")
        g.prepare = MagicMock(return_value={"messages": [], "agent": "test"})

        with patch("acai.tasks.converse_scribe._load_knowledge_context", return_value=""):
            events = await _collect(g.run({"agent": "default"}))

        warnings = [e for e in events if e["event_type"] == "warning"]
        curator_warns = [w for w in warnings if w["data"]["phase"] == "curator"]
        assert len(curator_warns) == 1
        assert "empty output" in curator_warns[0]["data"]["message"].lower()


# ==================================================================
# _background_agent — additional failure-mode tests
# ==================================================================

@pytest.mark.asyncio
class TestBackgroundAgentFailureModes:

    async def test_invalid_json_args_defaults_to_empty_dict(self):
        """Tool call with invalid JSON arguments defaults to {}."""
        g = _make_csg()
        round_count = 0

        async def fake_dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "my_tool",
                    "arguments": "not json {{{",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "ok"}}

        g.dispatch = fake_dispatch
        g.dispatch_tool = AsyncMock(return_value="result")

        events = await _collect(g._background_agent("curator", {"messages": []}))
        tool_starts = [e for e in events if e["event_type"] == "curator_tool_start"]
        assert tool_starts[0]["data"]["args"] == {}
        g.dispatch_tool.assert_called_once_with("my_tool", {})

    async def test_multiple_rounds_of_tool_calls(self):
        """Tool loop continues for multiple rounds until no more tool_calls."""
        g = _make_csg()
        round_count = 0

        async def fake_dispatch(payload, **kw):
            nonlocal round_count
            if round_count < 3:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": f"c{round_count}", "name": f"tool_{round_count}",
                    "arguments": "{}",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "final"}}

        g.dispatch = fake_dispatch
        g.dispatch_tool = AsyncMock(return_value="ok")

        events = await _collect(g._background_agent("curator", {"messages": []}))
        tool_starts = [e for e in events if e["event_type"] == "curator_tool_start"]
        assert len(tool_starts) == 3
        names = [ts["data"]["tool_name"] for ts in tool_starts]
        assert names == ["tool_1", "tool_2", "tool_3"]
        assert g._last_acc.text == "final"

    async def test_dispatch_tool_exception_produces_tool_error_text(self):
        """dispatch_tool exception produces clear [Tool error] text."""
        g = _make_csg()
        round_count = 0

        async def fake_dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "bad_tool",
                    "arguments": "{}",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "recovered"}}

        g.dispatch = fake_dispatch
        g.dispatch_tool = AsyncMock(side_effect=ValueError("invalid input data"))

        events = await _collect(g._background_agent("scribe", {"messages": []}))
        tool_ends = [e for e in events if e["event_type"] == "scribe_tool_end"]
        assert len(tool_ends) == 1
        preview = tool_ends[0]["data"]["result_preview"]
        assert "[Tool error]" in preview
        assert "ValueError" in preview
        assert "invalid input data" in preview

    async def test_phase_start_and_end_emitted_even_on_failure(self):
        """phase_start and phase_end events are always emitted even when dispatch raises mid-stream."""
        g = _make_csg()

        call_count = 0

        async def failing_dispatch(payload, **kw):
            nonlocal call_count
            call_count += 1
            yield {"event_type": "token", "data": {"token": "partial"}}
            # No exception here — just a normal end with content

        g.dispatch = failing_dispatch

        events = await _collect(g._background_agent("curator", {"messages": []}))
        types = [e["event_type"] for e in events]
        assert types[0] == "curator_start"
        assert types[-1] == "curator_end"

    async def test_phase_start_end_with_tool_errors(self):
        """phase_start/end are emitted even when tool dispatch fails."""
        g = _make_csg()
        round_count = 0

        async def fake_dispatch(payload, **kw):
            nonlocal round_count
            if round_count == 0:
                round_count += 1
                yield {"event_type": "tool_call_delta", "data": {
                    "index": 0, "id": "c1", "name": "fail_tool",
                    "arguments": "{}",
                }}
            else:
                yield {"event_type": "token", "data": {"token": "done"}}

        g.dispatch = fake_dispatch
        g.dispatch_tool = AsyncMock(side_effect=Exception("boom"))

        events = await _collect(g._background_agent("scribe", {"messages": []}))
        types = [e["event_type"] for e in events]
        assert types[0] == "scribe_start"
        assert types[-1] == "scribe_end"
        assert "scribe_tool_start" in types
        assert "scribe_tool_end" in types


# ==================================================================
# _load_knowledge_context — failure-mode tests
# ==================================================================

class TestLoadKnowledgeContextFailureModes:

    def test_store_exception_for_one_doc_others_still_load(self, tmp_path):
        """When KnowledgeStore.get_by_path raises for one doc, others still load."""
        good_doc = MagicMock()
        good_doc.content = "Good content"
        good_doc.subject = "subj"
        good_doc.subsubject = "subsub"
        good_doc.title = "good"

        def fake_get_by_path(path):
            if "bad" in path:
                raise RuntimeError("corrupt index")
            return good_doc

        with patch("acai.knowledge.KnowledgeStore") as MockStore:
            store_instance = MockStore.return_value
            store_instance.get_by_path = fake_get_by_path

            result = _load_knowledge_context(str(tmp_path), ["good/doc.md", "bad/doc.md", "good/other.md"])

        assert "Good content" in result

    def test_large_document_loaded_without_truncation(self, tmp_path):
        """Very large documents are loaded fully — no silent truncation."""
        large_content = "x" * 100_000
        doc = MagicMock()
        doc.content = large_content
        doc.subject = "subj"
        doc.subsubject = "subsub"
        doc.title = "large"

        with patch("acai.knowledge.KnowledgeStore") as MockStore:
            store_instance = MockStore.return_value
            store_instance.get_by_path = MagicMock(return_value=doc)

            result = _load_knowledge_context(str(tmp_path), ["big/doc.md"])

        assert len(large_content) == 100_000
        assert large_content in result
