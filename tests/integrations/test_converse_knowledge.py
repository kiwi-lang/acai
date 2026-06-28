"""Integration test: ConverseScribe + Knowledge (curator → load → converse).

Verifies that:
1. The curator agent identifies relevant knowledge paths from the DB.
2. LoadKnowledge resolves paths and injects content into the conversation.
3. The conversing agent can answer questions based on that knowledge.
4. A *new* conversation can recall knowledge stored from a prior one.
5. Parsing failures in the curator output emit warning events to the client.

Prerequisites: a running vLLM instance (see conftest.py).
"""

from __future__ import annotations

import asyncio
import os

import pytest

from tests.integrations.conftest import requires_vllm


@requires_vllm
class TestConverseKnowledge:
    """Test the ConverseScribe graph (curator → knowledge → converse → scribe)."""

    @pytest.fixture()
    def knowledge_workspace(self, workspace):
        """Workspace with pre-seeded knowledge documents + FTS index."""
        from acai.knowledge.db import KnowledgeDB
        from acai.knowledge.store import KnowledgeStore

        knowledge_dir = os.path.join(workspace, "knowledge")
        store = KnowledgeStore(knowledge_dir)
        db = KnowledgeDB(os.path.join(knowledge_dir, ".knowledge.db"))

        store.create(
            subject="project",
            subsubject="config",
            title="deployment-secrets",
            content=(
                "The production database password is 'elephant-stapler-9000'. "
                "The staging server is hosted at staging.acai-internal.example.com on port 8443. "
                "Deployments happen every Tuesday at 3pm UTC via the CI/CD pipeline."
            ),
            tags=["deployment", "secrets", "infrastructure"],
        )
        db.upsert(
            "project", "config", "deployment-secrets",
            tags=["deployment", "secrets", "infrastructure"],
            content=(
                "The production database password is 'elephant-stapler-9000'. "
                "The staging server is hosted at staging.acai-internal.example.com on port 8443. "
                "Deployments happen every Tuesday at 3pm UTC via the CI/CD pipeline."
            ),
        )

        store.create(
            subject="team",
            subsubject="processes",
            title="code-review-policy",
            content=(
                "All pull requests require at least 2 approvals before merging. "
                "The designated reviewer for the backend module is Alice Chen. "
                "Security-sensitive changes require an additional review from the security team. "
                "Reviews must be completed within 48 hours of submission."
            ),
            tags=["process", "code-review", "team"],
        )
        db.upsert(
            "team", "processes", "code-review-policy",
            tags=["process", "code-review", "team"],
            content=(
                "All pull requests require at least 2 approvals before merging. "
                "The designated reviewer for the backend module is Alice Chen. "
                "Security-sensitive changes require an additional review from the security team. "
                "Reviews must be completed within 48 hours of submission."
            ),
        )

        return workspace

    @pytest.fixture()
    def knowledge_config(self, knowledge_workspace, provider_config):
        from acai.orchestrator.config import AcaiConfig
        return AcaiConfig(
            workspace=knowledge_workspace,
            providers=[provider_config],
        )

    @pytest.fixture()
    def agent_store(self, knowledge_workspace):
        from acai.orchestrator.agent_store import AgentStore
        agents_dir = os.path.join(knowledge_workspace, "agents")
        builtin_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            os.pardir, os.pardir, "acai", "agents",
        )
        return AgentStore(agents_dir, os.path.normpath(builtin_dir))

    @pytest.fixture()
    def chat_store(self, knowledge_workspace):
        from acai.orchestrator.chat import ChatStore
        return ChatStore(os.path.join(knowledge_workspace, "store"))

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
    def projects(self, knowledge_workspace):
        from acai.orchestrator.projects import ProjectStore
        return ProjectStore(os.path.join(knowledge_workspace, "projects"))

    @pytest.fixture()
    def tracker(self):
        from acai.orchestrator.stream import StreamTracker
        return StreamTracker()

    async def _run_converse_scribe(
        self, message, conv_id, config, agent_store, chat_store,
        tool_registry, load_balancer, projects, tracker,
    ):
        """Helper: send a message through the ConverseScribeGraph pipeline."""
        from acai.tasks.converse_scribe import ConverseScribeGraph
        from acai.utils.audit import NullAuditTrail

        chat_store.append(conv_id, {"role": "user", "content": message})

        work = {
            "message": message,
            "conversation": conv_id,
            "agent": "default",
            "project": "",
            "spec_path": chat_store._msg_path(conv_id),
            "stream_id": conv_id,
            "provider": "test-vllm",
            "model": config.active_provider().model_slug,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseScribeGraph.from_work(
                worker, work,
                agent_store=agent_store,
                chat=chat_store,
                config=config,
                tracker=tracker,
                projects=projects,
                tool_registry=tool_registry,
                audit=NullAuditTrail(),
            )
            async for event in graph.run(work):
                events.append(event)

        messages = chat_store.read(conv_id)
        return events, messages

    @pytest.mark.asyncio
    async def test_knowledge_injected_into_conversation(
        self, knowledge_config, agent_store, chat_store,
        tool_registry, load_balancer, projects, tracker,
    ):
        """The curator finds paths, knowledge loads, and the agent uses it."""
        meta = chat_store.create(title="knowledge-test", agent="default")

        events, messages = await self._run_converse_scribe(
            "What is the production database password for our deployment?",
            meta.id, knowledge_config, agent_store, chat_store,
            tool_registry, load_balancer, projects, tracker,
        )

        # Check we got the full pipeline (curator → converse → done)
        event_types = [e.get("event_type") for e in events]
        assert "curator_start" in event_types, f"Missing curator_start. Events: {event_types}"
        assert "done" in event_types, f"Missing done event. Events: {event_types}"

        # If a warning event was emitted, the curator failed to parse — surface it
        warnings = [e for e in events if e.get("event_type") == "warning"]
        if warnings:
            pytest.fail(
                f"Knowledge pipeline emitted warning(s) — curator output "
                f"could not be parsed: {[w['data']['message'] for w in warnings]}"
            )

        # Verify the agent used the knowledge
        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) >= 1, "Expected at least one assistant response"
        response_text = assistant_msgs[-1].get("content", "").lower()
        assert "elephant-stapler-9000" in response_text, (
            f"Expected the agent to mention the password from knowledge. Got: {response_text[:300]}"
        )

    @pytest.mark.asyncio
    async def test_knowledge_recall_across_conversations(
        self, knowledge_config, agent_store, chat_store,
        tool_registry, load_balancer, projects, tracker,
    ):
        """Knowledge persists across conversations — a new conversation can access it."""
        # Conversation 1: ask about code review
        meta1 = chat_store.create(title="conv1-review", agent="default")
        events1, messages1 = await self._run_converse_scribe(
            "Who is the designated reviewer for backend pull requests?",
            meta1.id, knowledge_config, agent_store, chat_store,
            tool_registry, load_balancer, projects, tracker,
        )

        assistant_msgs1 = [m for m in messages1 if m.get("role") == "assistant"]
        assert len(assistant_msgs1) >= 1
        response1 = assistant_msgs1[-1].get("content", "")
        assert "Alice" in response1 or "alice" in response1.lower(), (
            f"Expected the agent to mention Alice Chen. Got: {response1[:300]}"
        )

        # Conversation 2: completely new conversation, asks about deployment
        meta2 = chat_store.create(title="conv2-deploy", agent="default")
        events2, messages2 = await self._run_converse_scribe(
            "When do deployments happen and what day of the week?",
            meta2.id, knowledge_config, agent_store, chat_store,
            tool_registry, load_balancer, projects, tracker,
        )

        assistant_msgs2 = [m for m in messages2 if m.get("role") == "assistant"]
        assert len(assistant_msgs2) >= 1
        response2 = assistant_msgs2[-1].get("content", "").lower()
        assert "tuesday" in response2, (
            f"Expected the agent to mention Tuesday from knowledge. Got: {response2[:300]}"
        )

    @pytest.mark.asyncio
    async def test_warning_emitted_when_curator_fails_to_parse(
        self, knowledge_config, agent_store, chat_store,
        tool_registry, load_balancer, projects, tracker,
    ):
        """If the curator returns garbage, a warning event reaches the client."""
        from unittest.mock import patch, AsyncMock
        from acai.tasks.converse_scribe import ConverseScribeGraph
        from acai.tasks.graph import Acc
        from acai.utils.audit import NullAuditTrail

        meta = chat_store.create(title="bad-curator", agent="default")
        chat_store.append(meta.id, {"role": "user", "content": "Tell me about X"})

        work = {
            "message": "Tell me about X",
            "conversation": meta.id,
            "agent": "default",
            "project": "",
            "spec_path": chat_store._msg_path(meta.id),
            "stream_id": meta.id,
            "provider": "test-vllm",
            "model": knowledge_config.active_provider().model_slug,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseScribeGraph.from_work(
                worker, work,
                agent_store=agent_store,
                chat=chat_store,
                config=knowledge_config,
                tracker=tracker,
                projects=projects,
                tool_registry=tool_registry,
                audit=NullAuditTrail(),
            )

            # Monkey-patch _background_agent to simulate a curator that returns garbage
            original_bg = graph._background_agent

            async def fake_curator(phase, payload):
                if phase == "curator":
                    graph._last_acc = Acc.__new__(Acc)
                    graph._last_acc.text = "I think you should look at docs about X"
                    graph._last_acc.reasoning = ""
                    graph._last_acc.tool_calls = []
                    yield {"event_type": "curator_start", "data": {"agent": "curator"}}
                    yield {"event_type": "curator_end", "data": {"status": "done", "text_length": 40}}
                else:
                    async for ev in original_bg(phase, payload):
                        yield ev

            graph._background_agent = fake_curator

            async for event in graph.run(work):
                events.append(event)

        # The client should receive a warning about the parsing failure
        warnings = [e for e in events if e.get("event_type") == "warning"]
        assert len(warnings) >= 1, (
            f"Expected a warning event when curator output is unparseable. "
            f"Events: {[e.get('event_type') for e in events]}"
        )
        assert "not valid JSON" in warnings[0]["data"]["message"]
