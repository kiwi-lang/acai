"""Unit tests for acai.utils.tokens — accurate token counting."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from acai.utils.tokens import (
    count_text_tokens,
    count_message_tokens,
    count_messages_tokens,
    count_tools_tokens,
    estimate_payload_tokens,
    fits_context,
    reset_tokenizer,
    _FALLBACK_CHARS_PER_TOKEN,
    _MESSAGE_OVERHEAD_TOKENS,
    _TOOL_CALL_OVERHEAD_TOKENS,
)


@pytest.fixture(autouse=True)
def _reset():
    """Reset the tokenizer cache between tests."""
    reset_tokenizer()
    yield
    reset_tokenizer()


class TestCountTextTokens:
    def test_empty_string(self):
        assert count_text_tokens("") == 0

    def test_simple_text(self):
        result = count_text_tokens("Hello, world!")
        assert result > 0
        assert result < 20

    def test_longer_text_more_tokens(self):
        short = count_text_tokens("hi")
        long = count_text_tokens("This is a much longer sentence with many words")
        assert long > short

    def test_code_text(self):
        code = "def foo():\n    return 1\n"
        result = count_text_tokens(code)
        assert result > 0

    def test_fallback_when_no_tokenizer(self):
        """When tokenizer isn't available, falls back to char ratio."""
        reset_tokenizer()
        with patch("acai.utils.tokens._get_tokenizer", return_value=None):
            result = count_text_tokens("abcdefghij")  # 10 chars
            expected = int(10 / _FALLBACK_CHARS_PER_TOKEN)
            assert result == expected

    def test_unicode_text(self):
        text = "日本語テスト" * 10
        result = count_text_tokens(text)
        assert result > 0


class TestCountMessageTokens:
    def test_string_content(self):
        msg = {"role": "user", "content": "Hello"}
        result = count_message_tokens(msg)
        assert result >= _MESSAGE_OVERHEAD_TOKENS

    def test_list_content(self):
        msg = {"content": [{"text": "part one"}, {"text": "part two"}]}
        result = count_message_tokens(msg)
        assert result > _MESSAGE_OVERHEAD_TOKENS

    def test_tool_calls(self):
        msg = {
            "content": "",
            "tool_calls": [
                {"function": {"name": "read_file", "arguments": '{"path": "/foo"}'}}
            ],
        }
        result = count_message_tokens(msg)
        assert result >= _MESSAGE_OVERHEAD_TOKENS + _TOOL_CALL_OVERHEAD_TOKENS

    def test_none_content(self):
        msg = {"content": None}
        result = count_message_tokens(msg)
        assert result == _MESSAGE_OVERHEAD_TOKENS

    def test_no_content_key(self):
        msg = {"role": "user"}
        result = count_message_tokens(msg)
        assert result == _MESSAGE_OVERHEAD_TOKENS


class TestCountMessagesTokens:
    def test_empty(self):
        assert count_messages_tokens([]) == 0

    def test_multiple_messages(self):
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi! How can I help?"},
        ]
        result = count_messages_tokens(msgs)
        assert result > 3 * _MESSAGE_OVERHEAD_TOKENS

    def test_growing_conversation(self):
        """More messages = more tokens."""
        short = [{"role": "user", "content": "hi"}]
        long = short + [{"role": "assistant", "content": "hello! " * 100}]
        assert count_messages_tokens(long) > count_messages_tokens(short)


class TestCountToolsTokens:
    def test_none(self):
        assert count_tools_tokens(None) == 0

    def test_empty_list(self):
        assert count_tools_tokens([]) == 0

    def test_tool_definitions(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from disk",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }]
        result = count_tools_tokens(tools)
        assert result > 0


class TestEstimatePayloadTokens:
    def test_messages_only(self):
        payload = {"messages": [{"role": "user", "content": "hello"}]}
        result = estimate_payload_tokens(payload)
        assert result > 0

    def test_messages_with_tools(self):
        payload = {
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "function": {"name": "test"}}],
        }
        without_tools = estimate_payload_tokens(
            {"messages": payload["messages"]}
        )
        with_tools = estimate_payload_tokens(payload)
        assert with_tools > without_tools

    def test_empty_payload(self):
        assert estimate_payload_tokens({}) == 0


class TestFitsContext:
    def test_small_payload_fits(self):
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        fits, est, avail = fits_context(payload, 128000, 4096)
        assert fits is True
        assert est > 0
        assert avail > 0

    def test_huge_payload_doesnt_fit(self):
        huge_content = "x" * 500000  # ~150K+ tokens
        payload = {"messages": [{"role": "user", "content": huge_content}]}
        fits, est, avail = fits_context(payload, 128000, 4096)
        assert fits is False
        assert est > avail

    def test_safety_margin(self):
        """Higher safety margin means less available space."""
        payload = {"messages": [{"role": "user", "content": "test " * 1000}]}
        _, _, avail_low = fits_context(payload, 128000, 4096, safety_margin=0.01)
        _, _, avail_high = fits_context(payload, 128000, 4096, safety_margin=0.20)
        assert avail_low > avail_high

    def test_max_output_tokens_reduces_budget(self):
        payload = {"messages": [{"role": "user", "content": "test"}]}
        _, _, avail_small = fits_context(payload, 128000, 100)
        _, _, avail_large = fits_context(payload, 128000, 50000)
        assert avail_small > avail_large

    def test_boundary_case(self):
        """Payload exactly at the boundary."""
        payload = {"messages": [{"role": "user", "content": "a" * 4000}]}
        est_tokens = estimate_payload_tokens(payload)
        # Set context window just above what's needed
        ctx_window = int((est_tokens + 100) / 0.95) + 50
        fits, est, avail = fits_context(payload, ctx_window, 50)
        assert fits is True


class TestTokenizerFallback:
    def test_fallback_used_when_import_fails(self):
        """If transformers can't be imported, char fallback is used."""
        reset_tokenizer()
        with patch("acai.utils.tokens._get_tokenizer", return_value=None):
            text = "Hello world, this is a test"
            result = count_text_tokens(text)
            expected = int(len(text) / _FALLBACK_CHARS_PER_TOKEN)
            assert result == expected

    def test_fallback_ratio_is_conservative(self):
        """The fallback ratio (3.2) overestimates compared to English (4.0)."""
        assert _FALLBACK_CHARS_PER_TOKEN < 4.0
        assert _FALLBACK_CHARS_PER_TOKEN > 2.5
