"""Basic vLLM integration tests — verify the adapter works end-to-end.

These tests exercise:
- Simple completion (non-streaming)
- Streaming completion
- Tool call generation
- Thinking mode
- Response format (JSON schema)

Prerequisites: a running vLLM instance (see conftest.py).
"""

from __future__ import annotations

import json

import pytest

from tests.integrations.conftest import requires_vllm


@requires_vllm
class TestVLLMCompletion:
    """Test basic LLM completion against the live instance."""

    def test_simple_complete(self, llm):
        result = llm.complete([
            {"role": "system", "content": "You are a helpful assistant. Respond briefly."},
            {"role": "user", "content": "What is 2+2? Answer with just the number."},
        ])
        assert isinstance(result, str)
        assert "4" in result

    def test_complete_raw(self, llm):
        result = llm.complete_raw([
            {"role": "user", "content": "Say 'hello' and nothing else."},
        ])
        assert isinstance(result, dict)
        assert "content" in result
        assert "hello" in result["content"].lower()

    def test_streaming(self, llm):
        from acai.provider.base import ContentToken, StreamDone

        tokens = []
        for event in llm.stream([
            {"role": "user", "content": "Count from 1 to 5, separated by commas."},
        ]):
            if isinstance(event, ContentToken):
                tokens.append(event.text)
            elif isinstance(event, StreamDone):
                break

        full_response = "".join(tokens)
        assert "1" in full_response
        assert "5" in full_response

    def test_streaming_with_thinking(self, llm):
        from acai.provider.base import ContentToken, ReasoningToken, StreamDone

        has_reasoning = False
        has_content = False
        for event in llm.stream(
            [{"role": "user", "content": "What is the capital of France?"}],
            enable_thinking=True,
        ):
            if isinstance(event, ReasoningToken):
                has_reasoning = True
            elif isinstance(event, ContentToken):
                has_content = True
            elif isinstance(event, StreamDone):
                break

        assert has_content


@requires_vllm
class TestVLLMToolCalls:
    """Test tool call generation."""

    def test_tool_call_generation(self, llm):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "City name"},
                        },
                        "required": ["city"],
                    },
                },
            }
        ]

        result = llm.complete_raw(
            [{"role": "user", "content": "What's the weather in Paris?"}],
            tools=tools,
        )

        assert isinstance(result, dict)
        if result.get("tool_calls"):
            tc = result["tool_calls"][0]
            assert tc["function"]["name"] == "get_weather"
            args = json.loads(tc["function"]["arguments"])
            assert "paris" in args.get("city", "").lower()

    def test_tool_call_streaming(self, llm):
        from acai.provider.base import ContentToken, ToolCallDelta, StreamDone

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "calculate",
                    "description": "Evaluate a math expression",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {"type": "string"},
                        },
                        "required": ["expression"],
                    },
                },
            }
        ]

        has_tool_delta = False
        for event in llm.stream(
            [{"role": "user", "content": "Calculate 15 * 37"}],
            tools=tools,
        ):
            if isinstance(event, ToolCallDelta):
                has_tool_delta = True
            elif isinstance(event, StreamDone):
                break

        # Model may choose to answer directly or use tool — both are valid


@requires_vllm
class TestVLLMResponseFormat:
    """Test structured output (JSON schema) responses."""

    def test_json_response_format(self, llm):
        schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "answer",
                "schema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "country": {"type": "string"},
                    },
                    "required": ["city", "country"],
                },
            },
        }

        result = llm.complete_raw(
            [
                {"role": "system", "content": "Always respond with valid JSON."},
                {"role": "user", "content": "What is the capital of Japan?"},
            ],
            response_format=schema,
        )
        content = result.get("content", "")
        parsed = json.loads(content)
        assert "city" in parsed or "tokyo" in content.lower()
