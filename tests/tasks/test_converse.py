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
