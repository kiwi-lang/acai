"""Tests for acai.provider.vllm — VLLMAdapter message preparation and payload building."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from acai.provider.vllm import VLLMAdapter


@pytest.fixture
def adapter():
    """Create a VLLMAdapter with a mock config."""
    config = MagicMock()
    config.endpoint = "http://localhost:8000/v1"
    config.model_slug = "Qwen/Qwen3-32B"
    config.max_tokens = 4096
    config.temperature = 0.7
    config.api_key = "test-key"
    return VLLMAdapter(config)


class TestVLLMAdapterInit:

    def test_strips_endpoint(self, adapter):
        assert adapter.endpoint == "http://localhost:8000"

    def test_url(self, adapter):
        assert adapter._url() == "http://localhost:8000/v1/chat/completions"

    def test_headers_include_auth(self, adapter):
        h = adapter._headers()
        assert h["Authorization"] == "Bearer test-key"
        assert h["Content-Type"] == "application/json"

    def test_headers_no_auth_when_empty(self):
        config = MagicMock()
        config.endpoint = "http://localhost:8000"
        config.model_slug = "model"
        config.max_tokens = 1024
        config.temperature = 0.5
        config.api_key = ""
        a = VLLMAdapter(config)
        h = a._headers()
        assert "Authorization" not in h


class TestPrepareMessages:

    def test_no_thinking_returns_unchanged(self, adapter):
        msgs = [{"role": "user", "content": "hello"}]
        result = adapter._prepare_messages(msgs)
        assert result == msgs

    def test_enable_thinking_prepends_think_tag(self, adapter):
        msgs = [{"role": "user", "content": "solve x=2"}]
        result = adapter._prepare_messages(msgs, enable_thinking=True)
        assert result[0]["content"].startswith("<think>\n")
        assert "solve x=2" in result[0]["content"]

    def test_disable_thinking_prepends_close_tag(self, adapter):
        msgs = [{"role": "user", "content": "just answer"}]
        result = adapter._prepare_messages(msgs, enable_thinking=False)
        assert result[0]["content"].startswith("</think>\n")

    def test_thinking_targets_last_user_message(self, adapter):
        msgs = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "second"},
        ]
        result = adapter._prepare_messages(msgs, enable_thinking=True)
        assert result[0]["content"] == "You are helpful"
        assert result[1]["content"] == "first"
        assert result[3]["content"].startswith("<think>\n")
        assert "second" in result[3]["content"]

    def test_empty_messages_returned_unchanged(self, adapter):
        result = adapter._prepare_messages([], enable_thinking=True)
        assert result == []


class TestInjectResponseFormat:

    def test_injects_into_system_message(self, adapter):
        msgs = [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Do stuff"},
        ]
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        result = VLLMAdapter._inject_response_format(msgs, schema)
        assert "Required output format" in result[0]["content"]
        assert "answer" in result[0]["content"]

    def test_creates_system_message_when_absent(self, adapter):
        msgs = [{"role": "user", "content": "Do stuff"}]
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        result = VLLMAdapter._inject_response_format(msgs, schema)
        assert result[0]["role"] == "system"
        assert "Required output format" in result[0]["content"]

    def test_unwraps_json_schema_envelope(self, adapter):
        msgs = [{"role": "system", "content": "sys"}]
        schema = {
            "type": "json_schema",
            "json_schema": {"schema": {"type": "object", "properties": {"v": {"type": "string"}}}},
        }
        result = VLLMAdapter._inject_response_format(msgs, schema)
        assert '"v"' in result[0]["content"]


class TestPayload:

    def test_basic_payload(self, adapter):
        msgs = [{"role": "user", "content": "hi"}]
        p = adapter._payload(msgs, stream=False)
        assert p["model"] == "Qwen/Qwen3-32B"
        assert p["messages"] == msgs
        assert p["temperature"] == 0.7
        assert p["max_tokens"] == 4096
        assert p["stream"] is False

    def test_streaming_payload(self, adapter):
        msgs = [{"role": "user", "content": "hi"}]
        p = adapter._payload(msgs, stream=True)
        assert p["stream"] is True

    def test_thinking_adds_chat_template_kwargs(self, adapter):
        msgs = [{"role": "user", "content": "hi"}]
        p = adapter._payload(msgs, stream=False, enable_thinking=True)
        assert p["chat_template_kwargs"] == {"enable_thinking": True}

    def test_tools_included(self, adapter):
        msgs = [{"role": "user", "content": "hi"}]
        tools = [{"type": "function", "function": {"name": "test"}}]
        p = adapter._payload(msgs, stream=False, tools=tools)
        assert p["tools"] == tools

    def test_tools_with_response_format_injects_schema(self, adapter):
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        tools = [{"type": "function", "function": {"name": "t"}}]
        resp_fmt = {"type": "object", "properties": {"a": {"type": "string"}}}
        p = adapter._payload(msgs, stream=False, tools=tools, response_format=resp_fmt)
        assert "tools" in p
        assert "response_format" not in p
        assert "Required output format" in p["messages"][0]["content"]

    def test_model_override(self, adapter):
        msgs = [{"role": "user", "content": "hi"}]
        p = adapter._payload(msgs, stream=False, model="other-model")
        assert p["model"] == "other-model"
