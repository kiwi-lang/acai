"""vLLM / OpenAI-compatible local server adapter + model fetching."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests

from acai.provider.base import (
    LLM,
    ContentToken,
    _error_or_raise,
    _parse_openai_sse,
    _strip_endpoint,
)

if TYPE_CHECKING:
    from acai.provider.config import ProviderConfig

log = logging.getLogger(__name__)


class VLLMAdapter(LLM):
    """Adapter for vLLM and other local OpenAI-compatible servers.

    Supports vLLM extensions: ``chat_template_kwargs`` for thinking
    control, Qwen3 thinking prefix hack.
    """

    def __init__(self, config: ProviderConfig):
        self.endpoint = _strip_endpoint(config.endpoint)
        self.model = config.model_slug
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature
        self.api_key = config.api_key
        self.timeout = 300

    def _url(self) -> str:
        return f"{self.endpoint}/v1/chat/completions"

    def _headers(self) -> dict:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _prepare_messages(self, messages: list[dict], **kwargs) -> list[dict]:
        """Apply Qwen3 thinking prefix hack if needed."""
        enable_thinking = kwargs.get("enable_thinking")
        if enable_thinking is None or not messages:
            return messages
        msgs = [dict(m) for m in messages]
        if enable_thinking:
            prefix = "<think>\n"
            suffix = "\nI have to give the solution based on the reasoning directly now."
        else:
            prefix = "</think>\n"
            suffix = ""
        for msg in reversed(msgs):
            if msg.get("role") == "user":
                msg["content"] = prefix + (msg.get("content") or "") + suffix
                break
        return msgs

    @staticmethod
    def _inject_response_format(messages: list[dict], response_format: dict) -> list[dict]:
        """Inject response_format schema into the system prompt as text.

        vLLM doesn't support response_format + tools simultaneously,
        so we fall back to describing the schema in the system message.
        """
        schema = response_format
        if schema.get("type") == "json_schema":
            schema = schema["json_schema"].get("schema", schema)

        import json as _json
        schema_text = _json.dumps(schema, indent=2)
        suffix = (
            "\n\n## Required output format\n"
            "You MUST respond with a JSON object conforming to this schema "
            "(no markdown fences, no commentary):\n"
            f"\n{schema_text}\n"
        )

        msgs = [dict(m) for m in messages]
        for msg in msgs:
            if msg.get("role") == "system":
                msg["content"] = (msg.get("content") or "") + suffix
                break
        else:
            msgs.insert(0, {"role": "system", "content": suffix.lstrip()})
        return msgs

    def _payload(self, messages: list[dict], stream: bool = False, **kwargs) -> dict:
        resp_fmt = kwargs.get("response_format")
        has_tools = bool(kwargs.get("tools"))

        if has_tools and resp_fmt:
            messages = self._inject_response_format(messages, resp_fmt)
            resp_fmt = None

        payload: dict = {
            "model": kwargs.get("model", self.model),
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": stream,
        }
        enable_thinking = kwargs.get("enable_thinking")
        if enable_thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
        if has_tools:
            payload["tools"] = kwargs["tools"]
        if resp_fmt:
            payload["response_format"] = resp_fmt
        return payload

    def complete(self, messages, **kwargs):
        msgs = self._prepare_messages(messages, **kwargs)
        payload = self._payload(msgs, stream=False, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def complete_raw(self, messages, tools=None, **kwargs):
        msgs = self._prepare_messages(messages, **kwargs)
        if tools:
            kwargs["tools"] = tools
        payload = self._payload(msgs, stream=False, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

    def stream(self, messages, **kwargs):
        msgs = self._prepare_messages(messages, **kwargs)
        payload = self._payload(msgs, stream=True, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), stream=True,
                             timeout=self.timeout)
        _error_or_raise(resp)
        split_reasoning = kwargs.get("enable_thinking") is True
        yield from _parse_openai_sse(resp, split_reasoning=split_reasoning)


OpenAICompatibleLLM = VLLMAdapter


def fetch_models(prov: ProviderConfig) -> list[dict]:
    """Fetch available models from an OpenAI-compatible /v1/models endpoint."""
    ep = prov.endpoint.rstrip("/")
    url = f"{ep}/v1/models"
    headers = {"Authorization": f"Bearer {prov.api_key}"} if prov.api_key else {}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    models = []
    for m in data:
        mid = m.get("id", "")
        models.append({
            "name": mid,
            "slug": mid,
            "max_tokens": 0,
            "context_window": 0,
            "cost_weight": 10,
            "smart_weight": 10,
        })
    return models
