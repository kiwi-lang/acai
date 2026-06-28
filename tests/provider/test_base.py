"""Tests for acai.provider.base — shared LLM helpers and SSE parsing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from acai.provider.base import (
    _strip_endpoint,
    _error_or_raise,
    _parse_openai_sse,
    LLMRequestError,
    ContentToken,
    ReasoningToken,
    ToolCallDelta,
    StreamDone,
)


class TestStripEndpoint:

    def test_bare_url_unchanged(self):
        assert _strip_endpoint("http://localhost:8000") == "http://localhost:8000"

    def test_strips_v1_suffix(self):
        assert _strip_endpoint("http://localhost:8000/v1") == "http://localhost:8000"

    def test_strips_full_completions_path(self):
        assert _strip_endpoint("http://host:8000/v1/chat/completions") == "http://host:8000"

    def test_strips_trailing_slash(self):
        assert _strip_endpoint("http://localhost:8000/") == "http://localhost:8000"

    def test_strips_v1_with_trailing_slash(self):
        assert _strip_endpoint("http://host/v1/") == "http://host"


class TestErrorOrRaise:

    def test_ok_response_does_nothing(self):
        resp = MagicMock(ok=True)
        _error_or_raise(resp)

    def test_error_with_json_body(self):
        resp = MagicMock(ok=False, status_code=400)
        resp.json.return_value = {"error": {"message": "bad request"}}
        with pytest.raises(LLMRequestError, match="bad request"):
            _error_or_raise(resp)

    def test_error_with_string_detail(self):
        resp = MagicMock(ok=False, status_code=500)
        resp.json.return_value = {"error": "server exploded"}
        with pytest.raises(LLMRequestError, match="server exploded"):
            _error_or_raise(resp)

    def test_error_with_non_json_body(self):
        resp = MagicMock(ok=False, status_code=502)
        resp.json.side_effect = ValueError("no json")
        resp.text = "Bad Gateway"
        with pytest.raises(LLMRequestError, match="Bad Gateway"):
            _error_or_raise(resp)


class TestParseOpenaiSSE:

    def _make_response(self, lines):
        """Create a mock response that iterates over SSE lines."""
        resp = MagicMock()
        resp.iter_lines.return_value = iter(lines)
        return resp

    def test_content_tokens(self):
        lines = [
            'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            'data: {"choices": [{"delta": {"content": " world"}}]}',
            "data: [DONE]",
        ]
        resp = self._make_response(lines)
        events = list(_parse_openai_sse(resp))
        assert events[0] == ContentToken(text="Hello")
        assert events[1] == ContentToken(text=" world")
        assert isinstance(events[2], StreamDone)

    def test_reasoning_tokens_split(self):
        lines = [
            'data: {"choices": [{"delta": {"reasoning_content": "Let me think"}}]}',
            'data: {"choices": [{"delta": {"content": "Answer"}}]}',
            "data: [DONE]",
        ]
        resp = self._make_response(lines)
        events = list(_parse_openai_sse(resp, split_reasoning=True))
        assert events[0] == ReasoningToken(text="Let me think")
        assert events[1] == ContentToken(text="Answer")

    def test_reasoning_not_split_becomes_content(self):
        lines = [
            'data: {"choices": [{"delta": {"reasoning": "thinking"}}]}',
            "data: [DONE]",
        ]
        resp = self._make_response(lines)
        events = list(_parse_openai_sse(resp, split_reasoning=False))
        assert events[0] == ContentToken(text="thinking")

    def test_tool_call_deltas(self):
        lines = [
            'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "read_file", "arguments": ""}}]}}]}',
            'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\\\"path\\\": \\\"f.txt\\\"}"}}]}}]}',
            "data: [DONE]",
        ]
        resp = self._make_response(lines)
        events = list(_parse_openai_sse(resp))
        assert isinstance(events[0], ToolCallDelta)
        assert events[0].name == "read_file"
        assert events[0].id == "call_1"
        assert isinstance(events[1], ToolCallDelta)
        assert '{"path": "f.txt"}' in events[1].arguments

    def test_empty_lines_and_non_data_ignored(self):
        lines = [
            "",
            ": comment",
            'data: {"choices": [{"delta": {"content": "ok"}}]}',
            "data: [DONE]",
        ]
        resp = self._make_response(lines)
        events = list(_parse_openai_sse(resp))
        assert events[0] == ContentToken(text="ok")
        assert isinstance(events[1], StreamDone)

    def test_error_in_stream_raises(self):
        lines = [
            'data: {"error": {"message": "context length exceeded"}}',
        ]
        resp = self._make_response(lines)
        with pytest.raises(LLMRequestError, match="context length exceeded"):
            list(_parse_openai_sse(resp))

    def test_invalid_json_skipped(self):
        lines = [
            "data: not-json-at-all",
            'data: {"choices": [{"delta": {"content": "ok"}}]}',
            "data: [DONE]",
        ]
        resp = self._make_response(lines)
        events = list(_parse_openai_sse(resp))
        assert events[0] == ContentToken(text="ok")
