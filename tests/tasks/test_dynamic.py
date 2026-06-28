"""Tests for acai.tasks.dynamic — DynamicGraph and WorkflowSpec."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acai.tasks.dynamic import DynamicGraph, WorkflowSpec, _ForEachFrame
from acai.tasks import nodes as node_registry

_real_node_get = node_registry.get


def _mock_node_get(overrides: dict):
    """Return a side_effect function that looks up custom nodes from overrides,
    falling back to the real registry for all other types."""
    def _get(type_name):
        if type_name in overrides:
            return overrides[type_name]
        return _real_node_get(type_name)
    return _get


# ======================================================================
# WorkflowSpec tests
# ======================================================================


class TestWorkflowSpec:

    def test_basic_construction(self):
        raw = {
            "id": "wf1",
            "name": "Test Workflow",
            "description": "A test",
            "nodes": [{"id": "n1", "type": "start"}],
            "edges": [],
        }
        spec = WorkflowSpec(raw)
        assert spec.id == "wf1"
        assert spec.name == "Test Workflow"
        assert spec.description == "A test"
        assert len(spec.nodes) == 1
        assert len(spec.edges) == 0

    def test_defaults_for_missing_fields(self):
        raw = {"nodes": [{"id": "n1", "type": "start"}]}
        spec = WorkflowSpec(raw)
        assert spec.id == ""
        assert spec.name == ""
        assert spec.description == ""

    def test_name_defaults_to_id(self):
        raw = {"id": "wf-abc", "nodes": [{"id": "n1", "type": "start"}]}
        spec = WorkflowSpec(raw)
        assert spec.name == "wf-abc"

    def test_empty_nodes_raises(self):
        with pytest.raises(ValueError, match="at least one node"):
            WorkflowSpec({"nodes": []})

    def test_missing_nodes_raises(self):
        with pytest.raises(ValueError, match="at least one node"):
            WorkflowSpec({})

    def test_node_lookup(self):
        raw = {
            "nodes": [
                {"id": "a", "type": "start"},
                {"id": "b", "type": "output"},
            ],
            "edges": [],
        }
        spec = WorkflowSpec(raw)
        assert spec.node("a")["type"] == "start"
        assert spec.node("b")["type"] == "output"

    def test_node_lookup_missing_raises(self):
        spec = WorkflowSpec({"nodes": [{"id": "a", "type": "start"}]})
        with pytest.raises(KeyError):
            spec.node("missing")

    def test_outgoing_edges(self):
        raw = {
            "nodes": [{"id": "a", "type": "start"}, {"id": "b", "type": "output"}],
            "edges": [
                {"source": "a", "target": "b", "sourceHandle": "exec_out", "type": "exec"},
                {"source": "a", "target": "b", "sourceHandle": "data_out", "type": "data"},
            ],
        }
        spec = WorkflowSpec(raw)
        assert len(spec.outgoing("a")) == 2
        assert len(spec.outgoing("a", "exec_out")) == 1
        assert len(spec.outgoing("a", "data_out")) == 1
        assert len(spec.outgoing("a", "nonexistent")) == 0
        assert len(spec.outgoing("b")) == 0

    def test_incoming_edges(self):
        raw = {
            "nodes": [{"id": "a", "type": "start"}, {"id": "b", "type": "output"}],
            "edges": [
                {"source": "a", "target": "b", "targetHandle": "exec_in", "type": "exec"},
                {"source": "a", "target": "b", "targetHandle": "data_in", "type": "data"},
            ],
        }
        spec = WorkflowSpec(raw)
        assert len(spec.incoming("b")) == 2
        assert len(spec.incoming("b", "exec_in")) == 1
        assert len(spec.incoming("b", "data_in")) == 1
        assert len(spec.incoming("a")) == 0

    def test_exec_edges(self):
        raw = {
            "nodes": [{"id": "a", "type": "start"}, {"id": "b", "type": "output"}],
            "edges": [
                {"source": "a", "target": "b", "sourceHandle": "exec_out", "type": "exec"},
                {"source": "a", "target": "b", "sourceHandle": "data_out", "type": "data"},
            ],
        }
        spec = WorkflowSpec(raw)
        exec_edges = spec.exec_edges("a", "exec_out")
        assert len(exec_edges) == 1
        assert exec_edges[0]["type"] == "exec"

    def test_data_inputs(self):
        raw = {
            "nodes": [{"id": "a", "type": "start"}, {"id": "b", "type": "agent"}],
            "edges": [
                {"source": "a", "target": "b", "type": "data",
                 "sourceHandle": "data_message", "targetHandle": "data_context"},
                {"source": "a", "target": "b", "type": "exec",
                 "sourceHandle": "exec_out", "targetHandle": "exec_in"},
            ],
        }
        spec = WorkflowSpec(raw)
        data_in = spec.data_inputs("b")
        assert len(data_in) == 1
        assert data_in[0]["type"] == "data"

    def test_find_start(self):
        raw = {
            "nodes": [
                {"id": "n1", "type": "agent"},
                {"id": "n2", "type": "start"},
                {"id": "n3", "type": "output"},
            ],
            "edges": [],
        }
        spec = WorkflowSpec(raw)
        start = spec.find_start()
        assert start is not None
        assert start["id"] == "n2"

    def test_find_start_returns_none_when_missing(self):
        raw = {
            "nodes": [{"id": "n1", "type": "agent"}],
            "edges": [],
        }
        spec = WorkflowSpec(raw)
        assert spec.find_start() is None

    def test_from_json(self):
        raw = {"id": "json-wf", "nodes": [{"id": "s", "type": "start"}], "edges": []}
        spec = WorkflowSpec.from_json(json.dumps(raw))
        assert spec.id == "json-wf"


# ======================================================================
# DynamicGraph tests
# ======================================================================


def _make_graph(chat_store, agent_store, acai_config, stream_id="test-stream", conversation="conv-1"):
    """Helper to construct a DynamicGraph with mocked dependencies."""
    worker = MagicMock()
    worker.url = "http://fake:9999"
    graph = DynamicGraph(
        worker,
        agent_store=agent_store,
        chat=chat_store,
        config=acai_config,
        tracker=None,
        projects=None,
        tool_registry=None,
        stream_id=stream_id,
        conversation=conversation,
    )
    return graph


async def _collect(graph, work):
    """Run a graph and collect all events."""
    events = []
    async for event in graph.run(work):
        events.append(event)
    return events


@pytest.mark.asyncio
class TestDynamicGraphErrors:

    async def test_no_workflow_spec(self, chat_store, agent_store, acai_config):
        graph = _make_graph(chat_store, agent_store, acai_config)
        events = await _collect(graph, {})
        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert "No workflow_spec" in events[0]["data"]["message"]

    async def test_invalid_json_spec(self, chat_store, agent_store, acai_config):
        graph = _make_graph(chat_store, agent_store, acai_config)
        events = await _collect(graph, {"workflow_spec_json": "not valid json {"})
        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert "Invalid workflow spec JSON" in events[0]["data"]["message"]

    async def test_valid_json_spec_from_string(self, chat_store, agent_store, acai_config):
        """workflow_spec_json is parsed and used when workflow_spec is absent."""
        spec = {
            "id": "wf",
            "nodes": [{"id": "s", "type": "start"}],
            "edges": [],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec_json": json.dumps(spec)})
        types = [e["event_type"] for e in events]
        assert "workflow_start" in types
        assert "done" in types

    async def test_json_spec_no_start_node(self, chat_store, agent_store, acai_config):
        spec = {
            "id": "wf",
            "nodes": [{"id": "n1", "type": "agent"}],
            "edges": [],
        }
        graph = _make_graph(chat_store, agent_store, acai_config)
        events = await _collect(graph, {"workflow_spec_json": json.dumps(spec)})
        assert events[0]["event_type"] == "error"
        assert "no start node" in events[0]["data"]["message"].lower()

    async def test_invalid_spec_structure(self, chat_store, agent_store, acai_config):
        graph = _make_graph(chat_store, agent_store, acai_config)
        events = await _collect(graph, {"workflow_spec": {"nodes": []}})
        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert "Invalid workflow spec" in events[0]["data"]["message"]

    async def test_no_start_node(self, chat_store, agent_store, acai_config):
        graph = _make_graph(chat_store, agent_store, acai_config)
        spec = {"nodes": [{"id": "n1", "type": "agent"}], "edges": []}
        events = await _collect(graph, {"workflow_spec": spec})
        assert any(e["event_type"] == "error" for e in events)
        error = next(e for e in events if e["event_type"] == "error")
        assert "no start node" in error["data"]["message"].lower()

    async def test_unknown_node_type(self, chat_store, agent_store, acai_config):
        spec = {
            "id": "wf",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "completely_unknown_type"},
            ],
            "edges": [
                {"source": "s", "target": "n1", "sourceHandle": "exec_out", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config)
        events = await _collect(graph, {"workflow_spec": spec})
        types = [e["event_type"] for e in events]
        assert "error" in types
        error = next(e for e in events if e["event_type"] == "error")
        assert "Unknown node type" in error["data"]["message"]
        assert "completely_unknown_type" in error["data"]["message"]


@pytest.mark.asyncio
class TestDynamicGraphExecution:

    async def test_start_only_workflow(self, chat_store, agent_store, acai_config):
        """A start node with no outgoing exec edges ends immediately."""
        spec = {
            "id": "wf-start-only",
            "name": "Start Only",
            "nodes": [{"id": "s", "type": "start"}],
            "edges": [],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "hi"})
        types = [e["event_type"] for e in events]
        assert "workflow_start" in types
        assert "workflow_end" in types
        assert "node_start" in types
        assert "node_end" in types
        assert "done" in types

    async def test_workflow_start_event_data(self, chat_store, agent_store, acai_config):
        spec = {
            "id": "wf-meta",
            "name": "Meta Test",
            "nodes": [{"id": "s", "type": "start"}],
            "edges": [],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        ws_event = next(e for e in events if e["event_type"] == "workflow_start")
        assert ws_event["data"]["workflow_id"] == "wf-meta"
        assert ws_event["data"]["name"] == "Meta Test"
        assert ws_event["data"]["node_count"] == 1

    async def test_start_to_output(self, load_balancer, chat_store, agent_store, acai_config):
        """Start → Output path with print node verifying data pin resolution."""
        spec = {
            "id": "wf-out",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "p", "type": "print", "data": {"label": "Debug"}},
                {"id": "o", "type": "output", "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "p",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "p", "target": "o",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "hello"})
        types = [e["event_type"] for e in events]
        assert "workflow_start" in types
        assert "node_start" in types
        assert "node_end" in types
        assert "workflow_end" in types
        assert "done" in types

    async def test_condition_true_branch(self, chat_store, agent_store, acai_config):
        """Condition node follows exec_true when value is truthy."""
        spec = {
            "id": "wf-cond",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "c", "type": "condition", "data": {"expression": "True"}},
                {"id": "pt", "type": "print", "data": {"label": "True Branch"}},
                {"id": "pf", "type": "print", "data": {"label": "False Branch"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "c",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "s", "target": "c",
                 "sourceHandle": "data_message", "targetHandle": "data_value",
                 "type": "data"},
                {"id": "e3", "source": "c", "target": "pt",
                 "sourceHandle": "exec_true", "type": "exec"},
                {"id": "e4", "source": "c", "target": "pf",
                 "sourceHandle": "exec_false", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "hello"})
        labels = [
            e["data"].get("label", "")
            for e in events if e["event_type"] == "node_start"
        ]
        assert "True Branch" in labels
        assert "False Branch" not in labels

    async def test_condition_false_branch(self, chat_store, agent_store, acai_config):
        """Condition node follows exec_false when expression evaluates to False."""
        spec = {
            "id": "wf-cond-f",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "c", "type": "condition", "data": {"expression": "len(input) == 0"}},
                {"id": "pt", "type": "print", "data": {"label": "True Branch"}},
                {"id": "pf", "type": "print", "data": {"label": "False Branch"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "c",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "s", "target": "c",
                 "sourceHandle": "data_message", "targetHandle": "data_value",
                 "type": "data"},
                {"id": "e3", "source": "c", "target": "pt",
                 "sourceHandle": "exec_true", "type": "exec"},
                {"id": "e4", "source": "c", "target": "pf",
                 "sourceHandle": "exec_false", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "notempty"})
        labels = [
            e["data"].get("label", "")
            for e in events if e["event_type"] == "node_start"
        ]
        assert "False Branch" in labels
        assert "True Branch" not in labels

    async def test_edge_traversal_events(self, chat_store, agent_store, acai_config):
        """Edge traversal events are emitted between nodes."""
        spec = {
            "id": "wf-edge",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "p", "type": "print", "data": {"label": "P"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "p",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "hi"})
        edge_events = [e for e in events if e["event_type"] == "edge_traversed"]
        assert len(edge_events) >= 1
        assert edge_events[0]["data"]["source"] == "s"
        assert edge_events[0]["data"]["target"] == "p"

    async def test_data_pin_resolution(self, chat_store, agent_store, acai_config):
        """Data from start node flows through data edges to downstream nodes."""
        spec = {
            "id": "wf-data",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "p", "type": "print", "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "p",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "s", "target": "p",
                 "sourceHandle": "data_message", "targetHandle": "data_value",
                 "type": "data"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "test input"})
        print_events = [e for e in events if e["event_type"] == "print"]
        assert len(print_events) == 1
        assert "test input" in print_events[0]["data"]["text"]

    async def test_inline_data_from_node(self, chat_store, agent_store, acai_config):
        """Unconnected pins pull values from the inline node data."""
        spec = {
            "id": "wf-inline",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "p", "type": "print", "data": {"value": "inline-val", "label": "P"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "p",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        print_events = [e for e in events if e["event_type"] == "print"]
        assert len(print_events) == 1
        assert "inline-val" in print_events[0]["data"]["text"]

    async def test_dead_end_stops_execution(self, chat_store, agent_store, acai_config):
        """When no exec edge exists, the workflow terminates cleanly."""
        spec = {
            "id": "wf-dead",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "p", "type": "print", "data": {"label": "End"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "p",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        types = [e["event_type"] for e in events]
        assert "workflow_end" in types
        assert "done" in types

    async def test_output_node_stops_execution(self, chat_store, agent_store, acai_config):
        """Output node terminates graph regardless of further exec edges."""
        spec = {
            "id": "wf-output-stop",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "o", "type": "output", "data": {}},
                {"id": "p", "type": "print", "data": {"label": "After Output"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "o",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "o", "target": "p",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        labels = [
            e["data"].get("label", "")
            for e in events if e["event_type"] == "node_start"
        ]
        assert "After Output" not in labels


@pytest.mark.asyncio
class TestDynamicGraphForEach:

    async def test_foreach_empty_array(self, chat_store, agent_store, acai_config):
        """ForEach with empty array skips body and follows exec_then."""
        spec = {
            "id": "wf-fe-empty",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "fe", "type": "for_each", "data": {"label": "Loop", "array": []}},
                {"id": "body", "type": "print", "data": {"label": "Body"}},
                {"id": "after", "type": "print", "data": {"label": "After"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "fe",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "fe", "target": "body",
                 "sourceHandle": "exec_body", "type": "exec"},
                {"id": "e3", "source": "fe", "target": "after",
                 "sourceHandle": "exec_then", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        labels = [
            e["data"].get("label", "")
            for e in events if e["event_type"] == "node_start"
        ]
        assert "After" in labels
        assert "Body" not in labels

    async def test_foreach_iterates_items(self, chat_store, agent_store, acai_config):
        """ForEach iterates over each item in the array."""
        spec = {
            "id": "wf-fe-items",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "fe", "type": "for_each", "data": {
                    "label": "Loop", "array": ["a", "b", "c"]}},
                {"id": "body", "type": "print", "data": {"label": "Body"}},
                {"id": "after", "type": "print", "data": {"label": "After"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "fe",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "fe", "target": "body",
                 "sourceHandle": "exec_body", "type": "exec"},
                {"id": "e3", "source": "fe", "target": "after",
                 "sourceHandle": "exec_then", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        body_starts = [
            e for e in events
            if e["event_type"] == "node_start" and e["data"].get("label") == "Body"
        ]
        assert len(body_starts) == 3

        labels = [
            e["data"].get("label", "")
            for e in events if e["event_type"] == "node_start"
        ]
        assert "After" in labels

    async def test_foreach_with_data_pins(self, chat_store, agent_store, acai_config):
        """ForEach outputs item/index accessible via data pins in the body."""
        spec = {
            "id": "wf-fe-data",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "fe", "type": "for_each", "data": {
                    "label": "Loop", "array": ["x", "y"]}},
                {"id": "body", "type": "print", "data": {"label": "Body"}},
                {"id": "after", "type": "print", "data": {"label": "After"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "fe",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "fe", "target": "body",
                 "sourceHandle": "exec_body", "type": "exec"},
                {"id": "e3", "source": "fe", "target": "after",
                 "sourceHandle": "exec_then", "type": "exec"},
                {"id": "e4", "source": "fe", "target": "body",
                 "sourceHandle": "data_item", "targetHandle": "data_value",
                 "type": "data"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        body_prints = [
            e for e in events
            if e["event_type"] == "print" and e["data"].get("label") == "Body"
        ]
        assert len(body_prints) == 2
        texts = [e["data"]["text"] for e in body_prints]
        assert any('"x"' in t for t in texts)
        assert any('"y"' in t for t in texts)


@pytest.mark.asyncio
class TestDynamicGraphNodeExecution:

    async def test_node_execution_error_halts_graph(
        self, chat_store, agent_store, acai_config,
    ):
        """When a node raises an exception, the graph emits an error and stops."""
        spec = {
            "id": "wf-err",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "p", "type": "print", "data": {"label": "P"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "p",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")

        class _BrokenNode:
            type = "print"
            async def execute(self, ctx):
                raise RuntimeError("node exploded")
                yield  # noqa: B901

        with patch("acai.tasks.nodes.get", return_value=_BrokenNode()):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        types = [e["event_type"] for e in events]
        assert "error" in types
        error = next(e for e in events if e["event_type"] == "error")
        assert "node exploded" in error["data"]["message"]
        assert "done" not in types

    async def test_node_output_preview_string(self, chat_store, agent_store, acai_config):
        """node_end event contains a preview of string outputs."""
        spec = {
            "id": "wf-preview",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "p", "type": "print", "data": {"label": "Debug"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "p",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "hi"})
        node_ends = [e for e in events if e["event_type"] == "node_end"]
        assert len(node_ends) >= 1


@pytest.mark.asyncio
class TestDynamicGraphConversationPersistence:

    async def test_final_text_persisted_to_chat(
        self, load_balancer, chat_store, agent_store, acai_config,
    ):
        """When the workflow accumulates tokens, they are saved to the conversation."""
        conv = chat_store.create(title="dynamic test")
        chat_store.append(conv.id, {"role": "user", "content": "hello"})

        spec = {
            "id": "wf-persist",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "o", "type": "output", "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "o",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        async with load_balancer.acquire() as worker:
            graph = DynamicGraph.from_work(
                worker,
                {"workflow_spec": spec, "message": "hello",
                 "conversation": conv.id, "stream_id": conv.id},
                agent_store=agent_store,
                chat=chat_store,
                config=acai_config,
            )
            await _collect(graph, {
                "workflow_spec": spec, "message": "hello",
                "conversation": conv.id, "stream_id": conv.id,
            })


@pytest.mark.asyncio
class TestDynamicGraphFromWork:

    async def test_from_work_sets_conversation(self, chat_store, agent_store, acai_config):
        worker = MagicMock()
        worker.url = "http://fake:9999"
        work = {"conversation": "conv-123", "stream_id": "s-456"}
        graph = DynamicGraph.from_work(
            worker, work,
            agent_store=agent_store,
            chat=chat_store,
            config=acai_config,
        )
        assert graph.conversation == "conv-123"
        assert graph.stream_id == "s-456"

    async def test_from_work_sets_workflow_dir(self, chat_store, agent_store, acai_config):
        worker = MagicMock()
        worker.url = "http://fake:9999"
        work = {"conversation": "", "stream_id": "", "workflow_dir": "/tmp/wf"}
        graph = DynamicGraph.from_work(
            worker, work,
            agent_store=agent_store,
            chat=chat_store,
            config=acai_config,
        )
        assert graph._workflow_dir == "/tmp/wf"


class TestForEachFrame:

    def test_defaults(self):
        frame = _ForEachFrame(node_id="n1", items=["a", "b"])
        assert frame.node_id == "n1"
        assert frame.items == ["a", "b"]
        assert frame.index == 0

    def test_mutation(self):
        frame = _ForEachFrame(node_id="n1", items=[1, 2, 3])
        frame.index = 2
        assert frame.index == 2


# ======================================================================
# Additional edge-case and error-path tests
# ======================================================================


@pytest.mark.asyncio
class TestDynamicGraphEdgeCases:
    """Cover uncovered branches: token/reasoning accumulation, preview
    generation, max-step guard, ForEach edge cases, and conversation
    persistence with reasoning."""

    async def test_workflow_spec_json_with_type_error(
        self, chat_store, agent_store, acai_config,
    ):
        """TypeError in workflow_spec_json parsing yields a clear error."""
        graph = _make_graph(chat_store, agent_store, acai_config)
        events = await _collect(graph, {"workflow_spec_json": 12345})
        assert events[0]["event_type"] == "error"
        assert "Invalid workflow spec JSON" in events[0]["data"]["message"]

    async def test_workflow_spec_with_node_missing_id_key(
        self, chat_store, agent_store, acai_config,
    ):
        """A node dict missing 'id' key triggers a clear KeyError message."""
        graph = _make_graph(chat_store, agent_store, acai_config)
        events = await _collect(graph, {"workflow_spec": {"nodes": [{"type": "start"}]}})
        assert events[0]["event_type"] == "error"
        assert "Invalid workflow spec" in events[0]["data"]["message"]

    async def test_token_accumulation(self, chat_store, agent_store, acai_config):
        """Token events emitted by a node are accumulated into final_text."""
        spec = {
            "id": "wf-tok",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "custom_streamer", "data": {"label": "Streamer"}},
                {"id": "o", "type": "output", "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "n1", "target": "o",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _TokenNode:
            type = "custom_streamer"
            async def execute(self, ctx):
                yield {"type": "event", "data": {
                    "event_type": "token",
                    "data": {"token": "Hello "},
                }}
                yield {"type": "event", "data": {
                    "event_type": "token",
                    "data": {"token": "World"},
                }}
                yield {"type": "output", "data": {"text": "Hello World"}}

        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"custom_streamer": _TokenNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        wf_end = next(e for e in events if e["event_type"] == "workflow_end")
        assert "Hello World" in wf_end["data"]["output"]

    async def test_reasoning_accumulation(self, chat_store, agent_store, acai_config):
        """Reasoning events are accumulated alongside token events."""
        spec = {
            "id": "wf-reason",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "thinker_node", "data": {"label": "Think"}},
                {"id": "o", "type": "output", "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "n1", "target": "o",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _ReasoningNode:
            type = "thinker_node"
            async def execute(self, ctx):
                yield {"type": "event", "data": {
                    "event_type": "reasoning",
                    "data": {"token": "Let me think..."},
                }}
                yield {"type": "event", "data": {
                    "event_type": "token",
                    "data": {"token": "Answer"},
                }}
                yield {"type": "output", "data": {"text": "Answer"}}

        conv = chat_store.create(title="reasoning test")
        graph = _make_graph(chat_store, agent_store, acai_config, conversation=conv.id)
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"thinker_node": _ReasoningNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        # Reasoning events should be yielded to the stream
        reasoning_events = [e for e in events if e.get("event_type") == "reasoning"]
        assert len(reasoning_events) == 1

    async def test_conversation_persistence_with_reasoning(
        self, chat_store, agent_store, acai_config,
    ):
        """When both final_text and final_reasoning are accumulated, they
        are persisted to the conversation with reasoning field."""
        spec = {
            "id": "wf-persist",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "persist_node", "data": {"label": "N"}},
                {"id": "o", "type": "output", "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "n1", "target": "o",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _PersistNode:
            type = "persist_node"
            async def execute(self, ctx):
                yield {"type": "event", "data": {
                    "event_type": "reasoning",
                    "data": {"token": "Thinking step"},
                }}
                yield {"type": "event", "data": {
                    "event_type": "token",
                    "data": {"token": "Final answer"},
                }}
                yield {"type": "output", "data": {"text": "Final answer"}}

        conv = chat_store.create(title="persist test")
        chat_store.append(conv.id, {"role": "user", "content": "hello"})

        graph = _make_graph(chat_store, agent_store, acai_config, conversation=conv.id)
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"persist_node": _PersistNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        # Verify the message was persisted with reasoning
        messages = chat_store.read(conv.id)
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["content"] == "Final answer"
        assert assistant_msgs[0]["reasoning"] == "Thinking step"

    async def test_preview_dict_output(self, chat_store, agent_store, acai_config):
        """node_end preview is generated from dict/list/int outputs via JSON."""
        spec = {
            "id": "wf-prev",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "dict_node", "data": {"label": "Dict"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _DictNode:
            type = "dict_node"
            async def execute(self, ctx):
                yield {"type": "output", "data": {"result": {"key": "value", "count": 42}}}

        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"dict_node": _DictNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        node_end = next(e for e in events if e["event_type"] == "node_end" and e["data"]["type"] == "dict_node")
        assert "key" in node_end["data"]["output_preview"]
        assert "value" in node_end["data"]["output_preview"]

    async def test_preview_unserializable_output(self, chat_store, agent_store, acai_config):
        """When output contains an unserializable value, preview generation
        gracefully continues to the next value or remains empty."""
        spec = {
            "id": "wf-unser",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "bad_json_node", "data": {"label": "Bad"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _Unserializable:
            pass

        class _BadJsonNode:
            type = "bad_json_node"
            async def execute(self, ctx):
                yield {"type": "output", "data": {
                    "bad_val": {"nested": _Unserializable()},
                    "good_val": "fallback text",
                }}

        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"bad_json_node": _BadJsonNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        node_end = next(e for e in events if e["event_type"] == "node_end" and e["data"]["type"] == "bad_json_node")
        # Should not crash; preview is either the fallback string or empty
        assert "output_preview" in node_end["data"]

    async def test_foreach_non_list_iterable(self, chat_store, agent_store, acai_config):
        """ForEach handles a non-list iterable (e.g. tuple) by converting to list."""
        spec = {
            "id": "wf-fe-tuple",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "fe", "type": "for_each", "data": {"label": "Loop"}},
                {"id": "body", "type": "print", "data": {"label": "Body"}},
                {"id": "after", "type": "print", "data": {"label": "After"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "fe",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "fe", "target": "body",
                 "sourceHandle": "exec_body", "type": "exec"},
                {"id": "e3", "source": "fe", "target": "after",
                 "sourceHandle": "exec_then", "type": "exec"},
                {"id": "e-data", "source": "s", "target": "fe",
                 "sourceHandle": "data_items", "targetHandle": "data_array",
                 "type": "data"},
            ],
        }

        class _TupleStartNode:
            type = "start"
            async def execute(self, ctx):
                yield {"type": "output", "data": {"items": (1, 2)}}

        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"start": _TupleStartNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        body_starts = [
            e for e in events
            if e["event_type"] == "node_start" and e["data"].get("label") == "Body"
        ]
        assert len(body_starts) == 2

    async def test_foreach_no_exec_body_edge(self, chat_store, agent_store, acai_config):
        """ForEach with items but no exec_body edge terminates gracefully."""
        spec = {
            "id": "wf-fe-nobody",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "fe", "type": "for_each", "data": {
                    "label": "Loop", "array": ["a", "b"]}},
                {"id": "after", "type": "print", "data": {"label": "After"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "fe",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e3", "source": "fe", "target": "after",
                 "sourceHandle": "exec_then", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        types = [e["event_type"] for e in events]
        assert "workflow_end" in types
        assert "done" in types

    async def test_foreach_no_exec_then_edge(self, chat_store, agent_store, acai_config):
        """ForEach with empty array and no exec_then edge ends gracefully."""
        spec = {
            "id": "wf-fe-nothen",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "fe", "type": "for_each", "data": {
                    "label": "Loop", "array": []}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "fe",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        types = [e["event_type"] for e in events]
        assert "workflow_end" in types
        assert "done" in types

    async def test_foreach_iteration_dead_end_pops_stack(
        self, chat_store, agent_store, acai_config,
    ):
        """ForEach body leads to dead end — stack pops and follows exec_then."""
        spec = {
            "id": "wf-fe-deadend",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "fe", "type": "for_each", "data": {
                    "label": "Loop", "array": ["a", "b"]}},
                {"id": "body", "type": "print", "data": {"label": "Body"}},
                {"id": "after", "type": "print", "data": {"label": "After"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "fe",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "fe", "target": "body",
                 "sourceHandle": "exec_body", "type": "exec"},
                {"id": "e3", "source": "fe", "target": "after",
                 "sourceHandle": "exec_then", "type": "exec"},
                # body has NO outgoing exec edge → dead end in body
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        body_starts = [
            e for e in events
            if e["event_type"] == "node_start" and e["data"].get("label") == "Body"
        ]
        # Body executed for each item, then falls through to After
        assert len(body_starts) == 2
        labels = [
            e["data"].get("label", "")
            for e in events if e["event_type"] == "node_start"
        ]
        assert "After" in labels

    async def test_foreach_body_no_exec_edge_during_iteration(
        self, chat_store, agent_store, acai_config,
    ):
        """When ForEach body has no outgoing exec edge and next iteration
        also has no exec_body edge, the stack unwinds correctly."""
        spec = {
            "id": "wf-fe-noedge-iter",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "fe", "type": "for_each", "data": {
                    "label": "Loop", "array": ["x", "y", "z"]}},
                {"id": "body", "type": "print", "data": {"label": "Body"}},
                {"id": "after", "type": "print", "data": {"label": "Done"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "fe",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "fe", "target": "body",
                 "sourceHandle": "exec_body", "type": "exec"},
                {"id": "e3", "source": "fe", "target": "after",
                 "sourceHandle": "exec_then", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        body_starts = [
            e for e in events
            if e["event_type"] == "node_start" and e["data"].get("label") == "Body"
        ]
        assert len(body_starts) == 3
        after_starts = [
            e for e in events
            if e["event_type"] == "node_start" and e["data"].get("label") == "Done"
        ]
        assert len(after_starts) == 1

    async def test_max_steps_guard(self, chat_store, agent_store, acai_config):
        """Workflow terminates when max steps is exceeded (infinite loop guard)."""
        spec = {
            "id": "wf-loop",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "print", "data": {"label": "Loop Node"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e-loop", "source": "n1", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        # Should terminate due to max_steps limit (nodes*4+20 = 2*4+20 = 28)
        types = [e["event_type"] for e in events]
        assert "workflow_end" in types
        assert "done" in types
        node_starts = [e for e in events if e["event_type"] == "node_start"]
        assert len(node_starts) <= 28

    async def test_output_node_includes_final_output(
        self, chat_store, agent_store, acai_config,
    ):
        """Output node's node_end event includes final_output with accumulated text."""
        spec = {
            "id": "wf-final-output",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "streamer_node", "data": {"label": "S"}},
                {"id": "o", "type": "output", "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "n1", "target": "o",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _StreamerNode:
            type = "streamer_node"
            async def execute(self, ctx):
                yield {"type": "event", "data": {
                    "event_type": "token",
                    "data": {"token": "streamed content"},
                }}
                yield {"type": "output", "data": {"text": "streamed content"}}

        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"streamer_node": _StreamerNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        output_end = next(
            e for e in events
            if e["event_type"] == "node_end" and e["data"]["type"] == "output"
        )
        assert output_end["data"]["final_output"] == "streamed content"

    async def test_data_pin_not_resolved_when_source_missing(
        self, chat_store, agent_store, acai_config,
    ):
        """Data pin with a source that hasn't produced output leaves the pin
        unresolved — connected pins block inline-data fallback."""
        spec = {
            "id": "wf-missing-src",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "p", "type": "print", "data": {
                    "label": "P", "value": "fallback"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "p",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e-data", "source": "nonexistent", "target": "p",
                 "sourceHandle": "data_result", "targetHandle": "data_value",
                 "type": "data"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        # The print node still executes but without the connected data pin resolved
        # (inline "value" is NOT used since the pin is connected)
        print_events = [e for e in events if e["event_type"] == "print"]
        assert len(print_events) == 1
        # The inline "value" key is suppressed because the pin is considered connected
        assert "fallback" not in print_events[0]["data"]["text"]

    async def test_node_error_includes_traceback(
        self, chat_store, agent_store, acai_config,
    ):
        """Error event from a failing node includes traceback for debugging."""
        spec = {
            "id": "wf-tb",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "failing_node", "data": {"label": "Fail"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _FailingNode:
            type = "failing_node"
            async def execute(self, ctx):
                raise ValueError("something went wrong")
                yield  # noqa: B901

        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"failing_node": _FailingNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        error = next(e for e in events if e["event_type"] == "error")
        assert "something went wrong" in error["data"]["message"]
        assert "traceback" in error["data"]
        assert "ValueError" in error["data"]["traceback"]

    async def test_empty_workflow_spec_dict(self, chat_store, agent_store, acai_config):
        """Empty dict as workflow_spec produces clear error."""
        graph = _make_graph(chat_store, agent_store, acai_config)
        events = await _collect(graph, {"workflow_spec": {}})
        assert events[0]["event_type"] == "error"
        assert "No workflow_spec" in events[0]["data"]["message"]

    async def test_workflow_spec_none_value(self, chat_store, agent_store, acai_config):
        """None as workflow_spec value produces clear error."""
        graph = _make_graph(chat_store, agent_store, acai_config)
        events = await _collect(graph, {"workflow_spec": None})
        assert events[0]["event_type"] == "error"
        assert "No workflow_spec" in events[0]["data"]["message"]

    async def test_preview_with_boolean_output(self, chat_store, agent_store, acai_config):
        """Preview handles boolean output via JSON serialization."""
        spec = {
            "id": "wf-bool",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "bool_node", "data": {"label": "Bool"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _BoolNode:
            type = "bool_node"
            async def execute(self, ctx):
                yield {"type": "output", "data": {"flag": True}}

        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"bool_node": _BoolNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        node_end = next(e for e in events if e["event_type"] == "node_end" and e["data"]["type"] == "bool_node")
        assert node_end["data"]["output_preview"] == "true"

    async def test_preview_with_list_output(self, chat_store, agent_store, acai_config):
        """Preview handles list output via JSON serialization."""
        spec = {
            "id": "wf-list",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "list_node", "data": {"label": "List"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _ListNode:
            type = "list_node"
            async def execute(self, ctx):
                yield {"type": "output", "data": {"items": [1, 2, 3]}}

        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"list_node": _ListNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        node_end = next(e for e in events if e["event_type"] == "node_end" and e["data"]["type"] == "list_node")
        assert "[1, 2, 3]" in node_end["data"]["output_preview"]

    async def test_preview_truncated_at_200_chars(self, chat_store, agent_store, acai_config):
        """Preview is truncated to 200 characters for very long outputs."""
        spec = {
            "id": "wf-long",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "long_node", "data": {"label": "Long"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _LongNode:
            type = "long_node"
            async def execute(self, ctx):
                yield {"type": "output", "data": {"text": "x" * 500}}

        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"long_node": _LongNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        node_end = next(e for e in events if e["event_type"] == "node_end" and e["data"]["type"] == "long_node")
        assert len(node_end["data"]["output_preview"]) == 200

    async def test_foreach_iteration_completes_with_no_exec_then(
        self, chat_store, agent_store, acai_config,
    ):
        """ForEach iterates through all items and ends gracefully when there
        is no exec_then edge (dead end after iteration completes)."""
        spec = {
            "id": "wf-fe-nothen2",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "fe", "type": "for_each", "data": {
                    "label": "Loop", "array": ["a", "b"]}},
                {"id": "body", "type": "print", "data": {"label": "Body"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "fe",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "fe", "target": "body",
                 "sourceHandle": "exec_body", "type": "exec"},
                # No exec_then edge — iteration completes to dead end
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        events = await _collect(graph, {"workflow_spec": spec, "message": "x"})
        types = [e["event_type"] for e in events]
        assert "workflow_end" in types
        assert "done" in types
        body_starts = [
            e for e in events
            if e["event_type"] == "node_start" and e["data"].get("label") == "Body"
        ]
        assert len(body_starts) == 2

    async def test_node_yields_no_events(self, chat_store, agent_store, acai_config):
        """A node whose execute() yields nothing still completes without error."""
        spec = {
            "id": "wf-empty-gen",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "silent_node", "data": {"label": "Silent"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _SilentNode:
            type = "silent_node"
            async def execute(self, ctx):
                return
                yield  # noqa: B901 — makes it an async generator

        graph = _make_graph(chat_store, agent_store, acai_config, conversation="")
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"silent_node": _SilentNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        types = [e["event_type"] for e in events]
        assert "node_end" in types
        assert "workflow_end" in types
        node_end = next(e for e in events if e["event_type"] == "node_end" and e["data"]["type"] == "silent_node")
        assert node_end["data"]["output_preview"] == ""

    async def test_conversation_persistence_text_only_no_reasoning(
        self, chat_store, agent_store, acai_config,
    ):
        """When final_text is accumulated but no reasoning, the message is
        persisted without a reasoning field."""
        spec = {
            "id": "wf-persist-noreasing",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "text_only_node", "data": {"label": "T"}},
                {"id": "o", "type": "output", "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "n1", "target": "o",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _TextOnlyNode:
            type = "text_only_node"
            async def execute(self, ctx):
                yield {"type": "event", "data": {
                    "event_type": "token",
                    "data": {"token": "just text"},
                }}
                yield {"type": "output", "data": {"text": "just text"}}

        conv = chat_store.create(title="text only test")
        chat_store.append(conv.id, {"role": "user", "content": "hello"})

        graph = _make_graph(chat_store, agent_store, acai_config, conversation=conv.id)
        with patch("acai.tasks.nodes.get", side_effect=_mock_node_get({"text_only_node": _TextOnlyNode()})):
            events = await _collect(graph, {"workflow_spec": spec, "message": "x"})

        messages = chat_store.read(conv.id)
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["content"] == "just text"
        assert "reasoning" not in assistant_msgs[0]


# ======================================================================
# DynamicGraph — data-flow hardening tests
# ======================================================================


@pytest.mark.asyncio
class TestDynamicGraphDataFlowHardening:
    """Rigorous tests for the DynamicGraph's data-flow pipeline covering
    JSON parsing failures, exception propagation, ForEach resilience,
    max-step limits, unresolved data pins, and warning propagation."""

    async def test_llm_output_bad_json_for_downstream(
        self, chat_store, agent_store, acai_config,
    ):
        """Upstream node outputs text that downstream expects as JSON.
        Downstream handles parsing failure gracefully (no graph crash)."""
        spec = {
            "id": "wf-bad-json",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "producer", "type": "text_producer",
                 "data": {"label": "Producer"}},
                {"id": "consumer", "type": "json_consumer",
                 "data": {"label": "Consumer"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "producer",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "producer", "target": "consumer",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e-data", "source": "producer", "target": "consumer",
                 "sourceHandle": "data_text", "targetHandle": "data_input",
                 "type": "data"},
            ],
        }

        class _TextProducerNode:
            type = "text_producer"
            async def execute(self, ctx):
                yield {"type": "output", "data": {
                    "text": "This is not valid JSON at all {broken",
                }}

        class _JsonConsumerNode:
            type = "json_consumer"
            async def execute(self, ctx):
                raw = ctx.inputs.get("input", "")
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    yield {"type": "warning", "data": {
                        "message": f"Failed to parse input as JSON: "
                                   f"{str(raw)[:100]}",
                    }}
                    parsed = {}
                yield {"type": "output", "data": {"parsed": parsed}}

        graph = _make_graph(chat_store, agent_store, acai_config,
                            conversation="")
        overrides = {
            "text_producer": _TextProducerNode(),
            "json_consumer": _JsonConsumerNode(),
        }
        with patch("acai.tasks.nodes.get",
                    side_effect=_mock_node_get(overrides)):
            events = await _collect(graph, {
                "workflow_spec": spec, "message": "x",
            })

        types = [e["event_type"] for e in events]
        assert "error" not in types
        warnings = [e for e in events if e["event_type"] == "warning"]
        assert len(warnings) == 1
        assert "Failed to parse" in warnings[0]["data"]["message"]

    async def test_node_exception_includes_node_id_and_traceback(
        self, chat_store, agent_store, acai_config,
    ):
        """Node raises exception → error event includes node ID and traceback."""
        spec = {
            "id": "wf-exc",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "bad_node_42", "type": "exploder",
                 "data": {"label": "Boom"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "bad_node_42",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _ExploderNode:
            type = "exploder"
            async def execute(self, ctx):
                raise RuntimeError("unexpected null pointer")
                yield  # noqa: B901

        graph = _make_graph(chat_store, agent_store, acai_config,
                            conversation="")
        with patch("acai.tasks.nodes.get",
                    side_effect=_mock_node_get(
                        {"exploder": _ExploderNode()})):
            events = await _collect(graph, {
                "workflow_spec": spec, "message": "x",
            })

        error = next(e for e in events if e["event_type"] == "error")
        assert "bad_node_42" in error["data"]["message"]
        assert "unexpected null pointer" in error["data"]["message"]
        assert "traceback" in error["data"]
        assert "RuntimeError" in error["data"]["traceback"]

    async def test_foreach_one_item_fails_continues_loop(
        self, chat_store, agent_store, acai_config,
    ):
        """ForEach: one iteration fails → warning emitted, loop continues
        with remaining items, After node still executes."""
        spec = {
            "id": "wf-fe-fail",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "fe", "type": "for_each", "data": {
                    "label": "Loop",
                    "array": ["ok1", "FAIL", "ok2"],
                }},
                {"id": "body", "type": "maybe_fail",
                 "data": {"label": "Body"}},
                {"id": "after", "type": "print",
                 "data": {"label": "After"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "fe",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e2", "source": "fe", "target": "body",
                 "sourceHandle": "exec_body", "type": "exec"},
                {"id": "e3", "source": "fe", "target": "after",
                 "sourceHandle": "exec_then", "type": "exec"},
                {"id": "e-data", "source": "fe", "target": "body",
                 "sourceHandle": "data_item",
                 "targetHandle": "data_value", "type": "data"},
            ],
        }

        class _MaybeFailNode:
            type = "maybe_fail"
            async def execute(self, ctx):
                val = ctx.inputs.get("value", "")
                if val == "FAIL":
                    raise ValueError("item processing failed")
                yield {"type": "output", "data": {
                    "result": f"processed:{val}",
                }}

        graph = _make_graph(chat_store, agent_store, acai_config,
                            conversation="")
        with patch("acai.tasks.nodes.get",
                    side_effect=_mock_node_get(
                        {"maybe_fail": _MaybeFailNode()})):
            events = await _collect(graph, {
                "workflow_spec": spec, "message": "x",
            })

        types = [e["event_type"] for e in events]
        assert "error" not in types
        warnings = [e for e in events if e["event_type"] == "warning"]
        assert any("item processing failed" in w["data"]["message"]
                    for w in warnings)
        labels = [
            e["data"].get("label", "")
            for e in events if e["event_type"] == "node_start"
        ]
        assert "After" in labels

    async def test_max_steps_exceeded_error_message(
        self, chat_store, agent_store, acai_config,
    ):
        """Max steps exceeded → clear error mentioning 'max steps'."""
        spec = {
            "id": "wf-inf",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "n1", "type": "print",
                 "data": {"label": "Looper"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e-loop", "source": "n1", "target": "n1",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config,
                            conversation="")
        events = await _collect(graph, {
            "workflow_spec": spec, "message": "x",
        })

        errors = [e for e in events if e["event_type"] == "error"]
        assert len(errors) == 1
        assert "max steps" in errors[0]["data"]["message"].lower()

    async def test_data_pin_source_not_executed(
        self, chat_store, agent_store, acai_config,
    ):
        """Data pin references source that hasn't executed → no crash,
        pin value is absent (connected pin blocks inline fallback)."""
        spec = {
            "id": "wf-unresolved",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "unexecuted", "type": "print",
                 "data": {"label": "Never Runs"}},
                {"id": "consumer", "type": "print", "data": {
                    "label": "Consumer", "value": "default_val"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "consumer",
                 "sourceHandle": "exec_out", "type": "exec"},
                {"id": "e-data", "source": "unexecuted",
                 "target": "consumer",
                 "sourceHandle": "data_output",
                 "targetHandle": "data_value", "type": "data"},
            ],
        }
        graph = _make_graph(chat_store, agent_store, acai_config,
                            conversation="")
        events = await _collect(graph, {
            "workflow_spec": spec, "message": "x",
        })

        types = [e["event_type"] for e in events]
        assert "error" not in types
        print_events = [e for e in events
                        if e["event_type"] == "print"]
        assert len(print_events) == 1
        assert print_events[0]["data"]["text"] == "null"

    async def test_warning_event_propagates_to_graph_output(
        self, chat_store, agent_store, acai_config,
    ):
        """Node emits warning event → propagated to graph output stream."""
        spec = {
            "id": "wf-warn",
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "warner", "type": "warning_node",
                 "data": {"label": "Warner"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "warner",
                 "sourceHandle": "exec_out", "type": "exec"},
            ],
        }

        class _WarningNode:
            type = "warning_node"
            async def execute(self, ctx):
                yield {"type": "warning", "data": {
                    "message": "Something looks suspicious",
                }}
                yield {"type": "output", "data": {
                    "result": "ok despite warning",
                }}

        graph = _make_graph(chat_store, agent_store, acai_config,
                            conversation="")
        with patch("acai.tasks.nodes.get",
                    side_effect=_mock_node_get(
                        {"warning_node": _WarningNode()})):
            events = await _collect(graph, {
                "workflow_spec": spec, "message": "x",
            })

        types = [e["event_type"] for e in events]
        assert "error" not in types
        assert "warning" in types
        warnings = [e for e in events
                    if e["event_type"] == "warning"]
        assert len(warnings) == 1
        assert "Something looks suspicious" in (
            warnings[0]["data"]["message"]
        )
