"""Provider package — LLM adapters, config, scheduling, and routes.

Usage::

    from acai.provider import ProviderConfig, ModelConfig, create_llm
    from acai.provider import LLM, LLMServer, LLMServerError
    from acai.provider import ProviderScheduler
"""

from acai.provider.config import (  # noqa: F401
    ModelConfig,
    ModelSet,
    ModelSetEntry,
    COMPLEXITY_LEVELS,
    ProviderConfig,
    _model_to_slug,
    _provider_to_dict,
    _model_set_to_dict,
    _default_provider,
    _load_providers_from_global,
    _load_model_sets_from_global,
    load_providers,
    save_providers,
    load_model_sets,
    save_model_sets,
)

from acai.provider.base import (  # noqa: F401
    LLM,
    LLMRequestError,
    StreamEvent,
    ContentToken,
    ReasoningToken,
    ToolCallDelta,
    StreamDone,
)

from acai.provider.server import (  # noqa: F401
    LLMServer,
    LLMServerError,
)

from acai.provider.scheduler import ProviderScheduler  # noqa: F401
from acai.provider.router import ModelRouter  # noqa: F401

from acai.provider.registry import create_llm  # noqa: F401

from acai.provider.vllm import VLLMAdapter, OpenAICompatibleLLM  # noqa: F401
from acai.provider.openai import OpenAIAdapter  # noqa: F401
from acai.provider.anthropic import AnthropicAdapter  # noqa: F401

from acai.provider.routes import create_provider_router  # noqa: F401
