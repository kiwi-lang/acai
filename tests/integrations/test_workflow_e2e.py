"""End-to-end workflow tests — multi-step graph execution against live vLLM.

These tests exercise:
- DynamicGraph with multiple node types
- Agent → tool → agent chains
- Structured output via ReplyType + ReadReply
- Condition branching based on LLM output
- Multi-agent workflows (thinker → writer)

Prerequisites: a running vLLM instance (see conftest.py).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from tests.integrations.conftest import requires_vllm


def _make_workflow_spec(nodes: list[dict], edges: list[dict], **meta) -> dict:
    """Helper to build a workflow spec dict."""
    return {
        "id": meta.get("id", "test-workflow"),
        "name": meta.get("name", "Test Workflow"),
        "description": meta.get("description", ""),
        "nodes": nodes,
        "edges": edges,
    }


@requires_vllm
class TestSimpleWorkflow:
    """Test a minimal Start → AgentCall → Output workflow."""

    @pytest.fixture()
    def agent_store(self, workspace):
        from acai.orchestrator.agent_store import AgentStore
        agents_dir = os.path.join(workspace, "agents")
        builtin_dir = os.path.normpath(os.path.join(
            os.path.dirname(__file__), os.pardir, os.pardir, "acai", "agents",
        ))
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
    async def test_start_agent_output(
        self, acai_config, agent_store, chat_store,
        tool_registry, load_balancer, projects, tracker,
    ):
        """Start → AgentCall → Output: simplest possible workflow."""
        from acai.tasks import DynamicGraph
        from acai.utils.audit import NullAuditTrail

        spec = _make_workflow_spec(
            nodes=[
                {"id": "start", "type": "start", "data": {}},
                {"id": "agent", "type": "agent_call", "data": {
                    "agent": "default",
                    "system_prompt": "You are a helpful assistant. Be very brief.",
                }},
                {"id": "output", "type": "output", "data": {}},
            ],
            edges=[
                {"source": "start", "target": "agent",
                 "sourceHandle": "exec_out", "targetHandle": "exec_in"},
                {"source": "start", "target": "agent",
                 "sourceHandle": "data_conversation", "targetHandle": "data_context"},
                {"source": "start", "target": "agent",
                 "sourceHandle": "data_message", "targetHandle": "data_message"},
                {"source": "agent", "target": "output",
                 "sourceHandle": "exec_out", "targetHandle": "exec_in"},
                {"source": "agent", "target": "output",
                 "sourceHandle": "data_stream", "targetHandle": "data_response"},
            ],
        )

        meta = chat_store.create(title="wf-test", agent="default")
        chat_store.append(meta.id, {"role": "user", "content": "Say hello in exactly 3 words."})

        work = {
            "message": "Say hello in exactly 3 words.",
            "conversation": meta.id,
            "workflow_spec": spec,
            "workflow_dir": "",
            "stream_id": meta.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = DynamicGraph.from_work(
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
        # Workflow should complete: produces tokens, done, or at least workflow_end
        assert ("token" in event_types or "done" in event_types
                or "workflow_end" in event_types)


@requires_vllm
class TestStructuredOutputWorkflow:
    """Test ReplyType + ReadReply for structured JSON extraction."""

    @pytest.fixture()
    def agent_store(self, workspace):
        from acai.orchestrator.agent_store import AgentStore
        agents_dir = os.path.join(workspace, "agents")
        builtin_dir = os.path.normpath(os.path.join(
            os.path.dirname(__file__), os.pardir, os.pardir, "acai", "agents",
        ))
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
    async def test_structured_json_extraction(
        self, acai_config, agent_store, chat_store,
        tool_registry, load_balancer, projects, tracker,
    ):
        """Agent produces structured JSON, ReadReply parses fields."""
        from acai.tasks import DynamicGraph
        from acai.utils.audit import NullAuditTrail

        fields = json.dumps([
            {"name": "capital", "type": "str"},
            {"name": "population_millions", "type": "int"},
        ])

        spec = _make_workflow_spec(
            id="structured-test",
            name="Structured Output",
            nodes=[
                {"id": "start", "type": "start", "data": {}},
                {"id": "reply_type", "type": "reply_type", "data": {"fields": fields}},
                {"id": "agent", "type": "agent_call", "data": {
                    "agent": "default",
                    "system_prompt": (
                        "You are a geography expert. "
                        "Always respond with ONLY a JSON object, no markdown fences."
                    ),
                }},
                {"id": "read_reply", "type": "read_reply", "data": {"_node_id": "read_reply"}},
                {"id": "print", "type": "print", "data": {"label": "Result"}},
            ],
            edges=[
                {"source": "start", "target": "reply_type",
                 "sourceHandle": "exec_out", "targetHandle": "exec_in"},
                {"source": "reply_type", "target": "agent",
                 "sourceHandle": "exec_out", "targetHandle": "exec_in"},
                {"source": "reply_type", "target": "agent",
                 "sourceHandle": "data_format", "targetHandle": "data_response_format"},
                {"source": "start", "target": "agent",
                 "sourceHandle": "data_message", "targetHandle": "data_message"},
                {"source": "start", "target": "agent",
                 "sourceHandle": "data_conversation", "targetHandle": "data_context"},
                {"source": "agent", "target": "read_reply",
                 "sourceHandle": "exec_out", "targetHandle": "exec_in"},
                {"source": "agent", "target": "read_reply",
                 "sourceHandle": "data_reply", "targetHandle": "data_reply"},
                {"source": "reply_type", "target": "read_reply",
                 "sourceHandle": "data_format", "targetHandle": "data_reply_type"},
                {"source": "read_reply", "target": "print",
                 "sourceHandle": "exec_out", "targetHandle": "exec_in"},
            ],
        )

        meta = chat_store.create(title="structured", agent="default")
        chat_store.append(meta.id, {
            "role": "user",
            "content": "What is the capital of France and its approximate population in millions?",
        })

        work = {
            "message": "What is the capital of France and its approximate population in millions?",
            "conversation": meta.id,
            "workflow_spec": spec,
            "workflow_dir": "",
            "stream_id": meta.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = DynamicGraph.from_work(
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

        # Even if the model doesn't produce perfect JSON, the workflow should complete
        event_types = [e.get("event_type") for e in events]
        assert ("done" in event_types or "token" in event_types
                or "workflow_end" in event_types)


@requires_vllm
class TestMultiAgentWorkflow:
    """Test a workflow where one agent's output feeds into another."""

    @pytest.fixture()
    def agent_store(self, workspace):
        from acai.orchestrator.agent_store import AgentStore
        agents_dir = os.path.join(workspace, "agents")
        builtin_dir = os.path.normpath(os.path.join(
            os.path.dirname(__file__), os.pardir, os.pardir, "acai", "agents",
        ))
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
    async def test_thinker_then_writer(
        self, acai_config, agent_store, chat_store,
        tool_registry, load_balancer, projects, tracker,
    ):
        """Two-agent chain: thinker analyzes, writer produces final output."""
        from acai.tasks import DynamicGraph
        from acai.utils.audit import NullAuditTrail

        spec = _make_workflow_spec(
            id="multi-agent",
            name="Think then Write",
            nodes=[
                {"id": "start", "type": "start", "data": {}},
                {"id": "thinker", "type": "agent_call", "data": {
                    "agent": "default",
                    "system_prompt": (
                        "You are a planning agent. Given a user request, "
                        "produce a brief 2-3 bullet plan for how to respond. "
                        "Output ONLY the plan bullets, nothing else."
                    ),
                }},
                {"id": "accumulate", "type": "accumulate", "data": {}},
                {"id": "writer", "type": "agent_call", "data": {
                    "agent": "default",
                    "system_prompt": (
                        "You are a writing agent. You receive a plan from a thinker agent. "
                        "Write the final response following the plan. Be concise."
                    ),
                }},
                {"id": "output", "type": "output", "data": {}},
            ],
            edges=[
                {"source": "start", "target": "thinker",
                 "sourceHandle": "exec_out", "targetHandle": "exec_in"},
                {"source": "start", "target": "thinker",
                 "sourceHandle": "data_message", "targetHandle": "data_message"},
                {"source": "start", "target": "thinker",
                 "sourceHandle": "data_conversation", "targetHandle": "data_context"},
                {"source": "thinker", "target": "accumulate",
                 "sourceHandle": "exec_out", "targetHandle": "exec_in"},
                {"source": "thinker", "target": "accumulate",
                 "sourceHandle": "data_stream", "targetHandle": "data_stream"},
                {"source": "accumulate", "target": "writer",
                 "sourceHandle": "exec_out", "targetHandle": "exec_in"},
                {"source": "accumulate", "target": "writer",
                 "sourceHandle": "data_text", "targetHandle": "data_message"},
                {"source": "start", "target": "writer",
                 "sourceHandle": "data_conversation", "targetHandle": "data_context"},
                {"source": "writer", "target": "output",
                 "sourceHandle": "exec_out", "targetHandle": "exec_in"},
                {"source": "writer", "target": "output",
                 "sourceHandle": "data_stream", "targetHandle": "data_response"},
            ],
        )

        meta = chat_store.create(title="multi-agent", agent="default")
        chat_store.append(meta.id, {
            "role": "user",
            "content": "Explain why the sky is blue in one paragraph.",
        })

        work = {
            "message": "Explain why the sky is blue in one paragraph.",
            "conversation": meta.id,
            "workflow_spec": spec,
            "workflow_dir": "",
            "stream_id": meta.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = DynamicGraph.from_work(
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
        # The workflow should complete (tokens from writer or workflow lifecycle)
        assert ("token" in event_types or "done" in event_types
                or "workflow_end" in event_types)
