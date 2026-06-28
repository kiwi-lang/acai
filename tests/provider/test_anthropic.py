"""Unit tests for acai.provider.anthropic – AnthropicAdapter + fetch_models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest
import requests

from acai.provider.anthropic import AnthropicAdapter, fetch_models
from acai.provider.base import (
    ContentToken,
    LLMRequestError,
    ReasoningToken,
    StreamDone,
    ToolCallDelta,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeProviderConfig:
    """Minimal stand-in for ProviderConfig used by AnthropicAdapter."""
    endpoint: str = "https://api.anthropic.com/v1/messages"
    model_slug: str = "claude-3-opus"
    max_tokens: int = 4096
    temperature: float = 0.7
    api_key: str = "sk-test-key"


def _make_adapter(endpoint: str = "https://api.anthropic.com/v1/messages", **overrides) -> AnthropicAdapter:
    cfg = FakeProviderConfig(endpoint=endpoint, **overrides)
    return AnthropicAdapter(cfg)


def _sse_lines(events: list) -> list:
    """Build raw SSE lines from event dicts (or literal strings like '[DONE]')."""
    lines = []
    for ev in events:
        if isinstance(ev, str):
            lines.append(f"data: {ev}")
        else:
            lines.append(f"data: {json.dumps(ev)}")
    return lines


def _mock_stream_response(status_code: int, sse_events: list) -> MagicMock:
    """Create a mock requests.Response that mimics SSE streaming."""
    resp = MagicMock(spec=requests.Response)
    resp.ok = status_code < 400
    resp.status_code = status_code
    resp.iter_lines = MagicMock(return_value=iter(_sse_lines(sse_events)))
    return resp


def _mock_json_response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.ok = status_code < 400
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body)
    resp.text = json.dumps(body)
    return resp


# ===========================================================================
# Constructor / endpoint normalisation
# ===========================================================================

class TestAnthropicAdapterInit:
    def test_strips_v1_messages_suffix(self):
        adapter = _make_adapter("https://api.anthropic.com/v1/messages")
        assert adapter.endpoint == "https://api.anthropic.com"

    def test_strips_v1_suffix(self):
        adapter = _make_adapter("https://api.anthropic.com/v1")
        assert adapter.endpoint == "https://api.anthropic.com"

    def test_strips_trailing_slash(self):
        adapter = _make_adapter("https://api.anthropic.com/v1/messages/")
        assert adapter.endpoint == "https://api.anthropic.com"

    def test_bare_endpoint_unchanged(self):
        adapter = _make_adapter("https://custom-proxy.example.com")
        assert adapter.endpoint == "https://custom-proxy.example.com"

    def test_properties_stored(self):
        adapter = _make_adapter(model_slug="claude-sonnet", max_tokens=8192, temperature=0.5)
        assert adapter.model == "claude-sonnet"
        assert adapter.max_tokens == 8192
        assert adapter.temperature == 0.5
        assert adapter.api_key == "sk-test-key"

    def test_url_method(self):
        adapter = _make_adapter("https://api.anthropic.com/v1/messages")
        assert adapter._url() == "https://api.anthropic.com/v1/messages"

    def test_headers(self):
        adapter = _make_adapter()
        h = adapter._headers()
        assert h["x-api-key"] == "sk-test-key"
        assert h["anthropic-version"] == "2023-06-01"
        assert h["Content-Type"] == "application/json"


# ===========================================================================
# _split_system
# ===========================================================================

class TestSplitSystem:
    def test_no_system(self):
        msgs = [{"role": "user", "content": "hi"}]
        sys_text, rest = AnthropicAdapter._split_system(msgs)
        assert sys_text == ""
        assert rest == msgs

    def test_single_system(self):
        msgs = [{"role": "system", "content": "You are helpful"}, {"role": "user", "content": "hi"}]
        sys_text, rest = AnthropicAdapter._split_system(msgs)
        assert sys_text == "You are helpful"
        assert len(rest) == 1

    def test_multiple_system_messages(self):
        msgs = [
            {"role": "system", "content": "Part 1"},
            {"role": "system", "content": "Part 2"},
            {"role": "user", "content": "hi"},
        ]
        sys_text, rest = AnthropicAdapter._split_system(msgs)
        assert sys_text == "Part 1\n\nPart 2"
        assert len(rest) == 1

    def test_system_with_missing_content(self):
        msgs = [{"role": "system"}, {"role": "user", "content": "hi"}]
        sys_text, rest = AnthropicAdapter._split_system(msgs)
        assert sys_text == ""
        assert len(rest) == 1


# ===========================================================================
# _convert_messages
# ===========================================================================

class TestConvertMessages:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_simple_user_assistant(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = self.adapter._convert_messages(msgs)
        assert result == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

    def test_system_messages_skipped(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Hello"},
        ]
        result = self.adapter._convert_messages(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_tool_result_converted(self):
        msgs = [{"role": "tool", "tool_call_id": "tc_123", "content": "result data"}]
        result = self.adapter._convert_messages(msgs)
        assert result[0]["role"] == "user"
        assert result[0]["content"] == [{
            "type": "tool_result",
            "tool_use_id": "tc_123",
            "content": "result data",
        }]

    def test_assistant_with_tool_calls(self):
        msgs = [{
            "role": "assistant",
            "content": "Let me check",
            "tool_calls": [{
                "id": "tc_1",
                "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'},
            }],
        }]
        result = self.adapter._convert_messages(msgs)
        assert result[0]["role"] == "assistant"
        content = result[0]["content"]
        assert content[0] == {"type": "text", "text": "Let me check"}
        assert content[1]["type"] == "tool_use"
        assert content[1]["id"] == "tc_1"
        assert content[1]["name"] == "get_weather"
        assert content[1]["input"] == {"city": "NYC"}

    def test_assistant_tool_calls_without_content(self):
        msgs = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc_2", "function": {"name": "search", "arguments": "{}"}}],
        }]
        result = self.adapter._convert_messages(msgs)
        content = result[0]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "tool_use"

    def test_tool_calls_invalid_json_arguments(self):
        msgs = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc_3", "function": {"name": "fn", "arguments": "not json"}}],
        }]
        result = self.adapter._convert_messages(msgs)
        assert result[0]["content"][0]["input"] == {}

    def test_tool_calls_none_arguments(self):
        msgs = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc_4", "function": {"name": "fn", "arguments": None}}],
        }]
        result = self.adapter._convert_messages(msgs)
        assert result[0]["content"][0]["input"] == {}

    def test_merges_consecutive_same_role_strings(self):
        msgs = [
            {"role": "user", "content": "Part 1"},
            {"role": "user", "content": "Part 2"},
        ]
        result = self.adapter._convert_messages(msgs)
        assert len(result) == 1
        assert result[0]["content"] == "Part 1\n\nPart 2"

    def test_merges_consecutive_same_role_lists(self):
        msgs = [
            {"role": "tool", "tool_call_id": "a", "content": "r1"},
            {"role": "tool", "tool_call_id": "b", "content": "r2"},
        ]
        result = self.adapter._convert_messages(msgs)
        assert len(result) == 1
        assert len(result[0]["content"]) == 2

    def test_merges_string_then_list(self):
        msgs = [
            {"role": "user", "content": "preamble"},
            {"role": "tool", "tool_call_id": "x", "content": "res"},
        ]
        result = self.adapter._convert_messages(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        content = result[0]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "preamble"}

    def test_merges_list_then_string(self):
        msgs = [
            {"role": "tool", "tool_call_id": "x", "content": "res"},
            {"role": "user", "content": "follow-up"},
        ]
        result = self.adapter._convert_messages(msgs)
        assert len(result) == 1
        content = result[0]["content"]
        assert isinstance(content, list)
        assert content[-1] == {"type": "text", "text": "follow-up"}

    def test_missing_role_defaults_to_user(self):
        msgs = [{"content": "no role"}]
        result = self.adapter._convert_messages(msgs)
        assert result[0]["role"] == "user"

    def test_empty_messages(self):
        result = self.adapter._convert_messages([])
        assert result == []


# ===========================================================================
# _convert_tools
# ===========================================================================

class TestConvertTools:
    def test_basic_conversion(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }]
        result = AnthropicAdapter._convert_tools(tools)
        assert result == [{
            "name": "get_weather",
            "description": "Get the weather",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        }]

    def test_missing_parameters(self):
        tools = [{"function": {"name": "noop", "description": "no params"}}]
        result = AnthropicAdapter._convert_tools(tools)
        assert result[0]["input_schema"] == {"type": "object", "properties": {}}

    def test_empty_tools(self):
        assert AnthropicAdapter._convert_tools([]) == []


# ===========================================================================
# _payload
# ===========================================================================

class TestPayload:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_basic_payload(self):
        msgs = [{"role": "user", "content": "hi"}]
        p = self.adapter._payload(msgs, stream=True)
        assert p["model"] == "claude-3-opus"
        assert p["stream"] is True
        assert p["max_tokens"] == 4096
        assert p["temperature"] == 0.7
        assert "system" not in p
        assert "tools" not in p
        assert "thinking" not in p

    def test_payload_with_system(self):
        msgs = [
            {"role": "system", "content": "Be brief"},
            {"role": "user", "content": "hi"},
        ]
        p = self.adapter._payload(msgs)
        assert p["system"] == "Be brief"

    def test_payload_with_kwargs_override(self):
        msgs = [{"role": "user", "content": "hi"}]
        p = self.adapter._payload(msgs, model="claude-4", max_tokens=999, temperature=0.0)
        assert p["model"] == "claude-4"
        assert p["max_tokens"] == 999
        assert p["temperature"] == 0.0

    def test_payload_with_thinking(self):
        msgs = [{"role": "user", "content": "think"}]
        p = self.adapter._payload(msgs, enable_thinking=True)
        assert p["thinking"] == {"type": "enabled", "budget_tokens": 4096}

    def test_payload_with_tools(self):
        msgs = [{"role": "user", "content": "hi"}]
        tools = [{"function": {"name": "t", "description": "d", "parameters": {}}}]
        p = self.adapter._payload(msgs, tools=tools)
        assert "tools" in p
        assert p["tools"][0]["name"] == "t"


# ===========================================================================
# complete
# ===========================================================================

class TestComplete:
    def setup_method(self):
        self.adapter = _make_adapter()

    @patch("acai.provider.anthropic.requests.post")
    def test_returns_text(self, mock_post):
        mock_post.return_value = _mock_json_response(200, {
            "content": [{"type": "text", "text": "Hello!"}],
        })
        result = self.adapter.complete([{"role": "user", "content": "hi"}])
        assert result == "Hello!"

    @patch("acai.provider.anthropic.requests.post")
    def test_returns_empty_when_no_text_block(self, mock_post):
        mock_post.return_value = _mock_json_response(200, {"content": []})
        result = self.adapter.complete([{"role": "user", "content": "hi"}])
        assert result == ""

    @patch("acai.provider.anthropic.requests.post")
    def test_returns_first_text_block(self, mock_post):
        mock_post.return_value = _mock_json_response(200, {
            "content": [
                {"type": "tool_use", "id": "x", "name": "fn", "input": {}},
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
        })
        result = self.adapter.complete([{"role": "user", "content": "hi"}])
        assert result == "first"

    @patch("acai.provider.anthropic.requests.post")
    def test_raises_on_error(self, mock_post):
        resp = _mock_json_response(429, {"error": {"message": "rate limited"}})
        mock_post.return_value = resp
        with pytest.raises(LLMRequestError, match="429"):
            self.adapter.complete([{"role": "user", "content": "hi"}])


# ===========================================================================
# complete_raw
# ===========================================================================

class TestCompleteRaw:
    def setup_method(self):
        self.adapter = _make_adapter()

    @patch("acai.provider.anthropic.requests.post")
    def test_text_only_response(self, mock_post):
        mock_post.return_value = _mock_json_response(200, {
            "content": [{"type": "text", "text": "answer"}],
        })
        msg = self.adapter.complete_raw([{"role": "user", "content": "hi"}])
        assert msg["role"] == "assistant"
        assert msg["content"] == "answer"
        assert "tool_calls" not in msg

    @patch("acai.provider.anthropic.requests.post")
    def test_tool_use_response(self, mock_post):
        mock_post.return_value = _mock_json_response(200, {
            "content": [
                {"type": "text", "text": "I'll search"},
                {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "test"}},
            ],
        })
        msg = self.adapter.complete_raw([{"role": "user", "content": "find"}])
        assert msg["content"] == "I'll search"
        assert len(msg["tool_calls"]) == 1
        tc = msg["tool_calls"][0]
        assert tc["id"] == "tu_1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "search"
        assert json.loads(tc["function"]["arguments"]) == {"q": "test"}

    @patch("acai.provider.anthropic.requests.post")
    def test_with_tools_kwarg(self, mock_post):
        mock_post.return_value = _mock_json_response(200, {"content": [{"type": "text", "text": "ok"}]})
        tools = [{"function": {"name": "t", "description": "d", "parameters": {}}}]
        self.adapter.complete_raw([{"role": "user", "content": "hi"}], tools=tools)
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "tools" in payload

    @patch("acai.provider.anthropic.requests.post")
    def test_no_text_content_returns_none(self, mock_post):
        mock_post.return_value = _mock_json_response(200, {
            "content": [{"type": "tool_use", "id": "tu_2", "name": "fn", "input": {}}],
        })
        msg = self.adapter.complete_raw([{"role": "user", "content": "hi"}])
        assert msg["content"] is None


# ===========================================================================
# _to_openai_message
# ===========================================================================

class TestToOpenaiMessage:
    def test_text_only(self):
        data = {"content": [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}]}
        msg = AnthropicAdapter._to_openai_message(data)
        assert msg["content"] == "line1\nline2"
        assert "tool_calls" not in msg

    def test_tool_use_only(self):
        data = {"content": [{"type": "tool_use", "id": "x", "name": "fn", "input": {"a": 1}}]}
        msg = AnthropicAdapter._to_openai_message(data)
        assert msg["content"] is None
        assert len(msg["tool_calls"]) == 1

    def test_empty_content(self):
        msg = AnthropicAdapter._to_openai_message({"content": []})
        assert msg["content"] is None
        assert "tool_calls" not in msg


# ===========================================================================
# stream / _parse_anthropic_sse
# ===========================================================================

class TestStream:
    def setup_method(self):
        self.adapter = _make_adapter()

    @patch("acai.provider.anthropic.requests.post")
    def test_text_content_stream(self, mock_post):
        events = [
            {"type": "content_block_start", "content_block": {"type": "text"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}},
            {"type": "content_block_stop"},
            {"type": "message_stop"},
        ]
        mock_post.return_value = _mock_stream_response(200, events)
        results = list(self.adapter.stream([{"role": "user", "content": "hi"}]))
        tokens = [e for e in results if isinstance(e, ContentToken)]
        assert len(tokens) == 2
        assert tokens[0].text == "Hello"
        assert tokens[1].text == " world"
        assert isinstance(results[-1], StreamDone)

    @patch("acai.provider.anthropic.requests.post")
    def test_thinking_stream(self, mock_post):
        events = [
            {"type": "content_block_start", "content_block": {"type": "thinking"}},
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hmm"}},
            {"type": "content_block_stop"},
            {"type": "message_stop"},
        ]
        mock_post.return_value = _mock_stream_response(200, events)
        results = list(self.adapter.stream([{"role": "user", "content": "think"}]))
        reasoning = [e for e in results if isinstance(e, ReasoningToken)]
        assert len(reasoning) == 1
        assert reasoning[0].text == "hmm"

    @patch("acai.provider.anthropic.requests.post")
    def test_tool_use_stream(self, mock_post):
        events = [
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "tu_1", "name": "search"}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"q":'}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '"hello"}'}},
            {"type": "content_block_stop"},
            {"type": "message_stop"},
        ]
        mock_post.return_value = _mock_stream_response(200, events)
        results = list(self.adapter.stream([{"role": "user", "content": "search"}]))
        tool_deltas = [e for e in results if isinstance(e, ToolCallDelta)]
        assert len(tool_deltas) == 3
        assert tool_deltas[0].id == "tu_1"
        assert tool_deltas[0].name == "search"
        assert tool_deltas[0].arguments == ""
        assert tool_deltas[1].id is None
        assert tool_deltas[1].arguments == '{"q":'
        assert tool_deltas[2].arguments == '"hello"}'

    @patch("acai.provider.anthropic.requests.post")
    def test_multiple_tool_calls_increment_index(self, mock_post):
        events = [
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1", "name": "fn1"}},
            {"type": "content_block_stop"},
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t2", "name": "fn2"}},
            {"type": "content_block_stop"},
            {"type": "message_stop"},
        ]
        mock_post.return_value = _mock_stream_response(200, events)
        results = list(self.adapter.stream([{"role": "user", "content": "hi"}]))
        tool_starts = [e for e in results if isinstance(e, ToolCallDelta)]
        assert tool_starts[0].index == 0
        assert tool_starts[1].index == 1

    @patch("acai.provider.anthropic.requests.post")
    def test_stream_error_event(self, mock_post):
        events = [
            {"type": "error", "error": {"type": "overloaded_error", "message": "Server overloaded"}},
        ]
        mock_post.return_value = _mock_stream_response(200, events)
        with pytest.raises(LLMRequestError, match="Server overloaded"):
            list(self.adapter.stream([{"role": "user", "content": "hi"}]))

    @patch("acai.provider.anthropic.requests.post")
    def test_stream_error_event_string_detail(self, mock_post):
        events = [
            {"type": "error", "error": "something broke"},
        ]
        mock_post.return_value = _mock_stream_response(200, events)
        with pytest.raises(LLMRequestError, match="something broke"):
            list(self.adapter.stream([{"role": "user", "content": "hi"}]))

    @patch("acai.provider.anthropic.requests.post")
    def test_stream_done_marker(self, mock_post):
        events = ["[DONE]"]
        mock_post.return_value = _mock_stream_response(200, events)
        results = list(self.adapter.stream([{"role": "user", "content": "hi"}]))
        assert isinstance(results[-1], StreamDone)

    @patch("acai.provider.anthropic.requests.post")
    def test_stream_skips_empty_and_non_data_lines(self, mock_post):
        resp = MagicMock(spec=requests.Response)
        resp.ok = True
        resp.status_code = 200
        resp.iter_lines = MagicMock(return_value=iter([
            "",
            "event: ping",
            "data: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \"hi\"}}",
            "data: {\"type\": \"message_stop\"}",
        ]))
        mock_post.return_value = resp
        results = list(self.adapter.stream([{"role": "user", "content": "hi"}]))
        tokens = [e for e in results if isinstance(e, ContentToken)]
        assert len(tokens) == 1

    @patch("acai.provider.anthropic.requests.post")
    def test_stream_skips_invalid_json(self, mock_post):
        resp = MagicMock(spec=requests.Response)
        resp.ok = True
        resp.status_code = 200
        resp.iter_lines = MagicMock(return_value=iter([
            "data: {invalid json",
            "data: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \"ok\"}}",
            "data: {\"type\": \"message_stop\"}",
        ]))
        mock_post.return_value = resp
        results = list(self.adapter.stream([{"role": "user", "content": "hi"}]))
        tokens = [e for e in results if isinstance(e, ContentToken)]
        assert len(tokens) == 1
        assert tokens[0].text == "ok"

    @patch("acai.provider.anthropic.requests.post")
    def test_stream_empty_text_delta_not_yielded(self, mock_post):
        events = [
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ""}},
            {"type": "message_stop"},
        ]
        mock_post.return_value = _mock_stream_response(200, events)
        results = list(self.adapter.stream([{"role": "user", "content": "hi"}]))
        tokens = [e for e in results if isinstance(e, ContentToken)]
        assert len(tokens) == 0

    @patch("acai.provider.anthropic.requests.post")
    def test_stream_empty_thinking_delta_not_yielded(self, mock_post):
        events = [
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": ""}},
            {"type": "message_stop"},
        ]
        mock_post.return_value = _mock_stream_response(200, events)
        results = list(self.adapter.stream([{"role": "user", "content": "hi"}]))
        reasoning = [e for e in results if isinstance(e, ReasoningToken)]
        assert len(reasoning) == 0

    @patch("acai.provider.anthropic.requests.post")
    def test_stream_empty_partial_json_not_yielded(self, mock_post):
        events = [
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t", "name": "fn"}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": ""}},
            {"type": "content_block_stop"},
            {"type": "message_stop"},
        ]
        mock_post.return_value = _mock_stream_response(200, events)
        results = list(self.adapter.stream([{"role": "user", "content": "hi"}]))
        tool_deltas = [e for e in results if isinstance(e, ToolCallDelta)]
        # Only the initial start delta, not the empty partial
        assert len(tool_deltas) == 1

    @patch("acai.provider.anthropic.requests.post")
    def test_stream_http_error(self, mock_post):
        resp = _mock_json_response(500, {"error": {"message": "Internal error"}})
        mock_post.return_value = resp
        with pytest.raises(LLMRequestError, match="500"):
            list(self.adapter.stream([{"role": "user", "content": "hi"}]))

    @patch("acai.provider.anthropic.requests.post")
    def test_content_block_stop_without_tool(self, mock_post):
        """content_block_stop for a text block doesn't bump tool_index."""
        events = [
            {"type": "content_block_start", "content_block": {"type": "text"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
            {"type": "content_block_stop"},
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1", "name": "fn1"}},
            {"type": "content_block_stop"},
            {"type": "message_stop"},
        ]
        mock_post.return_value = _mock_stream_response(200, events)
        results = list(self.adapter.stream([{"role": "user", "content": "hi"}]))
        tool_deltas = [e for e in results if isinstance(e, ToolCallDelta)]
        assert tool_deltas[0].index == 0


# ===========================================================================
# fetch_models
# ===========================================================================

class TestFetchModels:
    @patch("acai.provider.anthropic.requests.get")
    def test_success(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={
                "data": [
                    {"id": "claude-3-opus-20240229", "display_name": "Claude 3 Opus"},
                    {"id": "claude-3-sonnet-20240229"},
                ],
            }),
        )
        mock_get.return_value.raise_for_status = MagicMock()

        prov = FakeProviderConfig(endpoint="https://api.anthropic.com/v1/messages/")
        result = fetch_models(prov)
        assert len(result) == 2
        assert result[0]["name"] == "Claude 3 Opus"
        assert result[0]["slug"] == "claude-3-opus-20240229"
        assert result[0]["max_tokens"] == 0
        assert result[0]["cost_weight"] == 10
        # Second model uses id as display_name fallback
        assert result[1]["name"] == "claude-3-sonnet-20240229"

    @patch("acai.provider.anthropic.requests.get")
    def test_http_error(self, mock_get):
        mock_get.return_value = MagicMock()
        mock_get.return_value.raise_for_status = MagicMock(
            side_effect=requests.HTTPError("403 Forbidden")
        )
        prov = FakeProviderConfig(endpoint="https://api.anthropic.com")
        with pytest.raises(requests.HTTPError):
            fetch_models(prov)

    @patch("acai.provider.anthropic.requests.get")
    def test_empty_data(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"data": []}),
        )
        mock_get.return_value.raise_for_status = MagicMock()
        prov = FakeProviderConfig(endpoint="https://api.anthropic.com")
        result = fetch_models(prov)
        assert result == []

    @patch("acai.provider.anthropic.requests.get")
    def test_headers_and_url(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"data": []}),
        )
        mock_get.return_value.raise_for_status = MagicMock()
        prov = FakeProviderConfig(endpoint="https://proxy.example.com/", api_key="my-key")
        fetch_models(prov)
        call_args = mock_get.call_args
        assert call_args[0][0] == "https://proxy.example.com/v1/models"
        assert call_args[1]["headers"]["x-api-key"] == "my-key"
        assert call_args[1]["headers"]["anthropic-version"] == "2023-06-01"
