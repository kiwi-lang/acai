"""Provider and model configuration dataclasses + persistence."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field


# ---------------------------------------------------------------------------
# ModelConfig — per-model settings within a provider
# ---------------------------------------------------------------------------

@dataclass
class ModelConfig:
    """A single model offered by a provider.

    ``max_tokens`` / ``context_window`` of 0 mean "inherit from the
    provider-level defaults".  ``cost_weight`` and ``smart_weight``
    are user-assigned relative scores (1-100) for future routing.
    """

    name: str = ""
    slug: str = ""
    max_tokens: int = 0
    context_window: int = 0
    cost_weight: int = 10
    smart_weight: int = 10

    @classmethod
    def from_dict(cls, d: dict) -> ModelConfig:
        return cls(
            name=d.get("name", ""),
            slug=d.get("slug", ""),
            max_tokens=int(d.get("max_tokens", 0)),
            context_window=int(d.get("context_window", 0)),
            cost_weight=int(d.get("cost_weight", 10)),
            smart_weight=int(d.get("smart_weight", 10)),
        )


def _model_to_slug(model: str) -> str:
    """Derive a short slug from a model path like ``Org/Model-Name``."""
    return model.rsplit("/", 1)[-1].lower().replace("_", "-")


# ---------------------------------------------------------------------------
# Tool-call parser heuristics (used by the default vLLM launch template)
# ---------------------------------------------------------------------------

_MODEL_PARSER_MAP: list[tuple[str, str]] = [
    # Qwen family — coder variants use XML, everything else uses hermes
    ("qwen3-coder", "qwen3_xml"),
    ("qwen3_coder", "qwen3_xml"),
    ("qwen3", "hermes"),
    ("qwen2.5", "hermes"),
    ("qwen2", "hermes"),
    ("qwq", "hermes"),
    # Gemma
    ("gemma-4", "gemma4"),
    ("gemma4", "gemma4"),
    ("gemma-3", "hermes"),
    ("gemma-2", "hermes"),
    ("gemma3", "hermes"),
    ("gemma2", "hermes"),
    # Llama
    ("llama-4", "llama4_pythonic"),
    ("llama-3", "llama3_json"),
    # Mistral / Codestral
    ("mistral", "mistral"),
    ("codestral", "mistral"),
    # DeepSeek
    ("deepseek-v3.1", "deepseek_v31"),
    ("deepseek-v3", "deepseek_v3"),
    ("deepseek-r1", "deepseek_v3"),
    # IBM Granite
    ("granite-4", "granite4"),
    ("granite-3", "granite"),
    # GLM
    ("glm-4.7", "glm47"),
    ("glm-4.6", "glm45"),
    ("glm-4.5", "glm45"),
    # Others
    ("kimi-k2", "kimi_k2"),
    ("minimax", "minimax"),
    ("olmo-3", "olmo3"),
    ("hunyuan", "hunyuan_a13b"),
    ("hermes", "hermes"),
]

_MODEL_REASONING_PARSER_MAP: list[tuple[str, str]] = [
    ("gemma-4", "gemma4"),
    ("gemma4", "gemma4"),
    ("qwen3", "qwen3"),
    ("qwq", "deepseek_r1"),
    ("deepseek-r1", "deepseek_r1"),
    ("deepseek-v3", "deepseek_v3"),
    ("granite-3.2", "granite"),
    ("granite-4", "granite"),
    ("glm-4.7", "glm47"),
    ("glm-4.6", "glm45"),
    ("glm-4.5", "glm45"),
    ("glm-4", "glm45"),
    ("hunyuan", "hunyuan_a13b"),
    ("olmo-3", "olmo3"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("gpt-5.5", "openai"),
    ("claude", "anthropic"),
]


def _guess_tool_parser(model: str) -> str:
    lower = model.lower().replace("/", "-").replace("_", "-")
    for pattern, parser in _MODEL_PARSER_MAP:
        if pattern in lower:
            return parser
    return "hermes"


def _guess_reasoning_parser(model: str) -> str | None:
    """Return the reasoning parser name for *model*, or ``None`` if not a reasoning model."""
    lower = model.lower().replace("/", "-").replace("_", "-")
    for pattern, parser in _MODEL_REASONING_PARSER_MAP:
        if pattern in lower:
            return parser
    return None


def _default_vllm_template(model: str) -> str:
    """Build the default vLLM launch template for a given model."""
    lower = model.lower().replace("/", "-")
    parser = _guess_tool_parser(model)
    parts = [
        "vllm serve {model}",
        "--host {server_host}",
        "--served-model-name {slug}",
        "--port {server_port}",
        "--enable-auto-tool-choice",
        f"--tool-call-parser {parser}",
        "--enable-prefix-caching",
        "--kv-cache-dtype fp8",
        "--max-num-seqs 1",
    ]

    reasoning_parser = _guess_reasoning_parser(model)
    if reasoning_parser:
        parts.append(f"--reasoning-parser {reasoning_parser}")

    if "qwen3-coder" in lower:
        parts += [
            "--max-model-len 170000",
            "--gpu-memory-utilization 0.90",
            "--attention-backend flashinfer",
        ]
    elif "gemma-4" in lower or "gemma4" in lower:
        parts.append("--quantization fp8")

    return " ".join(parts)


_DEFAULT_TEMPLATES: dict[str, str] = {
    "llamacpp": "llama-server -m {model} --host {server_host} --port {server_port}",
    "local": "llama-server -m {model} --host {server_host} --port {server_port}",
}


# ---------------------------------------------------------------------------
# ProviderConfig
# ---------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    """An LLM provider -- local server or remote API.

    ``launch_template`` is a Python format string resolved via
    ``template.format(**asdict(self))``.  When empty, a backend-specific
    default is used (vLLM, llama.cpp).  When no default exists for the
    backend the provider is treated as *unmanaged* (remote endpoint).

    ``models`` is an ordered list of :class:`ModelConfig` entries.
    Position 0 is the default model.  Provider-level ``max_tokens``
    and ``context_window`` act as fallbacks when a model's value is 0.
    """

    name: str = ""
    backend: str = "openai"
    endpoint: str = ""
    api_key: str = ""
    server_port: int = 9123
    server_host: str = "0.0.0.0"
    launch_template: str = ""
    max_tokens: int = 4096
    temperature: float = 1.0
    context_window: int = 128000
    priority: int = 0
    models: list[ModelConfig] = field(default_factory=list)

    def __post_init__(self):
        if not self.endpoint and self.server_port:
            self.endpoint = f"http://127.0.0.1:{self.server_port}"

    # -- model helpers -----------------------------------------------------

    @property
    def default_model(self) -> ModelConfig | None:
        return self.models[0] if self.models else None

    @property
    def model_slug(self) -> str:
        m = self.default_model
        return m.slug if m else ""

    @property
    def model(self) -> str:
        """Backward-compat: return the default model's name (or slug)."""
        m = self.default_model
        if not m:
            return ""
        return m.name or m.slug

    @property
    def slug(self) -> str:
        """Backward-compat alias for :attr:`model_slug`."""
        return self.model_slug

    def get_model(self, slug: str) -> ModelConfig | None:
        for m in self.models:
            if m.slug == slug:
                return m
        return None

    def resolve_model(self, model: ModelConfig) -> ModelConfig:
        """Return a copy with zeros filled from provider defaults."""
        return ModelConfig(
            name=model.name,
            slug=model.slug,
            max_tokens=model.max_tokens or self.max_tokens,
            context_window=model.context_window or self.context_window,
            cost_weight=model.cost_weight,
            smart_weight=model.smart_weight,
        )

    # -- command building --------------------------------------------------

    def build_command(self) -> str:
        """Return the resolved server launch command, or ``""`` if unmanaged."""
        d = asdict(self)
        m = self.default_model
        if m:
            d["model"] = m.name or m.slug
            d["slug"] = m.slug
        else:
            d.setdefault("model", "")
            d.setdefault("slug", "")
        if self.launch_template:
            return self.launch_template.format(**d)
        if self.backend == "vllm" and d.get("model"):
            return _default_vllm_template(d["model"]).format(**d)
        tpl = _DEFAULT_TEMPLATES.get(self.backend, "")
        if tpl:
            return tpl.format(**d)
        return ""

    @property
    def managed(self) -> bool:
        """True when this provider requires a local server process."""
        return bool(self.build_command())

    @property
    def supports_thinking(self) -> bool:
        """True when the model behind this provider supports native thinking."""
        m = self.default_model
        identifier = (m.slug if m else "") or (m.name if m else "")
        return _guess_reasoning_parser(identifier) is not None if identifier else False

    # -- serialization -----------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict) -> ProviderConfig:
        raw_models = d.get("models")
        if isinstance(raw_models, list):
            models = [ModelConfig.from_dict(m) if isinstance(m, dict) else m for m in raw_models]
        else:
            old_model = d.get("model", "")
            old_slug = d.get("slug", "")
            if old_model or old_slug:
                models = [ModelConfig(
                    name=old_model,
                    slug=old_slug or _model_to_slug(old_model) if old_model else "",
                )]
            else:
                models = []
        return cls(
            name=d.get("name", ""),
            backend=d.get("backend", "openai"),
            endpoint=d.get("endpoint", ""),
            api_key=d.get("api_key", ""),
            server_port=int(d.get("server_port", 9123)),
            server_host=str(d.get("server_host", "0.0.0.0") or "0.0.0.0"),
            launch_template=d.get("launch_template", d.get("server_command", "")),
            max_tokens=int(d.get("max_tokens", 4096)),
            temperature=float(d.get("temperature", 1.0)),
            context_window=int(d.get("context_window", 128000)),
            priority=int(d.get("priority", 0)),
            models=models,
        )


