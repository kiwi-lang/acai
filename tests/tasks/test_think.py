"""Tests for assai.tasks.think — ThinkGraph."""

from __future__ import annotations

import pytest

from assai.tasks.think import ThinkGraph


@pytest.mark.asyncio
class TestThinkGraph:

    async def test_think_then_reply(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Phase 1 produces reasoning events, phase 2 produces token events."""
        conv = chat_store.create(title="think test")
        chat_store.append(conv.id, {"role": "user", "content": "explain gravity"})

        work = {
            "message": "explain gravity",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ThinkGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]

        assert "reasoning" in event_types, "Phase 1 should produce reasoning events"
        assert "token" in event_types, "Phase 2 should produce token events"
        assert event_types[-1] == "done"

        first_reasoning = event_types.index("reasoning")
        first_token = event_types.index("token")
        assert first_reasoning < first_token, "Reasoning should come before tokens"

        messages = chat_store.read(conv.id)
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1

    async def test_think_with_tool_calls(
        self, load_balancer, chat_store, graph_deps, worker_base_url,
    ):
        """Reply phase triggers tool loop when worker returns tool calls."""
        conv = chat_store.create(title="think+tools")
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
            graph = ThinkGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]
        assert "reasoning" in event_types
        assert "tool_start" in event_types
        assert "tool_end" in event_types
        assert event_types[-1] == "done"

    async def test_reasoning_accumulated(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Acc captures reasoning text correctly across the stream."""
        from assai.tasks.graph import Acc

        conv = chat_store.create(title="acc test")
        chat_store.append(conv.id, {"role": "user", "content": "think about it"})

        work = {
            "message": "think about it",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ThinkGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        reasoning_events = [
            e for e in events if e["event_type"] == "reasoning"
        ]
        assert len(reasoning_events) > 0
        reasoning_text = "".join(
            e["data"].get("token", "") for e in reasoning_events
        )
        assert len(reasoning_text) > 0

    async def test_error_in_think_phase(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Error during the think phase is properly propagated."""
        conv = chat_store.create(title="think error")
        chat_store.append(conv.id, {"role": "user", "content": "[error:thinker exploded] fail"})

        work = {
            "message": "[error:thinker exploded] fail",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ThinkGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]
        assert "error" in event_types
        assert "done" not in event_types
