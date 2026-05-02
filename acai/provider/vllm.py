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

    def _payload(self, messages: list[dict], stream: bool = False, **kwargs) -> dict:
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
        if kwargs.get("response_format"):
            payload["response_format"] = kwargs["response_format"]
        if kwargs.get("tools"):
            payload["tools"] = kwargs["tools"]
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
        yield from _parse_openai_sse(resp)


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