# ---------------------------------------------------------------------------
# Provider construction from global config
# ---------------------------------------------------------------------------

def _default_provider() -> ProviderConfig:
    """Build a single default provider from ``llm.*`` config keys (backward compat)."""
    from acai.orchestrator.config import option

    model_name = option("llm.model", str, "Qwen/Qwen3-Coder-Next-FP8") or ""
    model_slug = option("llm.slug", str, "") or (_model_to_slug(model_name) if model_name else "")
    return ProviderConfig(
        name=model_slug or _model_to_slug(model_name),
        backend=option("llm.backend", str, "vllm") or "vllm",
        endpoint=option("llm.endpoint", str, "") or "",
        api_key=option("llm.api_key", str, "") or "",
        server_port=option("llm.server_port", int, 9123) or 9123,
        server_host=option("llm.server_host", str, "0.0.0.0") or "0.0.0.0",
        launch_template=option("llm.server_command", str, "") or "",
        max_tokens=option("llm.max_tokens", int, 4096) or 4096,
        temperature=option("llm.temperature", float, 1.0) or 1.0,
        context_window=option("llm.context_window", int, 128000) or 128000,
        priority=100,
        models=[ModelConfig(name=model_name, slug=model_slug)] if model_name or model_slug else [],
    )


def _load_providers_from_global() -> list[ProviderConfig]:
    """Read the ``providers`` list from the global config dict."""
    from acai.orchestrator.config import config_global

    config = config_global.get() or {}
    raw = config.get("providers")
    if not isinstance(raw, list):
        return []
    return [ProviderConfig.from_dict(d) for d in raw if isinstance(d, dict)]


