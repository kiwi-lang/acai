"""Adapter and model-fetch registries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from acai.provider.base import LLM
from acai.provider.vllm import VLLMAdapter
from acai.provider.openai import OpenAIAdapter
from acai.provider.anthropic import AnthropicAdapter

from acai.provider import vllm as _vllm_mod
from acai.provider import openai as _openai_mod
from acai.provider import anthropic as _anthropic_mod
from acai.provider import google as _google_mod

if TYPE_CHECKING:
    from acai.provider.config import ProviderConfig

_ADAPTER_MAP: dict[str, type[LLM]] = {
    "vllm": VLLMAdapter,
    "llamacpp": VLLMAdapter,
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
}

_FETCH_MAP: dict[str, Callable[[ProviderConfig], list[dict]]] = {
    "vllm": _vllm_mod.fetch_models,
    "llamacpp": _vllm_mod.fetch_models,
    "openai": _openai_mod.fetch_models,
    "anthropic": _anthropic_mod.fetch_models,
    "google": _google_mod.fetch_models,
}


def create_llm(config: ProviderConfig) -> LLM:
    """Build the right LLM adapter for the given provider config."""
    cls = _ADAPTER_MAP.get(config.backend, VLLMAdapter)
    return cls(config)
