"""Tests for assai.tasks.uber — UberGraph (route-only)."""

from __future__ import annotations

import json
import pytest

from assai.tasks.uber import UberGraph


@pytest.mark.asyncio
class TestUberGraphHelpers:
    """Unit tests for routing helpers (no worker needed)."""

    async def test_build_catalogue(
        self, chat_store, agent_store, assai_config,
    ):
        """Catalogue includes all conversations."""
        chat_store.create(title="alpha")
        chat_store.create(title="beta")

        graph = UberGraph.__new__(UberGraph)
        graph.chat = chat_store
        catalogue = graph._build_catalogue()

        titles = {c["title"] for c in catalogue}
        assert "alpha" in titles
        assert "beta" in titles

    async def test_parse_routing_result_new(
        self, chat_store, agent_store, assai_config,
    ):
        """Parser correctly handles a 'new' decision."""
        graph = UberGraph.__new__(UberGraph)
        graph.chat = chat_store
        catalogue = graph._build_catalogue()

        decision = graph._parse_routing_result(
            json.dumps({"id": "new", "title": "Fresh topic", "tags": ["a", "b"]}),
            catalogue,
            "test message",
        )
        assert decision["id"] == "new"
        assert decision["title"] == "Fresh topic"
        assert decision["tags"] == ["a", "b"]

    async def test_parse_routing_result_existing(
        self, chat_store, agent_store, assai_config,
    ):
        """Parser correctly extracts an existing conversation id."""
        conv = chat_store.create(title="my topic")

        graph = UberGraph.__new__(UberGraph)
        graph.chat = chat_store
        catalogue = graph._build_catalogue()

        decision = graph._parse_routing_result(
            json.dumps({"id": conv.id}), catalogue, "test message",
        )
        assert decision["id"] == conv.id

    async def test_parse_routing_result_fallback(
        self, chat_store, agent_store, assai_config,
    ):
        """Malformed LLM output falls back to a new conversation."""
        graph = UberGraph.__new__(UberGraph)
        graph.chat = chat_store
        catalogue = graph._build_catalogue()

        decision = graph._parse_routing_result(
            "this is not json at all", catalogue, "test message",
        )
        assert decision["id"] == "new"
        assert "title" in decision

    async def test_create_new_sets_meta(
        self, chat_store, agent_store, assai_config,
    ):
        """_create_new persists metadata correctly."""
        graph = UberGraph.__new__(UberGraph)
        graph.chat = chat_store

        result = graph._create_new("hello world", agent="default", title="My Title", tags=["x"])
        assert result["is_new"] is True
        meta = chat_store.get_meta(result["conversation"])
        assert meta is not None
        assert meta["title"] == "My Title"
        assert "x" in meta.get("tags", [])


@pytest.mark.asyncio
class TestUberGraphRun:
    """End-to-end tests for UberGraph.run() — route-only."""

    async def test_run_yields_route_then_done(
        self, load_balancer, chat_store, agent_store, assai_config,
        stream_tracker, graph_deps, worker_base_url,
    ):
        """run() should yield route + done, no token events."""
        work = {
            "message": "Hello world",
            "current_conversation": "",
            "agent": "default",
        }

        async with load_balancer.acquire() as worker:
            graph = UberGraph.from_work(worker, work, **graph_deps)
            events = []
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]

        assert event_types == ["route", "done"]

        route_data = events[0]["data"]
        assert "conversation" in route_data
        assert isinstance(route_data["is_new"], bool)
        assert "title" in route_data

    async def test_run_routes_to_existing_conversation(
        self, load_balancer, chat_store, agent_store, assai_config,
        stream_tracker, graph_deps, worker_base_url,
    ):
        """When existing conversations exist, routing yields route + done."""
        conv = chat_store.create(title="existing topic")

        work = {
            "message": "continue the existing topic",
            "current_conversation": conv.id,
            "agent": "default",
        }

        async with load_balancer.acquire() as worker:
            graph = UberGraph.from_work(worker, work, **graph_deps)
            events = []
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]
        assert event_types == ["route", "done"]
        assert "conversation" in events[0]["data"]

    async def test_run_does_not_append_messages(
        self, load_balancer, chat_store, agent_store, assai_config,
        stream_tracker, graph_deps, worker_base_url,
    ):
        """Route-only run() should not append any messages to chat."""
        work = {
            "message": "should not persist",
            "current_conversation": "",
            "agent": "default",
        }

        async with load_balancer.acquire() as worker:
            graph = UberGraph.from_work(worker, work, **graph_deps)
            events = []
            async for event in graph.run(work):
                events.append(event)

        route_data = events[0]["data"]
        conv_id = route_data["conversation"]
        messages = chat_store.read(conv_id)
        assert len(messages) == 0

    async def test_backward_compat_alias(self):
        """UberRouter alias should still point to UberGraph."""
        from assai.tasks.uber import UberRouter
        assert UberRouter is UberGraph