# ---------------------------------------------------------------------------
# Provider persistence (workspace/acai.yaml)
# ---------------------------------------------------------------------------

def _yaml_path(workspace: str) -> str:
    return os.path.join(os.path.abspath(workspace), "acai.yaml")


def load_providers(workspace: str) -> list[ProviderConfig]:
    """Read the ``providers`` list from ``workspace/acai.yaml``."""
    path = _yaml_path(workspace)
    if not os.path.isfile(path):
        return []
    import yaml

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("providers")
    if not isinstance(raw, list):
        return []
    return [ProviderConfig.from_dict(d) for d in raw if isinstance(d, dict)]


def _provider_to_dict(p: ProviderConfig) -> dict:
    """Serialize a ProviderConfig to a clean dict for YAML."""
    d: dict = {
        "name": p.name,
        "backend": p.backend,
        "endpoint": p.endpoint,
        "api_key": p.api_key,
        "server_port": p.server_port,
        "server_host": p.server_host,
        "launch_template": p.launch_template,
        "max_tokens": p.max_tokens,
        "temperature": p.temperature,
        "context_window": p.context_window,
        "priority": p.priority,
    }
    if p.models:
        d["models"] = [asdict(m) for m in p.models]
    return d


def save_providers(workspace: str, providers: list[ProviderConfig]) -> None:
    """Write back only the ``providers`` section of ``workspace/acai.yaml``.

    Preserves any other top-level keys the user may have set.
    """
    import yaml

    path = _yaml_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing: dict = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}

    existing["providers"] = [_provider_to_dict(p) for p in providers]

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(existing, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp, path)
