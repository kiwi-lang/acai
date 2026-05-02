"""Anthropic Messages API adapter + model fetching."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Generator

import requests

from acai.provider.base import (
    LLM,
    ContentToken,
    LLMRequestError,
    ReasoningToken,
    StreamDone,
    StreamEvent,
    ToolCallDelta,
    _error_or_raise,
)

if TYPE_CHECKING:
    from acai.provider.config import ProviderConfig

log = logging.getLogger(__name__)


class AnthropicAdapter(LLM):
    """Adapter for the Anthropic Messages API.

    Translates the standard OpenAI-style messages list into
    Anthropic's wire format and parses the SSE stream back into
    the common :class:`StreamEvent` types.
    """

    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, config: ProviderConfig):
        ep = config.endpoint.rstrip("/")
        if ep.endswith("/v1/messages"):
            ep = ep[: -len("/v1/messages")]
        elif ep.endswith("/v1"):
            ep = ep[: -len("/v1")]
        self.endpoint = ep
        self.model = config.model_slug
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature
        self.api_key = config.api_key
        self.timeout = (10, 300)

    def _url(self) -> str:
        return f"{self.endpoint}/v1/messages"

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
        }

    @staticmethod
    def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
        """Extract system messages into a single string; return the rest."""
        system_parts: list[str] = []
        rest: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
            else:
                rest.append(m)
        return "\n\n".join(system_parts), rest

    def _convert_messages(self, messages: list[dict]) -> list[dict]:
        """Convert OpenAI-style messages to Anthropic format.

        Merges consecutive same-role messages (Anthropic requires
        strict alternation) and maps tool_calls / tool results.
        """
        out: list[dict] = []
        for m in messages:
            role = m.get("role", "user")
            if role == "system":
                continue
            if role == "tool":
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    }],
                })
                continue
            if role == "assistant" and m.get("tool_calls"):
                content: list[dict] = []
                if m.get("content"):
                    content.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    content.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": args,
                    })
                out.append({"role": "assistant", "content": content})
                continue

            out.append({"role": role, "content": m.get("content", "")})

        merged: list[dict] = []
        for msg in out:
            if merged and merged[-1]["role"] == msg["role"]:
                prev = merged[-1]["content"]
                cur = msg["content"]
                if isinstance(prev, str) and isinstance(cur, str):
                    merged[-1]["content"] = prev + "\n\n" + cur
                elif isinstance(prev, list) and isinstance(cur, list):
                    merged[-1]["content"] = prev + cur
                elif isinstance(prev, str) and isinstance(cur, list):
                    merged[-1]["content"] = [{"type": "text", "text": prev}] + cur
                elif isinstance(prev, list) and isinstance(cur, str):
                    merged[-1]["content"] = prev + [{"type": "text", "text": cur}]
            else:
                merged.append(msg)
        return merged

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        """Convert OpenAI tool definitions to Anthropic format."""
        out: list[dict] = []
        for t in tools:
            fn = t.get("function", {})
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return out

    def _payload(self, messages: list[dict], stream: bool = False, **kwargs) -> dict:
        system_text, user_msgs = self._split_system(messages)
        converted = self._convert_messages(user_msgs)
        payload: dict = {
            "model": kwargs.get("model", self.model),
            "messages": converted,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "stream": stream,
        }
        if system_text:
            payload["system"] = system_text
        enable_thinking = kwargs.get("enable_thinking")
        if enable_thinking:
            payload["thinking"] = {"type": "enabled", "budget_tokens": self.max_tokens}
        tools = kwargs.get("tools")
        if tools:
            payload["tools"] = self._convert_tools(tools)
        return payload

    def complete(self, messages, **kwargs):
        payload = self._payload(messages, stream=False, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), timeout=self.timeout)
        _error_or_raise(resp)
        data = resp.json()
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "")
        return ""

    def complete_raw(self, messages, tools=None, **kwargs):
        if tools:
            kwargs["tools"] = tools
        payload = self._payload(messages, stream=False, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), timeout=self.timeout)
        _error_or_raise(resp)
        return self._to_openai_message(resp.json())

    @staticmethod
    def _to_openai_message(data: dict) -> dict:
        """Convert an Anthropic response to an OpenAI-style message dict."""
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in data.get("content", []):
            btype = block.get("type", "")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
        msg: dict = {"role": "assistant", "content": "\n".join(text_parts) or None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg

    def stream(self, messages, **kwargs):
        payload = self._payload(messages, stream=True, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), stream=True,
                             timeout=self.timeout)
        _error_or_raise(resp)
        yield from self._parse_anthropic_sse(resp)

    def _parse_anthropic_sse(self, resp: requests.Response) -> Generator[StreamEvent, None, None]:
        current_tool_id = ""
        current_tool_name = ""
        tool_index = 0

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use":
                    current_tool_id = block.get("id", "")
                    current_tool_name = block.get("name", "")
                    yield ToolCallDelta(
                        index=tool_index,
                        id=current_tool_id,
                        name=current_tool_name,
                        arguments="",
                    )

            elif etype == "content_block_delta":
                delta = event.get("delta", {})
                dtype = delta.get("type", "")
                if dtype == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        yield ContentToken(text=text)
                elif dtype == "thinking_delta":
                    thinking = delta.get("thinking", "")
                    if thinking:
                        yield ReasoningToken(text=thinking)
                elif dtype == "input_json_delta":
                    partial = delta.get("partial_json", "")
                    if partial:
                        yield ToolCallDelta(
                            index=tool_index,
                            id=None,
                            name=None,
                            arguments=partial,
                        )

            elif etype == "content_block_stop":
                if current_tool_name:
                    tool_index += 1
                    current_tool_id = ""
                    current_tool_name = ""

            elif etype == "error":
                err_detail = event.get("error", {})
                err_msg = err_detail.get("message", str(err_detail)) if isinstance(err_detail, dict) else str(err_detail)
                raise LLMRequestError(f"Anthropic stream error: {err_msg}")

            elif etype == "message_stop":
                break

        yield StreamDone()


def fetch_models(prov: ProviderConfig) -> list[dict]:
    """Fetch available models from the Anthropic /v1/models endpoint."""
    ep = prov.endpoint.rstrip("/")
    url = f"{ep}/v1/models"
    headers = {
        "x-api-key": prov.api_key,
        "anthropic-version": "2023-06-01",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    models = []
    for m in data:
        mid = m.get("id", "")
        display = m.get("display_name", mid)
        models.append({
            "name": display,
            "slug": mid,
            "max_tokens": 0,
            "context_window": 0,
            "cost_weight": 10,
            "smart_weight": 10,
        })
    return models
