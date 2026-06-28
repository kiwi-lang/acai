"""End-to-end conversation tests — full agent pipeline against live vLLM.

These tests exercise the ConverseGraph path:
- Agent template rendering
- LLM dispatch via a real worker
- Tool call follow-up loops
- Multi-turn conversations

Prerequisites: a running vLLM instance (see conftest.py).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from tests.integrations.conftest import requires_vllm


@requires_vllm
class TestConverseGraphE2E:
    """Run the ConverseGraph against the real vLLM instance."""

    @pytest.fixture()
    def agent_store(self, workspace):
        from acai.orchestrator.agent_store import AgentStore
        agents_dir = os.path.join(workspace, "agents")
        builtin_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            os.pardir, os.pardir, "acai", "agents",
        )
        builtin_dir = os.path.normpath(builtin_dir)
        return AgentStore(agents_dir, builtin_dir)

    @pytest.fixture()
    def chat_store(self, workspace):
        from acai.orchestrator.chat import ChatStore
        return ChatStore(os.path.join(workspace, "store"))

    @pytest.fixture()
    def tool_registry(self):
        from acai.orchestrator.tools import discover_tools
        return discover_tools()

    @pytest.fixture()
    def load_balancer(self, worker_url):
        from acai.orchestrator.load_balancer import LoadBalancer
        lb = LoadBalancer()
        lb.register(worker_url)
        return lb

    @pytest.fixture()
    def projects(self, workspace):
        from acai.orchestrator.projects import ProjectStore
        return ProjectStore(os.path.join(workspace, "projects"))

    @pytest.fixture()
    def tracker(self):
        from acai.orchestrator.stream import StreamTracker
        return StreamTracker()

    @pytest.mark.asyncio
    async def test_simple_conversation(
        self, acai_config, agent_store, chat_store,
        tool_registry, load_balancer, projects, tracker,
    ):
        """Send a simple message and verify we get a response."""
        from acai.tasks.converse import ConverseGraph
        from acai.utils.audit import NullAuditTrail

        meta = chat_store.create(title="test", agent="default")
        chat_store.append(meta.id, {"role": "user", "content": "What is 2+2? Answer briefly."})

        work = {
            "message": "What is 2+2? Answer briefly.",
            "conversation": meta.id,
            "agent": "default",
            "project": "",
            "spec_path": chat_store._msg_path(meta.id),
            "stream_id": meta.id,
            "provider": "test-vllm",
            "model": acai_config.active_provider().model_slug,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(
                worker, work,
                agent_store=agent_store,
                chat=chat_store,
                config=acai_config,
                tracker=tracker,
                projects=projects,
                tool_registry=tool_registry,
                audit=NullAuditTrail(),
            )
            async for event in graph.run(work):
                events.append(event)

        event_types = [e.get("event_type") for e in events]
        assert "token" in event_types or "message" in event_types or "done" in event_types

        # Verify the response was saved to the conversation
        messages = chat_store.read(meta.id)
        assert any(m.get("role") == "assistant" for m in messages)

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(
        self, acai_config, agent_store, chat_store,
        tool_registry, load_balancer, projects, tracker,
    ):
        """Two-turn conversation maintains context."""
        from acai.tasks.converse import ConverseGraph
        from acai.utils.audit import NullAuditTrail

        meta = chat_store.create(title="multi-turn", agent="default")

        # Turn 1: establish context
        chat_store.append(meta.id, {"role": "user", "content": "My name is TestBot42. Remember it."})

        work = {
            "message": "My name is TestBot42. Remember it.",
            "conversation": meta.id,
            "agent": "default",
            "project": "",
            "spec_path": chat_store._msg_path(meta.id),
            "stream_id": meta.id,
            "provider": "test-vllm",
            "model": acai_config.active_provider().model_slug,
        }

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(
                worker, work,
                agent_store=agent_store,
                chat=chat_store,
                config=acai_config,
                tracker=tracker,
                projects=projects,
                tool_registry=tool_registry,
                audit=NullAuditTrail(),
            )
            async for _ in graph.run(work):
                pass

        # Turn 2: recall context
        chat_store.append(meta.id, {"role": "user", "content": "What is my name?"})
        work["message"] = "What is my name?"

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(
                worker, work,
                agent_store=agent_store,
                chat=chat_store,
                config=acai_config,
                tracker=tracker,
                projects=projects,
                tool_registry=tool_registry,
                audit=NullAuditTrail(),
            )
            async for event in graph.run(work):
                events.append(event)

        # Check the model remembers the name
        messages = chat_store.read(meta.id)
        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) >= 2
        last_response = assistant_msgs[-1].get("content", "")
        assert "TestBot42" in last_response or "testbot42" in last_response.lower()
