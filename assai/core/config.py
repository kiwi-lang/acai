"""Configuration system for assai.

Values are resolved in priority order:
    1. Environment variables (ASSAI_ prefix, e.g. ASSAI_LLM_BACKEND)
    2. Config file values (YAML)
    3. Dataclass defaults

Usage::

    from assai.config import load_config, AssaiConfig

    load_config("config.yaml")
    config = AssaiConfig()
"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field


config_global = contextvars.ContextVar("assai_config", default=None)

ENV_PREFIX = "ASSAI"


def getenv(name, expected_type):
    value = os.getenv(name)
    if value is None:
        return None
    if expected_type is bool:
        return value.lower() in ("1", "true", "yes")
    try:
        return expected_type(value)
    except (TypeError, ValueError):
        return None


def as_env_var(name):
    return ENV_PREFIX + "_" + name.replace(".", "_").upper()


def select(*args):
    """Return the first truthy non-None value."""
    prev = []
    for val in args:
        if val is not None:
            prev.append(val)
        if val:
            return val
    if prev:
        return prev[0]
    return None


def option(name, etype, default=None):
    """Resolve a config value from env var, config dict, or default."""
    config = config_global.get() or {}

    frags = name.split(".")
    env_name = as_env_var(name)
    env_value = getenv(env_name, etype)

    lookup = config
    for frag in frags[:-1]:
        lookup = lookup.get(frag, {}) if isinstance(lookup, dict) else {}
    config_value = lookup.get(frags[-1], None) if isinstance(lookup, dict) else None

    final_value = select(env_value, config_value, default)

    if final_value is None:
        return None
    try:
        return etype(final_value)
    except (ValueError, TypeError):
        return None


def defaultfield(name, etype, default=None):
    """Dataclass field whose default is resolved via ``option()`` at instantiation."""
    return field(default_factory=lambda: option(name, etype, default))


@contextmanager
def apply_config(overrides: dict):
    """Temporarily overlay config values."""
    config = config_global.get()
    old = deepcopy(config)

    if config is None:
        config = {}
        config_global.set(config)
        config = config_global.get()

    for k, v in overrides.items():
        frags = k.split(".")
        lookup = config
        for f in frags[:-1]:
            lookup = lookup.setdefault(f, {})
        lookup[frags[-1]] = v

    yield
    config_global.set(old)


def load_config(config_file=None):
    """Load a YAML config file and set it as the global config."""
    if config_file is None:
        config = {}
    else:
        import yaml

        with open(config_file) as f:
            config = yaml.safe_load(f) or {}

    config_global.set(config)
    return config


def show_config(config_obj, depth=0):
    """Print the current config for debugging."""
    from dataclasses import fields as dc_fields, is_dataclass

    if not is_dataclass(config_obj):
        return

    for f in dc_fields(config_obj):
        val = getattr(config_obj, f.name)
        indent = "  " * depth

        if is_dataclass(val):
            print(f"{indent}{f.name}:")
            show_config(val, depth + 1)
        else:
            env_hint = as_env_var(f.name) if depth == 0 else ""
            print(f"{indent}{f.name:<24}: {val!s:<40} {env_hint}")


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ScribeConfig:
    trigger: str = defaultfield("scribe.trigger", str, "event")
    specs_dir: str = defaultfield("scribe.specs_dir", str, "specs")


@dataclass
class CuratorConfig:
    strategy: str = defaultfield("curator.strategy", str, "references")


@dataclass
class WorkerConfig:
    max_retries: int = defaultfield("worker.max_retries", int, 3)
    sandbox: str = defaultfield("worker.sandbox", str, "container")
    timeout: int = defaultfield("worker.timeout", int, 300)
    tasks_dir: str = defaultfield("worker.tasks_dir", str, "tasks")
    host: str = defaultfield("worker.host", str, "0.0.0.0")
    port: int = defaultfield("worker.port", int, 5051)
    orchestrator_url: str = defaultfield(
        "worker.orchestrator_url", str, "http://localhost:5050/agent",
    )


@dataclass
class GitConfig:
    repo_path: str = defaultfield("git.repo_path", str, ".")
    worktree_dir: str = defaultfield("git.worktree_dir", str, ".worktrees")
    auto_commit: bool = defaultfield("git.auto_commit", bool, True)


@dataclass
class QueueConfig:
    url: str = defaultfield("queue.url", str, "sqlite:///work.db")
    poll_interval: int = defaultfield("queue.poll_interval", int, 5)


def _model_to_slug(model: str) -> str:
    """Derive a short slug from a model path like ``Org/Model-Name``."""
    return model.rsplit("/", 1)[-1].lower().replace("_", "-")


@dataclass
class LLMConfig:
    backend: str = defaultfield("llm.backend", str, "vllm")
    model: str = defaultfield("llm.model", str, "Qwen/Qwen3-Coder-Next-FP8")
    slug: str = defaultfield("llm.slug", str, "")
    endpoint: str = defaultfield("llm.endpoint", str, "http://127.0.0.1:9123")
    max_tokens: int = defaultfield("llm.max_tokens", int, 4096)
    temperature: float = defaultfield("llm.temperature", float, 0.7)
    api_key: str = defaultfield("llm.api_key", str, "")
    server_command: str = defaultfield("llm.server_command", str, "")
    server_port: int = defaultfield("llm.server_port", int, 9123)

    def __post_init__(self):
        if not self.slug:
            self.slug = _model_to_slug(self.model)


@dataclass
class ProviderConfig:
    """An LLM provider (local or remote API)."""

    name: str = ""
    backend: str = "openai"
    model: str = ""
    slug: str = ""
    endpoint: str = ""
    api_key: str = ""
    server_port: int = 9123
    server_command: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    priority: int = 0
    roles: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.model and not self.slug:
            self.slug = _model_to_slug(self.model)

    def to_llm_config(self) -> LLMConfig:
        """Convert to an ``LLMConfig`` for the worker/LLM server."""
        return LLMConfig(
            backend=self.backend,
            model=self.model,
            slug=self.slug,
            endpoint=self.endpoint or f"http://127.0.0.1:{self.server_port}",
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            api_key=self.api_key,
            server_command=self.server_command,
            server_port=self.server_port,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ProviderConfig:
        roles = d.get("roles")
        if isinstance(roles, str):
            roles = [r.strip() for r in roles.split(",") if r.strip()]
        return cls(
            name=d.get("name", ""),
            backend=d.get("backend", "openai"),
            model=d.get("model", ""),
            slug=d.get("slug", ""),
            endpoint=d.get("endpoint", ""),
            api_key=d.get("api_key", ""),
            server_port=int(d.get("server_port", 9123)),
            server_command=d.get("server_command", ""),
            max_tokens=int(d.get("max_tokens", 4096)),
            temperature=float(d.get("temperature", 0.7)),
            priority=int(d.get("priority", 0)),
            roles=roles if isinstance(roles, list) else [],
        )

    @classmethod
    def from_llm_config(cls, llm: LLMConfig, name: str = "",
                        priority: int = 0,
                        roles: list[str] | None = None) -> ProviderConfig:
        """Build a provider from the legacy ``LLMConfig``."""
        return cls(
            name=name or llm.slug,
            backend=llm.backend,
            model=llm.model,
            slug=llm.slug,
            endpoint=llm.endpoint,
            api_key=llm.api_key,
            server_port=llm.server_port,
            server_command=llm.server_command,
            max_tokens=llm.max_tokens,
            temperature=llm.temperature,
            priority=priority,
            roles=roles or ["worker"],
        )


def _load_providers_from_global() -> list[ProviderConfig]:
    """Read the ``providers`` list from the global config dict."""
    config = config_global.get() or {}
    raw = config.get("providers")
    if not isinstance(raw, list):
        return []
    return [ProviderConfig.from_dict(d) for d in raw if isinstance(d, dict)]


@dataclass
class AssaiConfig:
    workspace: str = defaultfield("workspace", str, "workspace")
    dump_rendered_request: bool = defaultfield("dump_rendered_request", bool, False)
    scribe: ScribeConfig = field(default_factory=ScribeConfig)
    curator: CuratorConfig = field(default_factory=CuratorConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    git: GitConfig = field(default_factory=GitConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    providers: list[ProviderConfig] = field(default_factory=_load_providers_from_global)

    def __post_init__(self):
        ws = os.path.abspath(self.workspace)
        self.workspace = ws
        os.makedirs(ws, exist_ok=True)

        if not os.path.isabs(self.scribe.specs_dir):
            self.scribe.specs_dir = os.path.join(ws, self.scribe.specs_dir)

        if not os.path.isabs(self.git.worktree_dir):
            self.git.worktree_dir = os.path.join(ws, self.git.worktree_dir)

        if not os.path.isabs(self.worker.tasks_dir):
            self.worker.tasks_dir = os.path.join(ws, self.worker.tasks_dir)

        url = self.queue.url
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
            db_path = url[len("sqlite:///"):]
            if not os.path.isabs(db_path):
                self.queue.url = f"sqlite:///{os.path.join(ws, db_path)}"

        if self.llm.backend != "openai" and self.llm.endpoint == "http://127.0.0.1:9123":
            self.llm.endpoint = f"http://127.0.0.1:{self.llm.server_port}"

        if not self.providers:
            self.providers = [
                ProviderConfig.from_llm_config(self.llm, priority=100, roles=["worker"]),
            ]

    def active_provider(self) -> ProviderConfig:
        """Return the provider matching ``config.llm``, or the highest-priority one."""
        for p in self.providers:
            if p.slug == self.llm.slug and p.backend == self.llm.backend:
                return p
        best = sorted(self.providers, key=lambda p: -p.priority)
        return best[0] if best else ProviderConfig.from_llm_config(self.llm)

    def get_provider(self, name: str) -> ProviderConfig | None:
        """Look up a provider by name."""
        for p in self.providers:
            if p.name == name:
                return p
        return None


# ---------------------------------------------------------------------------
# Provider persistence (workspace/assai.yaml)
# ---------------------------------------------------------------------------

def _yaml_path(workspace: str) -> str:
    return os.path.join(os.path.abspath(workspace), "assai.yaml")


def load_providers(workspace: str) -> list[ProviderConfig]:
    """Read the ``providers`` list from ``workspace/assai.yaml``."""
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


def save_providers(workspace: str, providers: list[ProviderConfig]) -> None:
    """Write back only the ``providers`` section of ``workspace/assai.yaml``.

    Preserves any other top-level keys the user may have set.
    """
    import yaml

    path = _yaml_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing: dict = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}

    existing["providers"] = [p.to_dict() for p in providers]

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(existing, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp, path)
