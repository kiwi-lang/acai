"""Tests for acai.tasks.nodes — workflow node types."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from acai.tasks.nodes import (
    LoadKnowledgeNode,
    ReadReplyNode,
    AppendNode,
    ExtendNode,
    ContentNode,
    RoleNode,
    ConditionNode,
    SetVariableNode,
    GetVariableNode,
    ReasoningMessageNode,
    PrintNode,
    StartNode,
    OutputNode,
    FetchConversationNode,
    ReplyTypeNode,
    AgentCallNode,
    AccumulateNode,
    SimpleAgentNode,
    StreamTransformNode,
    ToolFollowUpLoopNode,
    BackgroundAgentNode,
    SkillCallNode,
    ToolCallNode,
    TTSAccumulateNode,
    NodeType,
    Pin,
    Colors,
    NodeContext,
    pin_types_compatible,
    substitute,
    all_types,
    get as get_node_type,
    register,
    describe_registry,
    _extra_context,
    _extract_json_text,
    _fields_to_schema,
)


class _FakeConfig:
    def __init__(self, workspace):
        self.workspace = workspace


class _FakeGraph:
    def __init__(self, workspace):
        self.config = _FakeConfig(workspace)


class _FakeNodeContext:
    def __init__(self, inputs, data, graph):
        self.inputs = inputs
        self.data = data
        self.graph = graph
        self.node_id = "test_node"
        self.work = {}


class TestLoadKnowledgeNode:

    @pytest.fixture
    def knowledge_dir(self, tmp_path):
        kd = tmp_path / "knowledge"
        kd.mkdir()
        return kd

    @pytest.fixture
    def graph(self, tmp_path):
        return _FakeGraph(str(tmp_path))

    async def _execute(self, node, ctx):
        outputs = {}
        async for event in node.execute(ctx):
            if event.get("type") == "output":
                outputs.update(event["data"])
        return outputs

    @pytest.mark.asyncio
    async def test_loads_existing_docs(self, tmp_path, graph):
        kd = tmp_path / "knowledge" / "topic" / "sub"
        kd.mkdir(parents=True)
        (kd / "doc.md").write_text("This is the document content.")

        ctx = _FakeNodeContext(
            inputs={"paths": ["topic/sub/doc"]},
            data={"label": "Load Knowledge"},
            graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs = await self._execute(node, ctx)

        assert "knowledge_context" in outputs
        assert "This is the document content." in outputs["knowledge_context"]

    @pytest.mark.asyncio
    async def test_empty_paths_returns_empty(self, tmp_path, graph):
        ctx = _FakeNodeContext(
            inputs={"paths": []},
            data={"label": "Load Knowledge"},
            graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs = await self._execute(node, ctx)
        assert outputs["knowledge_context"] == ""

    @pytest.mark.asyncio
    async def test_missing_doc_skipped(self, tmp_path, graph):
        (tmp_path / "knowledge").mkdir()
        ctx = _FakeNodeContext(
            inputs={"paths": ["nonexistent/path/doc"]},
            data={"label": "Load Knowledge"},
            graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs = await self._execute(node, ctx)
        assert outputs["knowledge_context"] == ""

    @pytest.mark.asyncio
    async def test_empty_content_doc_skipped(self, tmp_path, graph):
        kd = tmp_path / "knowledge" / "a" / "b"
        kd.mkdir(parents=True)
        (kd / "empty.md").write_text("")

        ctx = _FakeNodeContext(
            inputs={"paths": ["a/b/empty"]},
            data={"label": "Load Knowledge"},
            graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs = await self._execute(node, ctx)
        assert outputs["knowledge_context"] == ""

    @pytest.mark.asyncio
    async def test_multiple_docs_joined(self, tmp_path, graph):
        kd = tmp_path / "knowledge"
        (kd / "a" / "b").mkdir(parents=True)
        (kd / "c" / "d").mkdir(parents=True)
        (kd / "a" / "b" / "doc1.md").write_text("First document.")
        (kd / "c" / "d" / "doc2.md").write_text("Second document.")

        ctx = _FakeNodeContext(
            inputs={"paths": ["a/b/doc1", "c/d/doc2"]},
            data={"label": "Load Knowledge"},
            graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs = await self._execute(node, ctx)
        assert "First document." in outputs["knowledge_context"]
        assert "Second document." in outputs["knowledge_context"]
        assert "---" in outputs["knowledge_context"]

    @pytest.mark.asyncio
    async def test_string_paths_parsed(self, tmp_path, graph):
        kd = tmp_path / "knowledge" / "x" / "y"
        kd.mkdir(parents=True)
        (kd / "z.md").write_text("parsed ok")

        ctx = _FakeNodeContext(
            inputs={"paths": '["x/y/z"]'},
            data={"label": "Load Knowledge"},
            graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs = await self._execute(node, ctx)
        assert "parsed ok" in outputs["knowledge_context"]


class TestReadReplyNode:

    async def _execute(self, node, ctx):
        outputs = {}
        async for event in node.execute(ctx):
            if event.get("type") == "output":
                outputs.update(event["data"])
        return outputs

    @pytest.mark.asyncio
    async def test_parses_json_object(self):
        ctx = _FakeNodeContext(
            inputs={
                "reply": {"content": '{"paths": ["a/b/c", "d/e/f"]}'},
                "reply_type": {"properties": {"paths": {"type": "array"}}},
            },
            data={"label": "Read Reply"},
            graph=None,
        )
        node = ReadReplyNode()
        outputs = await self._execute(node, ctx)
        assert outputs["paths"] == ["a/b/c", "d/e/f"]

    @pytest.mark.asyncio
    async def test_parses_json_with_code_fences(self):
        content = '```json\n{"paths": ["x/y/z"]}\n```'
        ctx = _FakeNodeContext(
            inputs={
                "reply": {"content": content},
                "reply_type": {},
            },
            data={"label": "Read Reply"},
            graph=None,
        )
        node = ReadReplyNode()
        outputs = await self._execute(node, ctx)
        assert outputs["paths"] == ["x/y/z"]

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self):
        ctx = _FakeNodeContext(
            inputs={
                "reply": {"content": "not json at all"},
                "reply_type": {},
            },
            data={"label": "Read Reply"},
            graph=None,
        )
        node = ReadReplyNode()
        outputs = await self._execute(node, ctx)
        assert outputs == {}

    @pytest.mark.asyncio
    async def test_empty_content(self):
        ctx = _FakeNodeContext(
            inputs={
                "reply": {"content": ""},
                "reply_type": {},
            },
            data={"label": "Read Reply"},
            graph=None,
        )
        node = ReadReplyNode()
        outputs = await self._execute(node, ctx)
        assert outputs == {}


# -- Helper for all data node tests -----------------------------------------

async def _run_node(node, inputs, data=None, work=None):
    """Execute a node and collect all outputs/events."""
    ctx = _FakeNodeContext(inputs=inputs, data=data or {}, graph=None)
    if work is not None:
        ctx.work = work
    outputs = {}
    events = []
    async for event in node.execute(ctx):
        if event.get("type") == "output":
            outputs.update(event["data"])
        elif event.get("type") == "event":
            events.append(event["data"])
    return outputs, events, ctx


# -- AppendNode tests --------------------------------------------------------

class TestAppendNode:

    @pytest.mark.asyncio
    async def test_append_dict_to_list(self):
        node = AppendNode()
        outputs, _, _ = await _run_node(node, {
            "a": [{"role": "user", "content": "hi"}],
            "b": {"role": "assistant", "content": "hello"},
        })
        assert len(outputs["result"]) == 2
        assert outputs["result"][1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_append_list_extends(self):
        node = AppendNode()
        outputs, _, _ = await _run_node(node, {
            "a": [{"role": "user", "content": "a"}],
            "b": [{"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}],
        })
        assert len(outputs["result"]) == 3

    @pytest.mark.asyncio
    async def test_append_string_wraps_in_message(self):
        node = AppendNode()
        outputs, _, _ = await _run_node(node, {
            "a": [],
            "b": "just text",
        })
        assert outputs["result"][0]["role"] == "assistant"
        assert outputs["result"][0]["content"] == "just text"

    @pytest.mark.asyncio
    async def test_append_to_empty(self):
        node = AppendNode()
        outputs, _, _ = await _run_node(node, {
            "a": [],
            "b": {"role": "system", "content": "sys"},
        })
        assert len(outputs["result"]) == 1

    @pytest.mark.asyncio
    async def test_non_list_a_coerced(self):
        node = AppendNode()
        outputs, _, _ = await _run_node(node, {
            "a": {"role": "user", "content": "single"},
            "b": {"role": "assistant", "content": "response"},
        })
        assert len(outputs["result"]) == 2


# -- ExtendNode tests -------------------------------------------------------

class TestExtendNode:

    @pytest.mark.asyncio
    async def test_merge_two_lists(self):
        node = ExtendNode()
        outputs, _, _ = await _run_node(node, {
            "a": [{"role": "user", "content": "1"}],
            "b": [{"role": "assistant", "content": "2"}],
        })
        assert len(outputs["result"]) == 2

    @pytest.mark.asyncio
    async def test_empty_lists(self):
        node = ExtendNode()
        outputs, _, _ = await _run_node(node, {"a": [], "b": []})
        assert outputs["result"] == []

    @pytest.mark.asyncio
    async def test_non_list_coerced(self):
        node = ExtendNode()
        outputs, _, _ = await _run_node(node, {
            "a": "single_a",
            "b": "single_b",
        })
        assert len(outputs["result"]) == 2


# -- ContentNode tests -------------------------------------------------------

class TestContentNode:

    @pytest.mark.asyncio
    async def test_extracts_content_from_dict(self):
        node = ContentNode()
        outputs, _, _ = await _run_node(node, {
            "message": {"role": "assistant", "content": "hello world"},
        })
        assert outputs["content"] == "hello world"

    @pytest.mark.asyncio
    async def test_empty_message(self):
        node = ContentNode()
        outputs, _, _ = await _run_node(node, {"message": {}})
        assert outputs["content"] == ""

    @pytest.mark.asyncio
    async def test_non_dict_stringified(self):
        node = ContentNode()
        outputs, _, _ = await _run_node(node, {"message": 42})
        assert outputs["content"] == "42"


# -- RoleNode tests ----------------------------------------------------------

class TestRoleNode:

    @pytest.mark.asyncio
    async def test_extracts_role(self):
        node = RoleNode()
        outputs, _, _ = await _run_node(node, {
            "message": {"role": "user", "content": "hi"},
        })
        assert outputs["role"] == "user"

    @pytest.mark.asyncio
    async def test_empty_message(self):
        node = RoleNode()
        outputs, _, _ = await _run_node(node, {"message": {}})
        assert outputs["role"] == ""

    @pytest.mark.asyncio
    async def test_non_dict_empty(self):
        node = RoleNode()
        outputs, _, _ = await _run_node(node, {"message": "string"})
        assert outputs["role"] == ""


# -- ConditionNode tests -----------------------------------------------------

class TestConditionNode:

    @pytest.mark.asyncio
    async def test_true_expression(self):
        node = ConditionNode()
        outputs, _, _ = await _run_node(
            node,
            {"value": "hello"},
            data={"expression": "len(input) > 3"},
        )
        assert outputs["_condition"] is True

    @pytest.mark.asyncio
    async def test_false_expression(self):
        node = ConditionNode()
        outputs, _, _ = await _run_node(
            node,
            {"value": "hi"},
            data={"expression": "len(input) > 5"},
        )
        assert outputs["_condition"] is False

    @pytest.mark.asyncio
    async def test_default_expression_is_true(self):
        node = ConditionNode()
        outputs, _, _ = await _run_node(node, {"value": ""}, data={})
        assert outputs["_condition"] is True

    @pytest.mark.asyncio
    async def test_invalid_expression_defaults_true(self):
        node = ConditionNode()
        outputs, _, _ = await _run_node(
            node, {"value": "x"}, data={"expression": "undefined_var"},
        )
        assert outputs["_condition"] is True

    @pytest.mark.asyncio
    async def test_list_value_serialized(self):
        node = ConditionNode()
        outputs, _, _ = await _run_node(
            node,
            {"value": [1, 2, 3]},
            data={"expression": "'1' in input"},
        )
        assert outputs["_condition"] is True


# -- SetVariableNode / GetVariableNode tests ---------------------------------

class TestSetVariableNode:

    @pytest.mark.asyncio
    async def test_stores_value(self):
        node = SetVariableNode()
        work = {}
        outputs, events, ctx = await _run_node(
            node, {"name": "counter", "value": 42}, work=work,
        )
        assert work["_variables"]["counter"] == 42
        assert any("variable_set" in str(e) for e in events)

    @pytest.mark.asyncio
    async def test_empty_name_skips(self):
        node = SetVariableNode()
        work = {}
        outputs, events, ctx = await _run_node(
            node, {"name": "", "value": "ignored"}, work=work,
        )
        assert "_variables" not in work

    @pytest.mark.asyncio
    async def test_name_from_data_fallback(self):
        node = SetVariableNode()
        work = {}
        outputs, _, ctx = await _run_node(
            node, {"name": "", "value": "val"},
            data={"name": "from_data"}, work=work,
        )
        assert work["_variables"]["from_data"] == "val"


class TestGetVariableNode:

    @pytest.mark.asyncio
    async def test_retrieves_stored_value(self):
        node = GetVariableNode()
        work = {"_variables": {"key": "stored_value"}}
        outputs, _, _ = await _run_node(
            node, {"name": "key"}, work=work,
        )
        assert outputs["value"] == "stored_value"

    @pytest.mark.asyncio
    async def test_returns_default_when_missing(self):
        node = GetVariableNode()
        work = {"_variables": {}}
        outputs, _, _ = await _run_node(
            node, {"name": "missing", "default": "fallback"}, work=work,
        )
        assert outputs["value"] == "fallback"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_default(self):
        node = GetVariableNode()
        work = {}
        outputs, _, _ = await _run_node(
            node, {"name": "x"}, work=work,
        )
        assert outputs["value"] is None


# -- ReasoningMessageNode tests ----------------------------------------------

class TestReasoningMessageNode:

    @pytest.mark.asyncio
    async def test_wraps_reasoning_in_system_message(self):
        node = ReasoningMessageNode()
        outputs, _, _ = await _run_node(node, {"reasoning": "Step 1: think hard"})
        msg = outputs["message"]
        assert msg["role"] == "system"
        assert "Step 1: think hard" in msg["content"]
        assert "Prior Reasoning" in msg["content"]

    @pytest.mark.asyncio
    async def test_empty_reasoning_returns_none(self):
        node = ReasoningMessageNode()
        outputs, _, _ = await _run_node(node, {"reasoning": ""})
        assert outputs["message"] is None


# -- PrintNode tests ---------------------------------------------------------

class TestPrintNode:

    @pytest.mark.asyncio
    async def test_prints_value_as_json(self):
        node = PrintNode()
        outputs, events, _ = await _run_node(
            node, {"value": {"key": "val"}}, data={"label": "Debug"},
        )
        assert len(events) == 1
        assert events[0]["event_type"] == "print"
        assert '"key"' in events[0]["data"]["text"]
        assert events[0]["data"]["label"] == "Debug"

    @pytest.mark.asyncio
    async def test_prints_none(self):
        node = PrintNode()
        outputs, events, _ = await _run_node(node, {})
        assert events[0]["data"]["text"] == "null"

    @pytest.mark.asyncio
    async def test_prints_string(self):
        node = PrintNode()
        outputs, events, _ = await _run_node(node, {"value": "hello"})
        assert '"hello"' in events[0]["data"]["text"]


# ==================================================================
# Registry utilities
# ==================================================================

class TestRegistry:
    def test_all_types_non_empty(self):
        types = all_types()
        assert len(types) > 10
        type_ids = [t.type for t in types]
        assert "start" in type_ids
        assert "agent_call" in type_ids
        assert "output" in type_ids

    def test_get_existing(self):
        nt = get_node_type("start")
        assert nt is not None
        assert nt.type == "start"

    def test_get_nonexistent(self):
        assert get_node_type("no_such_type_xyz") is None


# ==================================================================
# Helper functions
# ==================================================================

class TestSubstitute:
    def test_basic(self):
        assert substitute("Hello {{name}}!", {"name": "world"}) == "Hello world!"

    def test_multiple_vars(self):
        result = substitute("{{a}} and {{b}}", {"a": "1", "b": "2"})
        assert result == "1 and 2"

    def test_missing_var_preserved(self):
        assert substitute("{{missing}}", {}) == "{{missing}}"

    def test_empty_template(self):
        assert substitute("", {"x": "y"}) == ""


class TestPinTypesCompatible:
    def test_same_types(self):
        assert pin_types_compatible("string", "string") is True

    def test_any_source(self):
        assert pin_types_compatible("any", "int") is True

    def test_any_target(self):
        assert pin_types_compatible("float", "any") is True

    def test_incompatible(self):
        assert pin_types_compatible("string", "int") is False


class TestExtractJsonText:
    def test_plain_json(self):
        assert _extract_json_text('{"a": 1}') == '{"a": 1}'

    def test_fenced_json(self):
        text = '```json\n{"a": 1}\n```'
        assert _extract_json_text(text) == '{"a": 1}'

    def test_fenced_no_lang(self):
        text = '```\n{"a": 1}\n```'
        assert _extract_json_text(text) == '{"a": 1}'

    def test_whitespace(self):
        assert _extract_json_text("  \n hello \n  ") == "hello"


class TestFieldsToSchema:
    def test_empty(self):
        schema = _fields_to_schema([])
        assert schema["properties"] == {}
        assert schema["required"] == []

    def test_basic_fields(self):
        fields = [
            {"name": "title", "type": "str"},
            {"name": "count", "type": "int"},
        ]
        schema = _fields_to_schema(fields)
        assert schema["properties"]["title"] == {"type": "string"}
        assert schema["properties"]["count"] == {"type": "integer"}
        assert schema["required"] == ["title", "count"]

    def test_skips_empty_name(self):
        fields = [{"name": "", "type": "str"}, {"name": "ok", "type": "bool"}]
        schema = _fields_to_schema(fields)
        assert "ok" in schema["properties"]
        assert len(schema["properties"]) == 1


# ==================================================================
# Pin
# ==================================================================

class TestPin:
    def test_exec_in(self):
        p = Pin.exec_in()
        assert p.id == "exec_in"
        assert p.kind == "exec"
        assert p.side == "left"

    def test_exec_out(self):
        p = Pin.exec_out()
        assert p.id == "exec_out"
        assert p.kind == "exec"
        assert p.side == "right"

    def test_data_pin(self):
        p = Pin.data("my_pin", "My Pin", Colors.blue, "left", pin_type="int")
        assert p.id == "my_pin"
        assert p.label == "My Pin"
        assert p.pin_type == "int"
        assert p.kind == "data"

    def test_to_dict(self):
        p = Pin.data("x", "X", Colors.green, "right", choices=("a", "b"))
        d = p.to_dict()
        assert d["id"] == "x"
        assert d["choices"] == ["a", "b"]


# ==================================================================
# StartNode
# ==================================================================

class TestStartNode:
    @pytest.mark.asyncio
    async def test_outputs_message_and_agent(self):
        node = StartNode()
        graph = MagicMock()
        graph.conversation = ""
        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={}, work={"message": "hi", "agent": "coder", "model": "gpt-4"},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert len(results) == 1
        out = results[0]["data"]
        assert out["message"] == {"role": "user", "content": "hi"}
        assert out["agent"] == "coder"
        assert out["model"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_uses_preview_message_fallback(self):
        node = StartNode()
        graph = MagicMock()
        graph.conversation = ""
        ctx = NodeContext(
            graph=graph, node_id="n1",
            data={"preview_message": "preview"},
            inputs={}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0]["data"]["message"]["content"] == "preview"


# ==================================================================
# OutputNode
# ==================================================================

class TestOutputNode:
    @pytest.mark.asyncio
    async def test_list_stream(self):
        node = OutputNode()
        graph = MagicMock()
        events_in = [{"token": "a"}, {"token": "b"}]
        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"stream": events_in}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0] == {"type": "event", "data": {"token": "a"}}
        assert results[1] == {"type": "event", "data": {"token": "b"}}
        assert results[2] == {"type": "output", "data": {}}

    @pytest.mark.asyncio
    async def test_async_stream(self):
        node = OutputNode()
        graph = MagicMock()

        async def _gen():
            yield {"chunk": 1}
            yield {"chunk": 2}

        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"stream": _gen()}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0] == {"type": "event", "data": {"chunk": 1}}
        assert results[-1] == {"type": "output", "data": {}}

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        node = OutputNode()
        graph = MagicMock()
        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results == [{"type": "output", "data": {}}]


# ==================================================================
# FetchConversationNode
# ==================================================================

class TestFetchConversationNode:
    @pytest.mark.asyncio
    async def test_debug_mode_from_work(self):
        node = FetchConversationNode()
        graph = MagicMock()
        msgs = [{"role": "user", "content": "hi"}]
        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"debug": True}, work={"test_conversation": msgs},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0]["data"]["conversation"] == msgs

    @pytest.mark.asyncio
    async def test_debug_mode_string_json(self):
        node = FetchConversationNode()
        graph = MagicMock()
        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"debug": "true"},
            work={"test_conversation": '[{"role":"user","content":"x"}]'},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0]["data"]["conversation"] == [{"role": "user", "content": "x"}]

    @pytest.mark.asyncio
    async def test_non_debug_reads_chat(self):
        node = FetchConversationNode()
        graph = MagicMock()
        graph.chat.read.return_value = [{"role": "assistant", "content": "ok"}]
        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"debug": False, "conversation_id": "conv-123"},
            work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        graph.chat.read.assert_called_once_with("conv-123")
        assert results[0]["data"]["conversation"] == [{"role": "assistant", "content": "ok"}]

    @pytest.mark.asyncio
    async def test_non_debug_no_conv_id(self):
        node = FetchConversationNode()
        graph = MagicMock()
        del graph.chat
        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"debug": False}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0]["data"]["conversation"] == []


# ==================================================================
# ReplyTypeNode
# ==================================================================

class TestReplyTypeNode:
    @pytest.mark.asyncio
    async def test_builds_schema(self):
        node = ReplyTypeNode()
        graph = MagicMock()
        fields = json.dumps([{"name": "title", "type": "str"}, {"name": "age", "type": "int"}])
        ctx = NodeContext(
            graph=graph, node_id="n1", data={"fields": fields},
            inputs={}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        schema = results[0]["data"]["format"]
        assert schema["properties"]["title"] == {"type": "string"}
        assert schema["properties"]["age"] == {"type": "integer"}
        assert "title" in schema["required"]

    @pytest.mark.asyncio
    async def test_invalid_fields_json(self):
        node = ReplyTypeNode()
        graph = MagicMock()
        ctx = NodeContext(
            graph=graph, node_id="n1", data={"fields": "not-json"},
            inputs={}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        schema = results[0]["data"]["format"]
        assert schema["properties"] == {}


# ==================================================================
# NodeContext properties
# ==================================================================

class TestNodeContext:
    def test_properties(self):
        graph = MagicMock()
        ctx = NodeContext(
            graph=graph, node_id="n1", data={}, inputs={},
            work={"agent": "my-agent", "provider": "openai", "model": "gpt-4", "enable_thinking": True},
        )
        assert ctx.agent_name == "my-agent"
        assert ctx.provider == "openai"
        assert ctx.model == "gpt-4"
        assert ctx.enable_thinking is True

    def test_defaults(self):
        graph = MagicMock()
        ctx = NodeContext(graph=graph, node_id="n", data={}, inputs={}, work={})
        assert ctx.agent_name == "default"
        assert ctx.provider == "auto"
        assert ctx.model == ""
        assert ctx.enable_thinking is None


# ==================================================================
# Helpers for Acc-based node tests
# ==================================================================

class _FakeAcc:
    """Lightweight stand-in for ``acai.tasks.graph.Acc``."""

    def __init__(self, stream=None, *, events=None, text="",
                 reasoning="", tool_calls=None):
        self._events = list(events or [])
        self.text = text
        self.reasoning = reasoning
        self.tool_calls = tool_calls or []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


async def _collect(node, ctx):
    """Run a node and return ``(outputs_dict, events_list)``."""
    outputs: dict = {}
    events: list = []
    async for item in node.execute(ctx):
        if item.get("type") == "output":
            outputs.update(item["data"])
        elif item.get("type") == "event":
            events.append(item["data"])
    return outputs, events


# ==================================================================
# Pin — additional edge cases
# ==================================================================

class TestPinEdgeCases:
    def test_to_dict_includes_dynamic_choices(self):
        p = Pin.data("d", "D", Colors.green, "left", dynamic_choices="agents")
        d = p.to_dict()
        assert d["dynamic_choices"] == "agents"

    def test_to_dict_omits_empty_choices_and_dynamic(self):
        p = Pin.data("d", "D", Colors.green, "left")
        d = p.to_dict()
        assert "choices" not in d
        assert "dynamic_choices" not in d

    def test_exec_constructor(self):
        p = Pin.exec("my_exec", "My Exec", Colors.red, "right")
        assert p.id == "my_exec"
        assert p.kind == "exec"
        assert p.label == "My Exec"

    def test_data_pin_list_choices_converted_to_tuple(self):
        p = Pin.data("d", "D", Colors.green, "left", choices=["x", "y"])
        assert isinstance(p.choices, tuple)

    def test_data_pin_non_optional(self):
        d = Pin.data("d", "D", Colors.green, "left", optional=False).to_dict()
        assert d["optional"] is False


# ==================================================================
# NodeType base class
# ==================================================================

class TestNodeTypeBase:
    def test_dynamic_pins_returns_empty(self):
        assert NodeType.dynamic_pins({}) == []

    @pytest.mark.asyncio
    async def test_execute_yields_empty_output(self):
        nt = NodeType()
        ctx = NodeContext(graph=MagicMock(), node_id="n", data={}, inputs={}, work={})
        results = []
        async for item in nt.execute(ctx):
            results.append(item)
        assert results == [{"type": "output", "data": {}}]

    def test_to_dict_structure(self):
        nt = NodeType()
        nt.type = "custom"
        nt.label = "Custom"
        nt.accent = "#abc"
        nt.description = "desc"
        nt.category = "Test"
        d = nt.to_dict()
        assert d["type"] == "custom"
        assert d["label"] == "Custom"
        assert d["accent"] == "#abc"
        assert d["description"] == "desc"
        assert d["category"] == "Test"
        assert isinstance(d["pins"], list)


# ==================================================================
# Registry — error paths
# ==================================================================

class TestRegisterErrors:
    def test_empty_type_raises_valueerror(self):
        with pytest.raises(ValueError, match="must be a non-empty string"):
            @register
            class _BadNode(NodeType):
                type = ""
                label = "Bad"


# ==================================================================
# describe_registry
# ==================================================================

class TestDescribeRegistry:
    def test_produces_markdown_with_sections(self):
        text = describe_registry()
        assert "## Available Node Types" in text
        assert "## Pin Type System" in text

    def test_includes_known_node_types(self):
        text = describe_registry()
        assert "**start**" in text
        assert "**agent_call**" in text
        assert "**condition**" in text

    def test_includes_pin_type_info(self):
        text = describe_registry()
        assert "`any`" in text
        assert "Pin types:" in text


# ==================================================================
# _extra_context
# ==================================================================

class TestExtraContext:
    def test_collects_custom_data_keys(self):
        ctx = NodeContext(
            graph=MagicMock(), node_id="n",
            data={"custom_key": "val", "label": "skip"},
            inputs={}, work={},
        )
        extra = _extra_context(ctx)
        assert extra == {"custom_key": "val"}

    def test_collects_custom_input_keys(self):
        ctx = NodeContext(
            graph=MagicMock(), node_id="n",
            data={},
            inputs={"custom_input": "val", "context": "skip", "agent": "skip"},
            work={},
        )
        extra = _extra_context(ctx)
        assert extra == {"custom_input": "val"}

    def test_returns_none_when_all_keys_filtered(self):
        ctx = NodeContext(
            graph=MagicMock(), node_id="n",
            data={"label": "x", "agent": "a"},
            inputs={"context": "c", "stream_mode": "token"},
            work={},
        )
        assert _extra_context(ctx) is None

    def test_skips_underscore_prefixed_keys(self):
        ctx = NodeContext(
            graph=MagicMock(), node_id="n",
            data={"_hidden": "skip", "visible": "keep"},
            inputs={"_priv": "skip"}, work={},
        )
        extra = _extra_context(ctx)
        assert "_hidden" not in extra
        assert "_priv" not in extra
        assert extra == {"visible": "keep"}

    def test_filters_all_reserved_input_keys(self):
        reserved = ["agent", "context", "stream_mode", "phase", "format", "force_format"]
        ctx = NodeContext(
            graph=MagicMock(), node_id="n",
            data={}, inputs={k: "v" for k in reserved}, work={},
        )
        assert _extra_context(ctx) is None


# ==================================================================
# AgentCallNode
# ==================================================================

class TestAgentCallNode:
    @pytest.mark.asyncio
    async def test_basic_empty_context(self):
        node = AgentCallNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "mock_stream"

        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={}, work={"message": "hi"},
        )
        outputs, _ = await _collect(node, ctx)
        assert "stream" in outputs
        assert "payload" in outputs
        graph.prepare.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_list_replaces_non_system_messages(self):
        node = AgentCallNode()
        graph = MagicMock()
        graph.prepare.return_value = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "old"},
            ]
        }
        graph.dispatch.return_value = "stream"
        context = [{"role": "user", "content": "new"}]

        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"context": context}, work={"message": "hi"},
        )
        outputs, _ = await _collect(node, ctx)
        payload = outputs["payload"]
        assert payload["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "new"},
        ]

    @pytest.mark.asyncio
    async def test_context_as_string(self):
        node = AgentCallNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"

        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"context": "plain text"}, work={"message": "hi"},
        )
        await _collect(node, ctx)
        agent_work = graph.prepare.call_args[0][1]
        assert agent_work["message"] == "plain text"

    @pytest.mark.asyncio
    async def test_prompt_template_substitution(self):
        node = AgentCallNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"

        ctx = NodeContext(
            graph=graph, node_id="n1",
            data={"prompt_template": "Q: {{message}}"},
            inputs={"context": []}, work={"message": "what?"},
        )
        await _collect(node, ctx)
        assert graph.prepare.call_args[0][1]["message"] == "Q: what?"

    @pytest.mark.asyncio
    async def test_format_sets_extra_context(self):
        node = AgentCallNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fmt = {"type": "object", "properties": {"x": {"type": "string"}}}

        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"context": [], "format": fmt, "force_format": False},
            work={"message": "hi"},
        )
        outputs, _ = await _collect(node, ctx)
        assert "response_format" not in outputs["payload"]
        kwargs = graph.prepare.call_args[1]
        assert kwargs["extra_context"]["response_format_schema"] is fmt

    @pytest.mark.asyncio
    async def test_force_format_adds_response_format(self):
        node = AgentCallNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fmt = {"title": "Out", "type": "object", "properties": {}}

        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"context": [], "format": fmt, "force_format": True},
            work={"message": "hi"},
        )
        outputs, _ = await _collect(node, ctx)
        rf = outputs["payload"]["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "Out"
        assert rf["json_schema"]["strict"] is True

    @pytest.mark.asyncio
    async def test_force_format_default_title(self):
        node = AgentCallNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fmt = {"type": "object", "properties": {}}

        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"context": [], "format": fmt, "force_format": True},
            work={"message": "hi"},
        )
        outputs, _ = await _collect(node, ctx)
        assert outputs["payload"]["response_format"]["json_schema"]["name"] == "structured_output"

    @pytest.mark.asyncio
    async def test_agent_name_fallback_to_data(self):
        node = AgentCallNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"

        ctx = NodeContext(
            graph=graph, node_id="n1", data={"agent": "custom"},
            inputs={}, work={"message": "hi"},
        )
        await _collect(node, ctx)
        assert graph.prepare.call_args[0][0] == "custom"

    @pytest.mark.asyncio
    async def test_stream_mode_from_data(self):
        node = AgentCallNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"

        ctx = NodeContext(
            graph=graph, node_id="n1", data={"stream_mode": "silent"},
            inputs={}, work={"message": "hi"},
        )
        await _collect(node, ctx)
        assert graph.dispatch.call_args[1]["stream_mode"] == "silent"

    @pytest.mark.asyncio
    async def test_context_list_items_with_non_dict_filtered(self):
        node = AgentCallNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        context = [
            {"role": "user", "content": "hi"},
            "not-a-dict",
            {"role": "assistant", "content": "hey"},
        ]

        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"context": context}, work={"message": "hi"},
        )
        await _collect(node, ctx)
        agent_work = graph.prepare.call_args[0][1]
        assert "not-a-dict" not in agent_work["message"]


# ==================================================================
# AccumulateNode
# ==================================================================

class TestAccumulateNode:
    @pytest.mark.asyncio
    async def test_none_stream_returns_empty(self):
        node = AccumulateNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={}, inputs={}, work={},
        )
        outputs, events = await _collect(node, ctx)
        assert outputs["text"] == ""
        assert outputs["reasoning"] == ""
        assert outputs["tool_calls"] == []
        assert outputs["response"]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_accumulates_stream_events(self):
        node = AccumulateNode()
        fake = _FakeAcc(
            events=[
                {"event_type": "token", "data": {"token": "hi"}},
                {"event_type": "done"},
            ],
            text="hi", reasoning="", tool_calls=[],
        )
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=MagicMock(), node_id="n1", data={},
                inputs={"stream": "mock"}, work={},
            )
            outputs, events = await _collect(node, ctx)
        assert outputs["text"] == "hi"
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_event_mode_silent_suppresses_all_events(self):
        node = AccumulateNode()
        fake = _FakeAcc(
            events=[{"event_type": "token", "data": {"token": "x"}}],
            text="x",
        )
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=MagicMock(), node_id="n1", data={},
                inputs={"stream": "s", "event_mode": "silent"}, work={},
            )
            _, events = await _collect(node, ctx)
        assert events == []

    @pytest.mark.asyncio
    async def test_event_mode_relabels_token_events(self):
        node = AccumulateNode()
        fake = _FakeAcc(
            events=[{"event_type": "token", "data": {"token": "y"}}],
            text="y",
        )
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=MagicMock(), node_id="n1", data={},
                inputs={"stream": "s", "event_mode": "reasoning"}, work={},
            )
            _, events = await _collect(node, ctx)
        assert events[0]["event_type"] == "reasoning"

    @pytest.mark.asyncio
    async def test_event_mode_from_data_fallback(self):
        node = AccumulateNode()
        fake = _FakeAcc(
            events=[{"event_type": "reasoning", "data": {}}],
            text="",
        )
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=MagicMock(), node_id="n1",
                data={"event_mode": "token"},
                inputs={"stream": "s"}, work={},
            )
            _, events = await _collect(node, ctx)
        assert events[0]["event_type"] == "token"

    @pytest.mark.asyncio
    async def test_non_token_events_not_relabeled(self):
        node = AccumulateNode()
        fake = _FakeAcc(
            events=[{"event_type": "tool_start", "data": {}}],
            text="",
        )
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=MagicMock(), node_id="n1", data={},
                inputs={"stream": "s", "event_mode": "reasoning"}, work={},
            )
            _, events = await _collect(node, ctx)
        assert events[0]["event_type"] == "tool_start"


# ==================================================================
# TTSAccumulateNode — None-stream fast path
# ==================================================================

class TestTTSAccumulateNode:
    @pytest.mark.asyncio
    async def test_none_stream_returns_empty(self):
        node = TTSAccumulateNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={}, inputs={}, work={},
        )
        outputs, _ = await _collect(node, ctx)
        assert outputs["text"] == ""
        assert outputs["reasoning"] == ""
        assert outputs["tool_calls"] == []


# ==================================================================
# SimpleAgentNode
# ==================================================================

class TestSimpleAgentNode:
    @pytest.mark.asyncio
    async def test_basic_execution(self):
        node = SimpleAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(
            events=[{"event_type": "token", "data": {"token": "hi"}}],
            text="hi", reasoning="think", tool_calls=[],
        )
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={}, work={"message": "hello"},
            )
            outputs, events = await _collect(node, ctx)
        assert outputs["text"] == "hi"
        assert outputs["reasoning"] == "think"
        assert outputs["payload"] == {"messages": []}
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_silent_mode_suppresses_events(self):
        node = SimpleAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(
            events=[{"event_type": "token", "data": {"token": "x"}}],
            text="x",
        )
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1",
                data={"stream_mode": "silent"},
                inputs={}, work={"message": "hi"},
            )
            _, events = await _collect(node, ctx)
        assert events == []

    @pytest.mark.asyncio
    async def test_context_as_string(self):
        node = SimpleAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(events=[], text="")
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={"context": "raw text"}, work={"message": "hi"},
            )
            await _collect(node, ctx)
        assert graph.prepare.call_args[0][1]["message"] == "raw text"

    @pytest.mark.asyncio
    async def test_context_list_replaces_messages(self):
        node = SimpleAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "old"},
            ]
        }
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(events=[], text="ok")
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={"context": [{"role": "user", "content": "new"}]},
                work={"message": "hi"},
            )
            outputs, _ = await _collect(node, ctx)
        assert outputs["payload"]["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "new"},
        ]

    @pytest.mark.asyncio
    async def test_format_with_force_format(self):
        node = SimpleAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(events=[], text="")
        fmt = {"title": "Res", "type": "object"}
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={"context": [], "format": fmt, "force_format": True},
                work={"message": "hi"},
            )
            outputs, _ = await _collect(node, ctx)
        assert outputs["payload"]["response_format"]["json_schema"]["name"] == "Res"

    @pytest.mark.asyncio
    async def test_prompt_template_substitution(self):
        node = SimpleAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(events=[], text="")
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1",
                data={"prompt_template": "Do: {{message}}"},
                inputs={"context": []}, work={"message": "task"},
            )
            await _collect(node, ctx)
        assert graph.prepare.call_args[0][1]["message"] == "Do: task"


# ==================================================================
# StreamTransformNode
# ==================================================================

class TestStreamTransformNode:
    @pytest.mark.asyncio
    async def test_transforms_token_to_target_mode(self):
        node = StreamTransformNode()

        async def _stream():
            yield {"event_type": "token", "data": {"token": "hi"}}
            yield {"event_type": "done"}

        ctx = NodeContext(
            graph=MagicMock(), node_id="n1",
            data={"target_mode": "reasoning"},
            inputs={"stream": _stream()}, work={},
        )
        outputs, _ = await _collect(node, ctx)
        events = [e async for e in outputs["stream_out"]]
        assert events[0]["event_type"] == "reasoning"
        assert events[1]["event_type"] == "done"

    @pytest.mark.asyncio
    async def test_none_stream_outputs_empty_generator(self):
        node = StreamTransformNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1",
            data={"target_mode": "reasoning"},
            inputs={}, work={},
        )
        outputs, _ = await _collect(node, ctx)
        events = [e async for e in outputs["stream_out"]]
        assert events == []

    @pytest.mark.asyncio
    async def test_default_target_mode_is_reasoning(self):
        node = StreamTransformNode()

        async def _stream():
            yield {"event_type": "token", "data": {"token": "t"}}

        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={},
            inputs={"stream": _stream()}, work={},
        )
        outputs, _ = await _collect(node, ctx)
        events = [e async for e in outputs["stream_out"]]
        assert events[0]["event_type"] == "reasoning"


# ==================================================================
# ToolFollowUpLoopNode
# ==================================================================

class TestToolFollowUpLoopNode:
    @pytest.mark.asyncio
    async def test_no_tool_calls_passes_response_through(self):
        node = ToolFollowUpLoopNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={},
            inputs={
                "response": {"role": "assistant", "content": "done"},
                "tool_calls": [],
                "payload": {"messages": []},
            },
            work={},
        )
        outputs, _ = await _collect(node, ctx)
        assert outputs["response"]["content"] == "done"
        assert outputs["messages"] == []

    @pytest.mark.asyncio
    async def test_tool_dispatch_error_produces_clear_message(self):
        node = ToolFollowUpLoopNode()
        graph = MagicMock()
        graph.dispatch_tool = AsyncMock(side_effect=RuntimeError("connection refused"))
        graph.dispatch.return_value = "stream"

        tool_calls = [{
            "id": "call_1",
            "function": {"name": "broken_tool", "arguments": "{}"},
        }]
        fake = _FakeAcc(events=[], text="final", tool_calls=[])
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={
                    "response": {"role": "assistant", "content": ""},
                    "tool_calls": tool_calls,
                    "payload": {"messages": []},
                },
                work={},
            )
            outputs, events = await _collect(node, ctx)

        tool_end = [e for e in events if e.get("event_type") == "tool_end"]
        assert "[Tool error] RuntimeError: connection refused" in tool_end[0]["data"]["result_preview"]

    @pytest.mark.asyncio
    async def test_invalid_json_arguments_default_to_empty_dict(self):
        node = ToolFollowUpLoopNode()
        graph = MagicMock()
        graph.dispatch_tool = AsyncMock(return_value="ok")
        graph.dispatch.return_value = "stream"

        tool_calls = [{
            "id": "call_1",
            "function": {"name": "my_tool", "arguments": "<<<invalid>>>"},
        }]
        fake = _FakeAcc(events=[], text="ok", tool_calls=[])
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={
                    "response": {"role": "assistant", "content": ""},
                    "tool_calls": tool_calls,
                    "payload": {"messages": []},
                },
                work={},
            )
            await _collect(node, ctx)
        graph.dispatch_tool.assert_awaited_once_with("my_tool", {})

    @pytest.mark.asyncio
    async def test_follow_up_false_stops_after_tool_dispatch(self):
        node = ToolFollowUpLoopNode()
        graph = MagicMock()
        graph.dispatch_tool = AsyncMock(return_value="result")

        tool_calls = [{
            "id": "call_1",
            "function": {"name": "tool1", "arguments": "{}"},
        }]
        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={
                "response": {"role": "assistant", "content": "initial"},
                "tool_calls": tool_calls,
                "payload": {"messages": []},
                "follow_up": False,
            },
            work={},
        )
        outputs, _ = await _collect(node, ctx)
        assert outputs["response"]["content"] == "initial"
        graph.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_follow_up_string_false_variants(self):
        node = ToolFollowUpLoopNode()
        graph = MagicMock()
        graph.dispatch_tool = AsyncMock(return_value="result")

        for val in ["false", "0", "no", "off"]:
            tool_calls = [{
                "id": "c1",
                "function": {"name": "t", "arguments": "{}"},
            }]
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={
                    "response": {"role": "assistant", "content": "x"},
                    "tool_calls": tool_calls,
                    "payload": {"messages": []},
                    "follow_up": val,
                },
                work={},
            )
            outputs, _ = await _collect(node, ctx)
            assert outputs["response"]["content"] == "x"

    @pytest.mark.asyncio
    async def test_multi_round_tool_loop(self):
        node = ToolFollowUpLoopNode()
        graph = MagicMock()
        graph.dispatch_tool = AsyncMock(return_value="tool_result")
        graph.dispatch.return_value = "stream"

        initial_calls = [{
            "id": "c1",
            "function": {"name": "search", "arguments": "{}"},
        }]
        round2_calls = [{
            "id": "c2",
            "function": {"name": "summarize", "arguments": "{}"},
        }]

        acc1 = _FakeAcc(events=[], text="mid", tool_calls=round2_calls)
        acc2 = _FakeAcc(events=[], text="final answer", tool_calls=[])

        with patch("acai.tasks.graph.Acc", side_effect=[acc1, acc2]):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={
                    "response": {"role": "assistant", "content": ""},
                    "tool_calls": initial_calls,
                    "payload": {"messages": [{"role": "user", "content": "q"}]},
                },
                work={},
            )
            outputs, _ = await _collect(node, ctx)

        assert outputs["response"]["content"] == "final answer"
        assert graph.dispatch_tool.await_count == 2

    @pytest.mark.asyncio
    async def test_event_mode_relabels_follow_up_events(self):
        node = ToolFollowUpLoopNode()
        graph = MagicMock()
        graph.dispatch_tool = AsyncMock(return_value="ok")
        graph.dispatch.return_value = "stream"

        tool_calls = [{
            "id": "c1",
            "function": {"name": "t", "arguments": "{}"},
        }]
        fake = _FakeAcc(
            events=[{"event_type": "token", "data": {"token": "x"}}],
            text="x", tool_calls=[],
        )
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={
                    "response": {"role": "assistant", "content": ""},
                    "tool_calls": tool_calls,
                    "payload": {"messages": []},
                    "event_mode": "reasoning",
                },
                work={},
            )
            _, events = await _collect(node, ctx)
        relabeled = [e for e in events if e.get("event_type") == "reasoning"]
        assert len(relabeled) == 1

    @pytest.mark.asyncio
    async def test_response_non_dict_extracts_empty_content(self):
        node = ToolFollowUpLoopNode()
        graph = MagicMock()
        graph.dispatch_tool = AsyncMock(return_value="ok")
        graph.dispatch.return_value = "stream"

        tool_calls = [{
            "id": "c1",
            "function": {"name": "t", "arguments": "{}"},
        }]
        fake = _FakeAcc(events=[], text="done", tool_calls=[])
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={
                    "response": "string_response",
                    "tool_calls": tool_calls,
                    "payload": {"messages": []},
                },
                work={},
            )
            outputs, _ = await _collect(node, ctx)
        assert outputs["response"]["content"] == "done"

    @pytest.mark.asyncio
    async def test_none_arguments_handled(self):
        node = ToolFollowUpLoopNode()
        graph = MagicMock()
        graph.dispatch_tool = AsyncMock(return_value="ok")
        graph.dispatch.return_value = "stream"

        tool_calls = [{
            "id": "c1",
            "function": {"name": "t", "arguments": None},
        }]
        fake = _FakeAcc(events=[], text="ok", tool_calls=[])
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={
                    "response": {"role": "assistant", "content": ""},
                    "tool_calls": tool_calls,
                    "payload": {"messages": []},
                },
                work={},
            )
            await _collect(node, ctx)
        graph.dispatch_tool.assert_awaited_once_with("t", {})


# ==================================================================
# BackgroundAgentNode
# ==================================================================

class TestBackgroundAgentNode:
    @pytest.mark.asyncio
    async def test_basic_no_tools(self):
        node = BackgroundAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(
            events=[{"event_type": "token", "data": {"token": "hi"}}],
            text="hi", tool_calls=[],
        )
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1",
                data={"label": "my agent"},
                inputs={}, work={"message": "hello"},
            )
            outputs, events = await _collect(node, ctx)

        assert outputs["text"] == "hi"
        types = [e["event_type"] for e in events]
        assert "my_agent_start" in types
        assert "my_agent_end" in types
        assert "my_agent_token" in types

    @pytest.mark.asyncio
    async def test_phase_from_input(self):
        node = BackgroundAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(events=[], text="", tool_calls=[])
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={},
                inputs={"phase": "analysis"}, work={"message": "hi"},
            )
            _, events = await _collect(node, ctx)
        types = [e["event_type"] for e in events]
        assert "analysis_start" in types
        assert "analysis_end" in types

    @pytest.mark.asyncio
    async def test_tool_dispatch_error_produces_clear_message(self):
        node = BackgroundAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        graph.dispatch_tool = AsyncMock(side_effect=ValueError("bad args"))

        tool_calls = [{
            "id": "c1",
            "function": {"name": "broken", "arguments": "{}"},
        }]
        acc1 = _FakeAcc(events=[], text="thinking", tool_calls=tool_calls)
        acc2 = _FakeAcc(events=[], text="final", tool_calls=[])
        with patch("acai.tasks.graph.Acc", side_effect=[acc1, acc2]):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={"phase": "work"},
                inputs={}, work={"message": "hi"},
            )
            outputs, events = await _collect(node, ctx)

        tool_end = [e for e in events if e.get("event_type") == "work_tool_end"]
        assert "[Tool error] ValueError: bad args" in tool_end[0]["data"]["result_preview"]
        assert outputs["text"] == "final"

    @pytest.mark.asyncio
    async def test_invalid_json_tool_arguments_default_to_empty(self):
        node = BackgroundAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        graph.dispatch_tool = AsyncMock(return_value="ok")

        tool_calls = [{
            "id": "c1",
            "function": {"name": "my_tool", "arguments": "<<<invalid>>>"},
        }]
        acc1 = _FakeAcc(events=[], text="x", tool_calls=tool_calls)
        acc2 = _FakeAcc(events=[], text="done", tool_calls=[])
        with patch("acai.tasks.graph.Acc", side_effect=[acc1, acc2]):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={"phase": "run"},
                inputs={}, work={"message": "go"},
            )
            await _collect(node, ctx)
        graph.dispatch_tool.assert_awaited_with("my_tool", {})

    @pytest.mark.asyncio
    async def test_context_string_conversion(self):
        node = BackgroundAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(events=[], text="", tool_calls=[])
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={"phase": "t"},
                inputs={"context": "plain text"}, work={"message": "hi"},
            )
            await _collect(node, ctx)
        assert graph.prepare.call_args[0][1]["message"] == "plain text"

    @pytest.mark.asyncio
    async def test_context_list_replaces_messages(self):
        node = BackgroundAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "old"},
            ]
        }
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(events=[], text="", tool_calls=[])
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={"phase": "t"},
                inputs={"context": [{"role": "user", "content": "new"}]},
                work={"message": "hi"},
            )
            await _collect(node, ctx)

    @pytest.mark.asyncio
    async def test_format_with_force_format(self):
        node = BackgroundAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(events=[], text="", tool_calls=[])
        fmt = {"title": "BG", "type": "object"}
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={"phase": "bg"},
                inputs={"format": fmt, "force_format": True},
                work={"message": "hi"},
            )
            await _collect(node, ctx)

    @pytest.mark.asyncio
    async def test_non_token_events_forwarded_unchanged(self):
        node = BackgroundAgentNode()
        graph = MagicMock()
        graph.prepare.return_value = {"messages": []}
        graph.dispatch.return_value = "stream"
        fake = _FakeAcc(
            events=[{"event_type": "tool_progress", "data": {"status": "ok"}}],
            text="", tool_calls=[],
        )
        with patch("acai.tasks.graph.Acc", return_value=fake):
            ctx = NodeContext(
                graph=graph, node_id="n1", data={"phase": "bg"},
                inputs={}, work={"message": "hi"},
            )
            _, events = await _collect(node, ctx)
        non_phase = [e for e in events if e["event_type"] == "tool_progress"]
        assert len(non_phase) == 1


# ==================================================================
# PrintNode — edge cases
# ==================================================================

class TestPrintNodeEdgeCases:
    @pytest.mark.asyncio
    async def test_circular_reference_falls_back_to_str(self):
        node = PrintNode()
        d: dict = {}
        d["self"] = d
        outputs, events, _ = await _run_node(node, {"value": d})
        assert len(events) == 1
        assert events[0]["data"]["text"] is not None

    @pytest.mark.asyncio
    async def test_label_defaults_to_print(self):
        node = PrintNode()
        _, events, _ = await _run_node(node, {"value": "x"}, data={})
        assert events[0]["data"]["label"] == "Print"


# ==================================================================
# LoadKnowledgeNode — edge cases
# ==================================================================

class TestLoadKnowledgeNodeEdgeCases:
    async def _execute(self, node, ctx):
        outputs = {}
        async for event in node.execute(ctx):
            if event.get("type") == "output":
                outputs.update(event["data"])
        return outputs

    @pytest.mark.asyncio
    async def test_comma_separated_string_paths(self, tmp_path):
        (tmp_path / "knowledge").mkdir()
        graph = _FakeGraph(str(tmp_path))
        ctx = _FakeNodeContext(
            inputs={"paths": "path1, path2, path3"},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs = await self._execute(node, ctx)
        assert outputs["knowledge_context"] == ""

    @pytest.mark.asyncio
    async def test_non_list_paths_coerced_to_empty(self, tmp_path):
        (tmp_path / "knowledge").mkdir()
        graph = _FakeGraph(str(tmp_path))
        ctx = _FakeNodeContext(
            inputs={"paths": 42}, data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs = await self._execute(node, ctx)
        assert outputs["knowledge_context"] == ""

    @pytest.mark.asyncio
    async def test_paths_truncated_to_10(self, tmp_path):
        kd = tmp_path / "knowledge" / "a" / "b"
        kd.mkdir(parents=True)
        for i in range(15):
            (kd / f"doc{i}.md").write_text(f"Content {i}")
        graph = _FakeGraph(str(tmp_path))
        paths = [f"a/b/doc{i}" for i in range(15)]
        ctx = _FakeNodeContext(
            inputs={"paths": paths}, data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs = await self._execute(node, ctx)
        count = outputs["knowledge_context"].count("Content ")
        assert count <= 10

    @pytest.mark.asyncio
    async def test_invalid_json_string_falls_back_to_comma_split(self, tmp_path):
        (tmp_path / "knowledge").mkdir()
        graph = _FakeGraph(str(tmp_path))
        ctx = _FakeNodeContext(
            inputs={"paths": "not-json-at-all"},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs = await self._execute(node, ctx)
        assert outputs["knowledge_context"] == ""


# ==================================================================
# FetchConversationNode — edge cases
# ==================================================================

class TestFetchConversationNodeEdgeCases:
    @pytest.mark.asyncio
    async def test_debug_string_yes(self):
        node = FetchConversationNode()
        msgs = [{"role": "user", "content": "hello"}]
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={},
            inputs={"debug": "yes"},
            work={"test_conversation": msgs},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0]["data"]["conversation"] == msgs

    @pytest.mark.asyncio
    async def test_debug_invalid_json_string_returns_empty(self):
        node = FetchConversationNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={},
            inputs={"debug": True},
            work={"test_conversation": "[invalid json"},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0]["data"]["conversation"] == []

    @pytest.mark.asyncio
    async def test_debug_non_bracket_string_returns_empty(self):
        node = FetchConversationNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={},
            inputs={"debug": True},
            work={"test_conversation": "just a string"},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0]["data"]["conversation"] == []

    @pytest.mark.asyncio
    async def test_conv_id_from_data_fallback(self):
        node = FetchConversationNode()
        graph = MagicMock()
        graph.chat.read.return_value = [{"role": "user", "content": "hi"}]
        ctx = NodeContext(
            graph=graph, node_id="n1",
            data={"conversation_id": "conv-from-data"},
            inputs={"debug": False}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        graph.chat.read.assert_called_once_with("conv-from-data")

    @pytest.mark.asyncio
    async def test_debug_from_data_fallback(self):
        node = FetchConversationNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1",
            data={"debug": True},
            inputs={}, work={"test_conversation": []},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0]["data"]["conversation"] == []


# ==================================================================
# SkillCallNode
# ==================================================================

class TestSkillCallNode:
    @pytest.mark.asyncio
    async def test_no_tool_selected_returns_empty(self):
        node = SkillCallNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={}, inputs={}, work={},
        )
        outputs, events = await _collect(node, ctx)
        assert outputs["result"] == ""
        assert events == []

    @pytest.mark.asyncio
    async def test_tool_from_data_fallback(self):
        node = SkillCallNode()
        graph = MagicMock()
        graph.dispatch_tool = AsyncMock(return_value="tool output")
        ctx = NodeContext(
            graph=graph, node_id="n1",
            data={"tool": "my_tool"},
            inputs={"custom_arg": "val"}, work={},
        )
        outputs, events = await _collect(node, ctx)
        assert outputs["result"] == "tool output"
        tool_start = [e for e in events if e.get("event_type") == "tool_start"]
        assert tool_start[0]["data"]["tool_name"] == "my_tool"

    @pytest.mark.asyncio
    async def test_tool_dispatch_error_clear_message(self):
        node = SkillCallNode()
        graph = MagicMock()
        graph.dispatch_tool = AsyncMock(side_effect=TimeoutError("timed out"))
        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"tool": "slow"}, work={},
        )
        outputs, _ = await _collect(node, ctx)
        assert "[Tool error] TimeoutError: timed out" in outputs["result"]

    @pytest.mark.asyncio
    async def test_result_preview_truncated_to_2000(self):
        node = SkillCallNode()
        graph = MagicMock()
        graph.dispatch_tool = AsyncMock(return_value="x" * 5000)
        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={"tool": "big"}, work={},
        )
        _, events = await _collect(node, ctx)
        tool_end = [e for e in events if e.get("event_type") == "tool_end"]
        assert len(tool_end[0]["data"]["result_preview"]) == 2000

    def test_dynamic_pins_no_tool_name(self):
        assert SkillCallNode.dynamic_pins({}, tool_defs=[]) == []

    def test_dynamic_pins_tool_not_found(self):
        pins = SkillCallNode.dynamic_pins(
            {"tool": "missing"},
            tool_defs=[{"function": {"name": "other"}}],
        )
        assert pins == []

    def test_dynamic_pins_generates_pins_from_schema(self):
        tool_defs = [{
            "function": {
                "name": "search",
                "parameters": {
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                        "verbose": {"type": "boolean"},
                    },
                    "required": ["query"],
                },
            }
        }]
        pins = SkillCallNode.dynamic_pins({"tool": "search"}, tool_defs=tool_defs)
        assert len(pins) == 3
        pin_ids = {p.id for p in pins}
        assert "data_query" in pin_ids
        query_pin = next(p for p in pins if p.id == "data_query")
        assert query_pin.optional is False
        limit_pin = next(p for p in pins if p.id == "data_limit")
        assert limit_pin.pin_type == "int"

    def test_dynamic_pins_unknown_json_type_defaults_to_string(self):
        tool_defs = [{
            "function": {
                "name": "t",
                "parameters": {
                    "properties": {"data": {"type": "unknown_type"}},
                    "required": [],
                },
            }
        }]
        pins = SkillCallNode.dynamic_pins({"tool": "t"}, tool_defs=tool_defs)
        assert pins[0].pin_type == "string"


# ==================================================================
# ToolCallNode (alias)
# ==================================================================

class TestToolCallNode:
    def test_is_alias_of_skill_call(self):
        node = ToolCallNode()
        assert node.type == "tool_call"
        assert node.label == "Tool Call"
        assert isinstance(node, SkillCallNode)


# ==================================================================
# ReadReplyNode — edge cases
# ==================================================================

class TestReadReplyNodeEdgeCases:
    @pytest.mark.asyncio
    async def test_non_dict_reply_uses_str(self):
        node = ReadReplyNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={},
            inputs={"reply": '{"a": 1}', "reply_type": {}}, work={},
        )
        outputs, _ = await _collect(node, ctx)
        assert outputs.get("a") == 1

    @pytest.mark.asyncio
    async def test_parsed_non_dict_produces_empty_output(self):
        node = ReadReplyNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={},
            inputs={"reply": {"content": "[1, 2, 3]"}, "reply_type": {}}, work={},
        )
        outputs, _ = await _collect(node, ctx)
        assert outputs == {}

    def test_dynamic_pins_no_spec_returns_empty(self):
        assert ReadReplyNode.dynamic_pins({}) == []

    def test_dynamic_pins_no_matching_edge(self):
        spec = {
            "nodes": [{"id": "n1", "type": "read_reply", "data": {}}],
            "edges": [],
        }
        assert ReadReplyNode.dynamic_pins({"_node_id": "n1"}, spec=spec) == []

    def test_dynamic_pins_with_reply_type_edge(self):
        spec = {
            "nodes": [
                {"id": "rt1", "type": "reply_type", "data": {
                    "fields": json.dumps([
                        {"name": "title", "type": "str"},
                        {"name": "score", "type": "float"},
                    ])
                }},
                {"id": "rr1", "type": "read_reply", "data": {"_node_id": "rr1"}},
            ],
            "edges": [{"source": "rt1", "target": "rr1"}],
        }
        pins = ReadReplyNode.dynamic_pins({"_node_id": "rr1"}, spec=spec)
        assert len(pins) == 2
        assert {p.id for p in pins} == {"data_title", "data_score"}

    def test_dynamic_pins_invalid_fields_json(self):
        spec = {
            "nodes": [
                {"id": "rt1", "type": "reply_type", "data": {"fields": "bad"}},
                {"id": "rr1", "type": "read_reply", "data": {}},
            ],
            "edges": [{"source": "rt1", "target": "rr1"}],
        }
        assert ReadReplyNode.dynamic_pins({"_node_id": "rr1"}, spec=spec) == []

    def test_dynamic_pins_skips_empty_field_names(self):
        spec = {
            "nodes": [
                {"id": "rt1", "type": "reply_type", "data": {
                    "fields": json.dumps([
                        {"name": "", "type": "str"},
                        {"name": "valid", "type": "int"},
                    ])
                }},
                {"id": "rr1", "type": "read_reply", "data": {}},
            ],
            "edges": [{"source": "rt1", "target": "rr1"}],
        }
        pins = ReadReplyNode.dynamic_pins({"_node_id": "rr1"}, spec=spec)
        assert len(pins) == 1
        assert pins[0].id == "data_valid"

    def test_dynamic_pins_source_not_reply_type(self):
        spec = {
            "nodes": [
                {"id": "other", "type": "start", "data": {}},
                {"id": "rr1", "type": "read_reply", "data": {}},
            ],
            "edges": [{"source": "other", "target": "rr1"}],
        }
        assert ReadReplyNode.dynamic_pins({"_node_id": "rr1"}, spec=spec) == []


# ==================================================================
# ReplyTypeNode — edge cases
# ==================================================================

class TestReplyTypeNodeEdgeCases:
    @pytest.mark.asyncio
    async def test_fields_as_list_not_string(self):
        node = ReplyTypeNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1",
            data={"fields": [{"name": "x", "type": "bool"}]},
            inputs={}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        schema = results[0]["data"]["format"]
        assert schema["properties"]["x"] == {"type": "boolean"}

    @pytest.mark.asyncio
    async def test_no_fields_key_defaults_empty(self):
        node = ReplyTypeNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={}, inputs={}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0]["data"]["format"]["properties"] == {}


# ==================================================================
# AppendNode — edge cases
# ==================================================================

class TestAppendNodeEdgeCases:
    @pytest.mark.asyncio
    async def test_falsy_b_not_appended(self):
        node = AppendNode()
        outputs, _, _ = await _run_node(node, {"a": [{"x": 1}], "b": ""})
        assert outputs["result"] == [{"x": 1}]

    @pytest.mark.asyncio
    async def test_none_b_not_appended(self):
        node = AppendNode()
        outputs, _, _ = await _run_node(node, {"a": [], "b": None})
        assert outputs["result"] == []

    @pytest.mark.asyncio
    async def test_falsy_non_list_a_coerced_to_empty(self):
        node = AppendNode()
        outputs, _, _ = await _run_node(
            node, {"a": "", "b": {"role": "user", "content": "hi"}},
        )
        assert outputs["result"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_empty_list_b_not_extended(self):
        node = AppendNode()
        outputs, _, _ = await _run_node(node, {"a": [1], "b": []})
        assert outputs["result"] == [1]


# ==================================================================
# ExtendNode — edge cases
# ==================================================================

class TestExtendNodeEdgeCases:
    @pytest.mark.asyncio
    async def test_falsy_a_becomes_empty(self):
        node = ExtendNode()
        outputs, _, _ = await _run_node(node, {"a": "", "b": ["x"]})
        assert outputs["result"] == ["x"]

    @pytest.mark.asyncio
    async def test_falsy_b_becomes_empty(self):
        node = ExtendNode()
        outputs, _, _ = await _run_node(node, {"a": ["x"], "b": ""})
        assert outputs["result"] == ["x"]

    @pytest.mark.asyncio
    async def test_none_inputs_produce_empty(self):
        node = ExtendNode()
        outputs, _, _ = await _run_node(node, {"a": None, "b": None})
        assert outputs["result"] == []


# ==================================================================
# _fields_to_schema — edge cases
# ==================================================================

class TestFieldsToSchemaEdgeCases:
    def test_array_types(self):
        fields = [
            {"name": "tags", "type": "str[]"},
            {"name": "ids", "type": "int[]"},
            {"name": "scores", "type": "float[]"},
            {"name": "flags", "type": "bool[]"},
        ]
        schema = _fields_to_schema(fields)
        assert schema["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
        assert schema["properties"]["ids"] == {"type": "array", "items": {"type": "integer"}}
        assert schema["properties"]["scores"] == {"type": "array", "items": {"type": "number"}}
        assert schema["properties"]["flags"] == {"type": "array", "items": {"type": "boolean"}}

    def test_unknown_type_defaults_to_string(self):
        schema = _fields_to_schema([{"name": "data", "type": "custom_type"}])
        assert schema["properties"]["data"] == {"type": "string"}

    def test_type_case_insensitive(self):
        schema = _fields_to_schema([
            {"name": "x", "type": "STRING"},
            {"name": "y", "type": "Boolean"},
        ])
        assert schema["properties"]["x"] == {"type": "string"}
        assert schema["properties"]["y"] == {"type": "boolean"}

    def test_whitespace_stripped(self):
        schema = _fields_to_schema([{"name": " title ", "type": " int "}])
        assert "title" in schema["properties"]
        assert schema["properties"]["title"] == {"type": "integer"}

    def test_additional_properties_false(self):
        schema = _fields_to_schema([{"name": "x", "type": "str"}])
        assert schema["additionalProperties"] is False


# ==================================================================
# StartNode — edge cases
# ==================================================================

class TestStartNodeEdgeCases:
    @pytest.mark.asyncio
    async def test_loads_conversation_when_present(self):
        node = StartNode()
        graph = MagicMock()
        graph.conversation = "conv-123"
        graph.chat.read.return_value = [{"role": "user", "content": "old msg"}]
        ctx = NodeContext(
            graph=graph, node_id="n1", data={},
            inputs={}, work={"message": "new"},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0]["data"]["conversation"] == [{"role": "user", "content": "old msg"}]

    @pytest.mark.asyncio
    async def test_no_message_yields_empty_content(self):
        node = StartNode()
        graph = MagicMock()
        graph.conversation = ""
        ctx = NodeContext(
            graph=graph, node_id="n1", data={}, inputs={}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0]["data"]["message"]["content"] == ""


# ==================================================================
# OutputNode — edge cases
# ==================================================================

class TestOutputNodeEdgeCases:
    @pytest.mark.asyncio
    async def test_non_iterable_stream_skipped(self):
        node = OutputNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={},
            inputs={"stream": 42}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results == [{"type": "output", "data": {}}]

    @pytest.mark.asyncio
    async def test_tuple_stream(self):
        node = OutputNode()
        ctx = NodeContext(
            graph=MagicMock(), node_id="n1", data={},
            inputs={"stream": ({"a": 1}, {"b": 2})}, work={},
        )
        results = []
        async for item in node.execute(ctx):
            results.append(item)
        assert results[0] == {"type": "event", "data": {"a": 1}}
        assert results[-1] == {"type": "output", "data": {}}


# ==================================================================
# ConditionNode — edge cases
# ==================================================================

class TestConditionNodeEdgeCases:
    @pytest.mark.asyncio
    async def test_dict_value_serialized_to_json(self):
        node = ConditionNode()
        outputs, _, _ = await _run_node(
            node,
            {"value": {"key": "val"}},
            data={"expression": "'key' in input"},
        )
        assert outputs["_condition"] is True

    @pytest.mark.asyncio
    async def test_missing_value_uses_empty_string(self):
        node = ConditionNode()
        outputs, _, _ = await _run_node(node, {}, data={"expression": "True"})
        assert outputs["_condition"] is True
        assert outputs["value"] == ""


# ==================================================================
# LoadKnowledgeNode — warning behavior with malformed paths
# ==================================================================

class TestLoadKnowledgeNodeWarnings:
    """Rigorous tests verifying LoadKnowledgeNode emits correct warnings
    and never crashes when receiving garbage or malformed paths input
    (e.g. from an upstream LLM agent)."""

    @pytest.fixture
    def graph(self, tmp_path):
        return _FakeGraph(str(tmp_path))

    async def _execute_full(self, node, ctx):
        """Execute node and collect outputs + warning events separately."""
        outputs = {}
        warnings = []
        async for event in node.execute(ctx):
            etype = event.get("type", "")
            if etype == "output":
                outputs.update(event["data"])
            elif etype == "warning":
                warnings.append(event["data"])
        return outputs, warnings

    @pytest.mark.asyncio
    async def test_json_array_string_loads_docs(self, tmp_path, graph):
        """paths as a valid JSON array string → loads documents correctly."""
        kd = tmp_path / "knowledge" / "topic" / "sub"
        kd.mkdir(parents=True)
        (kd / "doc.md").write_text("Loaded via JSON string.")

        ctx = _FakeNodeContext(
            inputs={"paths": '["topic/sub/doc"]'},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs, warnings = await self._execute_full(node, ctx)
        assert "Loaded via JSON string." in outputs["knowledge_context"]
        assert warnings == []

    @pytest.mark.asyncio
    async def test_comma_separated_string_splits(self, tmp_path, graph):
        """paths as a comma-separated string (not JSON) → split correctly."""
        kd1 = tmp_path / "knowledge" / "a" / "b"
        kd1.mkdir(parents=True)
        (kd1 / "doc1.md").write_text("First doc.")
        kd2 = tmp_path / "knowledge" / "c" / "d"
        kd2.mkdir(parents=True)
        (kd2 / "doc2.md").write_text("Second doc.")

        ctx = _FakeNodeContext(
            inputs={"paths": "a/b/doc1, c/d/doc2"},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs, warnings = await self._execute_full(node, ctx)
        assert "First doc." in outputs["knowledge_context"]
        assert "Second doc." in outputs["knowledge_context"]
        assert warnings == []

    @pytest.mark.asyncio
    async def test_empty_string_emits_warning(self, tmp_path, graph):
        """paths is an empty string → emits warning, returns empty context."""
        (tmp_path / "knowledge").mkdir()
        ctx = _FakeNodeContext(
            inputs={"paths": ""},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs, warnings = await self._execute_full(node, ctx)
        assert outputs["knowledge_context"] == ""
        assert len(warnings) == 1
        assert "empty string" in warnings[0]["message"].lower()

    @pytest.mark.asyncio
    async def test_none_paths_no_crash(self, tmp_path, graph):
        """paths is None → treated as empty list, no crash."""
        (tmp_path / "knowledge").mkdir()
        ctx = _FakeNodeContext(
            inputs={"paths": None},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs, warnings = await self._execute_full(node, ctx)
        assert outputs["knowledge_context"] == ""

    @pytest.mark.asyncio
    async def test_integer_emits_warning_with_type(self, tmp_path, graph):
        """paths is an integer → emits warning mentioning the type."""
        (tmp_path / "knowledge").mkdir()
        ctx = _FakeNodeContext(
            inputs={"paths": 42},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs, warnings = await self._execute_full(node, ctx)
        assert outputs["knowledge_context"] == ""
        assert len(warnings) == 1
        assert "int" in warnings[0]["message"]
        assert "non-list" in warnings[0]["message"].lower()

    @pytest.mark.asyncio
    async def test_dict_input_emits_warning_with_type(self, tmp_path, graph):
        """paths is a dict → emits warning mentioning dict type."""
        (tmp_path / "knowledge").mkdir()
        ctx = _FakeNodeContext(
            inputs={"paths": {"not": "a list"}},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs, warnings = await self._execute_full(node, ctx)
        assert outputs["knowledge_context"] == ""
        assert len(warnings) == 1
        assert "dict" in warnings[0]["message"]

    @pytest.mark.asyncio
    async def test_missing_docs_emits_warning_listing_paths(self, tmp_path, graph):
        """Valid list, docs don't exist → emits warning listing missing paths."""
        (tmp_path / "knowledge").mkdir()
        ctx = _FakeNodeContext(
            inputs={"paths": ["no/such/doc1", "no/such/doc2"]},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs, warnings = await self._execute_full(node, ctx)
        assert outputs["knowledge_context"] == ""
        assert len(warnings) == 1
        assert "2 path(s)" in warnings[0]["message"]
        assert "no/such/doc1" in warnings[0]["message"]
        assert "no/such/doc2" in warnings[0]["message"]

    @pytest.mark.asyncio
    async def test_partial_load_warns_missing(self, tmp_path, graph):
        """Some docs exist, some don't → partial load + warning for missing."""
        kd = tmp_path / "knowledge" / "a" / "b"
        kd.mkdir(parents=True)
        (kd / "exists.md").write_text("Real content.")

        ctx = _FakeNodeContext(
            inputs={"paths": ["a/b/exists", "x/y/missing"]},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs, warnings = await self._execute_full(node, ctx)
        assert "Real content." in outputs["knowledge_context"]
        assert len(warnings) == 1
        assert "1 path(s)" in warnings[0]["message"]
        assert "x/y/missing" in warnings[0]["message"]

    @pytest.mark.asyncio
    async def test_max_10_paths_processed(self, tmp_path, graph):
        """More than 10 items → only first 10 processed."""
        kd = tmp_path / "knowledge" / "a" / "b"
        kd.mkdir(parents=True)
        for i in range(15):
            (kd / f"d{i}.md").write_text(f"Content {i}")

        paths = [f"a/b/d{i}" for i in range(15)]
        ctx = _FakeNodeContext(
            inputs={"paths": paths},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs, warnings = await self._execute_full(node, ctx)
        loaded_count = outputs["knowledge_context"].count("Content ")
        assert loaded_count == 10
        assert "Content 10" not in outputs["knowledge_context"]
        assert "Content 14" not in outputs["knowledge_context"]

    @pytest.mark.asyncio
    async def test_non_string_items_converted_via_str(self, tmp_path, graph):
        """Non-string items in paths list → converted via str()."""
        (tmp_path / "knowledge").mkdir()
        ctx = _FakeNodeContext(
            inputs={"paths": [123, 45.6, True]},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        outputs, warnings = await self._execute_full(node, ctx)
        assert outputs["knowledge_context"] == ""
        assert len(warnings) == 1
        assert "3 path(s)" in warnings[0]["message"]
        assert "123" in warnings[0]["message"]

    @pytest.mark.asyncio
    async def test_store_exception_does_not_crash(self, tmp_path, graph):
        """KnowledgeStore.get_by_path raises → node doesn't crash."""
        (tmp_path / "knowledge").mkdir()
        ctx = _FakeNodeContext(
            inputs={"paths": ["a/b/doc"]},
            data={}, graph=graph,
        )
        node = LoadKnowledgeNode()
        with patch(
            "acai.knowledge.KnowledgeStore.get_by_path",
            side_effect=PermissionError("access denied"),
        ):
            outputs, warnings = await self._execute_full(node, ctx)
        assert outputs["knowledge_context"] == ""
        assert len(warnings) == 1
        assert "a/b/doc" in warnings[0]["message"]
