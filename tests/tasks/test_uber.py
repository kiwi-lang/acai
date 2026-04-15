"""Tests for assai.tasks.uber — UberRouter."""

from __future__ import annotations

import json
import pytest

from assai.tasks.uber import UberRouter


@pytest.mark.asyncio
class TestUberRouter:

    async def test_route_new_conversation(
        self, load_balancer, chat_store, agent_store, assai_config,
    ):
        """With no existing conversations the router creates a new one."""
        router = UberRouter(chat=chat_store, agent_store=agent_store, config=assai_config)

        async with load_balancer.acquire() as worker:
            result = await router.route(
                worker, message="Hello world", agent="default",
            )

        assert "conversation" in result
        assert result["is_new"] is True
        meta = chat_store.get_meta(result["conversation"])
        assert meta is not None

    async def test_route_existing_conversation(
        self, load_balancer, chat_store, agent_store, assai_config,
        worker_base_url,
    ):
        """When the LLM returns an existing conv id, it is used."""
        conv = chat_store.create(title="existing topic")

        router = UberRouter(chat=chat_store, agent_store=agent_store, config=assai_config)

        async with load_balancer.acquire() as worker:
            result = await router.route(
                worker,
                message="continue the existing topic",
                current_conv_id=conv.id,
                agent="default",
            )

        assert "conversation" in result
        assert isinstance(result["is_new"], bool)

    async def test_route_parse_fallback(
        self, chat_store, agent_store, assai_config,
    ):
        """Malformed LLM output falls back to a new conversation."""
        router = UberRouter(chat=chat_store, agent_store=agent_store, config=assai_config)

        catalogue = router._build_catalogue()
        decision = router._parse_routing_result(
            "this is not json at all", catalogue, "test message",
        )
        assert decision["id"] == "new"
        assert "title" in decision

    async def test_route_parse_existing_id(
        self, chat_store, agent_store, assai_config,
    ):
        """Parser correctly extracts an existing conversation id."""
        conv = chat_store.create(title="my topic")

        router = UberRouter(chat=chat_store, agent_store=agent_store, config=assai_config)
        catalogue = router._build_catalogue()

        decision = router._parse_routing_result(
            json.dumps({"id": conv.id}), catalogue, "test message",
        )
        assert decision["id"] == conv.id

    async def test_route_parse_new(
        self, chat_store, agent_store, assai_config,
    ):
        """Parser correctly handles a 'new' decision."""
        router = UberRouter(chat=chat_store, agent_store=agent_store, config=assai_config)
        catalogue = router._build_catalogue()

        decision = router._parse_routing_result(
            json.dumps({"id": "new", "title": "Fresh topic", "tags": ["a", "b"]}),
            catalogue,
            "test message",
        )
        assert decision["id"] == "new"
        assert decision["title"] == "Fresh topic"
        assert decision["tags"] == ["a", "b"]

    async def test_create_new_sets_meta(
        self, chat_store, agent_store, assai_config,
    ):
        """_create_new persists metadata correctly."""
        router = UberRouter(chat=chat_store, agent_store=agent_store, config=assai_config)
        result = router._create_new("hello world", agent="default", title="My Title", tags=["x"])

        assert result["is_new"] is True
        meta = chat_store.get_meta(result["conversation"])
        assert meta is not None
        assert meta["title"] == "My Title"
        assert "x" in meta.get("tags", [])

    async def test_build_catalogue(
        self, chat_store, agent_store, assai_config,
    ):
        """Catalogue includes all conversations."""
        chat_store.create(title="alpha")
        chat_store.create(title="beta")

        router = UberRouter(chat=chat_store, agent_store=agent_store, config=assai_config)
        catalogue = router._build_catalogue()

        titles = {c["title"] for c in catalogue}
        assert "alpha" in titles
        assert "beta" in titles
