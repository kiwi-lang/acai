"""Tests for assai.tasks.converse — ConverseGraph."""

from __future__ import annotations

import json

import pytest

from assai.tasks.converse import ConverseGraph


@pytest.mark.asyncio
class TestConverseGraph:

    async def test_basic_conversation(
        self, load_balancer, chat_store, graph_deps,
    ):
        conv = chat_store.create(title="test conv")
        chat_store.append(conv.id, {"role": "user", "content": "hello"})

        work = {
            "message": "hello",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
            "n_tokens": 3,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]
        assert "token" in event_types
        assert event_types[-1] == "done"

        messages = chat_store.read(conv.id)
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert "word0" in assistant_msgs[0]["content"]

    async def test_tool_follow_up(
        self, load_balancer, chat_store, graph_deps,
    ):
        conv = chat_store.create(title="tool test")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]
        assert "tool_start" in event_types
        assert "tool_end" in event_types
        assert event_types[-1] == "done"

        messages = chat_store.read(conv.id)
        roles = [m["role"] for m in messages]
        assert "tool_call" in roles
        assert "tool_result" in roles

    async def test_error_from_worker(
        self, load_balancer, chat_store, graph_deps,
    ):
        conv = chat_store.create(title="error test")
        chat_store.append(conv.id, {"role": "user", "content": "[error:LLM crashed] fail"})

        work = {
            "message": "[error:LLM crashed] fail",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]
        assert "error" in event_types

    async def test_prepare_missing_agent(
        self, load_balancer, chat_store, graph_deps,
    ):
        """When the requested agent doesn't exist, falls back to default."""
        conv = chat_store.create(title="missing agent")
        chat_store.append(conv.id, {"role": "user", "content": "hi"})

        work = {
            "message": "hi",
            "conversation": conv.id,
            "agent": "nonexistent_agent_xyz",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]
        assert "token" in event_types
        assert event_types[-1] == "done"

    async def test_tracker_receives_events(
        self, load_balancer, chat_store, graph_deps, stream_tracker,
    ):
        conv = chat_store.create(title="tracker test")
        chat_store.append(conv.id, {"role": "user", "content": "hello"})

        q = stream_tracker.subscribe(conv.id)

        work = {
            "message": "hello",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for _ in graph.run(work):
                pass

        tracked_events = []
        while not q.empty():
            tracked_events.append(q.get_nowait())
        tracked_types = [e["event_type"] for e in tracked_events]
        assert "token" in tracked_types
        assert "done" in tracked_types


@pytest.mark.asyncio
class TestToolStreamHandling:
    """Verify the exact event sequence when the LLM uses a tool.

    The mock worker returns tool_call_delta events when the user message
    contains ``[tool]``.  On the follow-up (tool results present in
    messages) it returns normal token events.

    Expected stream:
        token (initial text before tool call)
        tool_call_delta ×N (chunked tool invocation)
        tool_start (graph dispatches the tool)
        tool_end (tool result received)
        token ×3 (follow-up LLM response)
        done (graph-level termination)
    """

    async def test_tool_then_text_event_sequence(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Full event sequence: tokens → tool deltas → tool start/end → tokens → done."""
        conv = chat_store.create(title="stream sequence")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        types = [e["event_type"] for e in events]

        # Phase 1: initial LLM dispatch — token + tool_call_delta(s)
        assert types[0] == "token", f"expected initial token, got {types[0]}"
        tool_delta_indices = [i for i, t in enumerate(types) if t == "tool_call_delta"]
        assert len(tool_delta_indices) >= 1, "expected at least one tool_call_delta"

        # Phase 2: graph-level tool dispatch
        tool_start_idx = types.index("tool_start")
        tool_end_idx = types.index("tool_end")
        assert tool_start_idx > tool_delta_indices[-1], "tool_start must follow tool_call_delta"
        assert tool_end_idx > tool_start_idx, "tool_end must follow tool_start"

        # Phase 3: follow-up LLM tokens
        follow_up_tokens = [i for i, t in enumerate(types) if t == "token" and i > tool_end_idx]
        assert len(follow_up_tokens) >= 1, "expected follow-up tokens after tool_end"

        # Terminal: single done event at the end
        assert types[-1] == "done"
        assert types.count("done") == 1, "exactly one done event in the stream"

    async def test_tool_start_contains_correct_data(
        self, load_balancer, chat_store, graph_deps,
    ):
        """tool_start event carries the tool name and parsed arguments."""
        conv = chat_store.create(title="tool data check")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        starts = [e for e in events if e["event_type"] == "tool_start"]
        assert len(starts) == 1
        data = starts[0]["data"]
        assert data["tool_name"] == "shell.run"
        assert data["args"] == {"cmd": "ls"}
        assert data["conversation"] == conv.id

    async def test_tool_end_contains_result_preview(
        self, load_balancer, chat_store, graph_deps,
    ):
        """tool_end event carries a preview of the tool result."""
        conv = chat_store.create(title="tool end check")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        ends = [e for e in events if e["event_type"] == "tool_end"]
        assert len(ends) == 1
        data = ends[0]["data"]
        assert data["tool_name"] == "shell.run"
        assert "result of shell.run" in data["result_preview"]

    async def test_follow_up_text_is_persisted(
        self, load_balancer, chat_store, graph_deps,
    ):
        """After tools, the follow-up LLM response is saved to chat."""
        conv = chat_store.create(title="persist after tool")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for _ in graph.run(work):
                pass

        messages = chat_store.read(conv.id)
        roles = [m["role"] for m in messages]

        assert roles.count("tool_call") == 1
        assert roles.count("tool_result") == 1
        assert roles.count("assistant") == 1

        assistant = next(m for m in messages if m["role"] == "assistant")
        assert "word0" in assistant["content"], "follow-up tokens should be in assistant content"

    async def test_tracker_sees_full_tool_sequence(
        self, load_balancer, chat_store, graph_deps, stream_tracker,
    ):
        """StreamTracker receives the complete event sequence including tool events."""
        conv = chat_store.create(title="tracker tool")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        q = stream_tracker.subscribe(conv.id)

        work = {
            "message": "[tool] run a command",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for _ in graph.run(work):
                pass

        tracked = []
        while not q.empty():
            tracked.append(q.get_nowait())
        tracked_types = [e["event_type"] for e in tracked]

        assert "token" in tracked_types
        assert "tool_call_delta" in tracked_types
        assert "tool_start" in tracked_types
        assert "tool_end" in tracked_types
        assert "done" in tracked_types

        done_idx = tracked_types.index("done")
        assert done_idx == len(tracked_types) - 1, "done must be the last tracked event"
