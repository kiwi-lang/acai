"""Configuration system for acai.

Values are resolved in priority order:
    1. Environment variables (ACAI_ prefix, e.g. ACAI_LLM_BACKEND)
    2. Config file values (YAML)
    3. Dataclass defaults

Usage::

    from acai.config import load_config, AcaiConfig

    load_config("config.yaml")
    config = AcaiConfig()
"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field


config_global = contextvars.ContextVar("acai_config", default=None)

ENV_PREFIX = "ACAI"


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


# ---------------------------------------------------------------------------
# sandboxfield — dataclass field annotated with backend affinity
# ---------------------------------------------------------------------------

_ALL_BACKENDS = frozenset({"container", "firecracker", "bubblewrap", "nsjail"})
_CONTAINER = frozenset({"container"})
_FIRECRACKER = frozenset({"firecracker"})
_BUBBLEWRAP = frozenset({"bubblewrap"})
_NSJAIL = frozenset({"nsjail"})


def sandboxfield(default, *, backends: frozenset[str] = _ALL_BACKENDS):
    """Dataclass field with ``metadata["backends"]`` indicating which sandbox backends use it.

    Usage::

        @dataclass
        class SandboxConfig:
            network: bool = sandboxfield(True)                           # all backends
            image:   str  = sandboxfield("acai-sandbox", backends=_CONTAINER)  # docker/podman only

    Introspection::

        from dataclasses import fields
        for f in fields(SandboxConfig):
            print(f.name, f.metadata.get("backends", set()))
    """
    md: dict = {"backends": backends}
    if isinstance(default, list):
        return field(default_factory=list, metadata=md)
    return field(default=default, metadata=md)


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
# SandboxConfig
# ---------------------------------------------------------------------------

@dataclass
class SandboxConfig:
    """System-wide sandbox configuration.

    Each field is annotated with the set of backends that use it
    (accessible via ``field.metadata["backends"]``).  A single flat
    config works for all backends — irrelevant fields are ignored.

    Resolving order (for the system-wide default built by
    :meth:`from_global_config`):

    1. Env var  — ``ACAI_SANDBOX_<FIELD>``
    2. YAML     — ``sandbox.<field>``
    3. Default  — the value passed to ``sandboxfield()``.
    """

    # -- Common (all backends) ----------------------------------------------
    type: str = sandboxfield("podman")
    network: bool = sandboxfield(True)
    gpu: bool = sandboxfield(False, backends=_CONTAINER)
    timeout: int = sandboxfield(120)
    memory_limit: str = sandboxfield("4G", backends=_CONTAINER | _FIRECRACKER | _NSJAIL)
    writable_paths: list[str] = sandboxfield([], backends=_CONTAINER | _BUBBLEWRAP | _NSJAIL)
    readonly_paths: list[str] = sandboxfield([], backends=_CONTAINER | _BUBBLEWRAP | _NSJAIL)

    # -- Container (docker / podman) ----------------------------------------
    image: str = sandboxfield("acai-sandbox", backends=_CONTAINER)
    runtime: str = sandboxfield("podman", backends=_CONTAINER)
    rootless: bool = sandboxfield(True, backends=_CONTAINER)

    # -- Firecracker (microVM) ----------------------------------------------
    kernel: str = sandboxfield("", backends=_FIRECRACKER)
    rootfs: str = sandboxfield("", backends=_FIRECRACKER)
    vcpu_count: int = sandboxfield(2, backends=_FIRECRACKER)
    firecracker_bin: str = sandboxfield("", backends=_FIRECRACKER)

    # -- Bubblewrap ---------------------------------------------------------
    unshare_user: bool = sandboxfield(True, backends=_BUBBLEWRAP)
    unshare_pid: bool = sandboxfield(True, backends=_BUBBLEWRAP)
    unshare_ipc: bool = sandboxfield(True, backends=_BUBBLEWRAP)
    dev_mode: str = sandboxfield("minimal", backends=_BUBBLEWRAP)

    # -- Nsjail -------------------------------------------------------------
    nsjail_config: str = sandboxfield("", backends=_NSJAIL)
    cgroup_pids_max: int = sandboxfield(64, backends=_NSJAIL)
    rlimit_as: str = sandboxfield("max", backends=_NSJAIL)
    seccomp_policy: str = sandboxfield("", backends=_NSJAIL)

    # -- System-level -------------------------------------------------------
    mcp_port: int = sandboxfield(9200)

    # -- Helpers ------------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict) -> SandboxConfig:
        """Create from a dict, silently ignoring unknown keys."""
        import dataclasses as _dc
        known = {f.name for f in _dc.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def from_global_config(cls) -> SandboxConfig:
        """Build from the global config / env vars.

        Maps each field to ``option("sandbox.<name>", ...)``.
        """
        import dataclasses as _dc

        prefix = "sandbox"
        kwargs: dict = {}
        base = cls()
        for f in _dc.fields(cls):
            base_val = getattr(base, f.name)
            etype = type(base_val)
            if etype is list:
                kwargs[f.name] = base_val
                continue
            val = option(f"{prefix}.{f.name}", etype, base_val)
            kwargs[f.name] = val if val is not None else base_val
        return cls(**kwargs)

    @classmethod
    def fields_for_backend(cls, backend: str) -> list[str]:
        """Return the field names relevant to *backend*.

        Useful for UIs that want to show/hide options dynamically.
        """
        import dataclasses as _dc
        from acai.worker.sandbox.base import _BACKEND_ALIASES

        canonical = _BACKEND_ALIASES.get(backend, backend)
        return [
            f.name
            for f in _dc.fields(cls)
            if canonical in f.metadata.get("backends", set())
        ]


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
    task_timeout: int = defaultfield("queue.task_timeout", int, 300)


@dataclass
class AuditConfig:
    enabled: bool = defaultfield("audit.enabled", bool, True)
    dir: str = defaultfield("audit.dir", str, ".audit")


@dataclass
class CIConfig:
    """CI / CD integration settings.

    ``platform`` controls which backend the CI tools use.  Set to
    ``"auto"`` (default) to detect from the git remote URL, or force
    a specific platform (``"github"``, ``"gitlab"``, ``"codeberg"``).
    """

    platform: str = defaultfield("ci.platform", str, "auto")
    token: str = defaultfield("ci.token", str, "")
    default_branch: str = defaultfield("ci.default_branch", str, "main")
    poll_interval: int = defaultfield("ci.poll_interval", int, 30)
    auto_fix: bool = defaultfield("ci.auto_fix", bool, False)

    @classmethod
    def from_dict(cls, d: dict) -> CIConfig:
        import dataclasses as _dc
        known = {f.name for f in _dc.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def _model_to_slug(model: str) -> str:
    """Derive a short slug from a model path like ``Org/Model-Name``."""
    return model.rsplit("/", 1)[-1].lower().replace("_", "-")


# ---------------------------------------------------------------------------
# Tool-call parser heuristics (used by the default vLLM launch template)
# ---------------------------------------------------------------------------

_MODEL_PARSER_MAP: list[tuple[str, str]] = [
    ("qwen3-coder", "qwen3_xml"),
    ("qwen3_coder", "qwen3_xml"),
    ("qwen2.5", "hermes"),
    ("qwq", "hermes"),
    ("llama-4", "llama4_pythonic"),
    ("llama-3", "llama3_json"),
    ("mistral", "mistral"),
    ("deepseek-v3", "deepseek_v3"),
    ("deepseek-r1", "deepseek_v3"),
    ("granite-4", "granite4"),
    ("granite-3", "granite"),
    ("hermes", "hermes"),
]

_MODEL_REASONING_PARSER_MAP: list[tuple[str, str]] = [
    ("qwen3", "qwen3"),
    ("qwq", "deepseek_r1"),
    ("deepseek-r1", "deepseek_r1"),
    ("deepseek-v3", "deepseek_v3"),
    ("granite-3.2", "granite"),
    ("granite-4", "granite"),
    ("glm-4", "glm45"),
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
    parser = _guess_tool_parser(model)
    parts = [
        "vllm serve {model}",
        "--served-model-name {slug}",
        "--port {server_port}",
        "--enable-auto-tool-choice",
        f"--tool-call-parser {parser}",
        "--enable-prefix-caching",
        "--kv-cache-dtype fp8",
        "--max-num-seqs 1",
    ]
    supports_reasoning = False

    if supports_reasoning:
        reasoning_parser = _guess_reasoning_parser(model)
        if reasoning_parser:
            parts.append(f"--reasoning-parser {reasoning_parser}")
            parts.append("--default-chat-template-kwargs '{{\"enable_thinking\": false}}'")
            parts.append(
                "--reasoning-config '{{\"reasoning_start_str\": \"<think>\", \"reasoning_end_str\": \"I have to give the solution based on the reasoning directly now.</think>\"}}'"
            )

    if "qwen3-coder" in model.lower().replace("/", "-"):
        parts += [
            "--max-model-len 170000",
            "--gpu-memory-utilization 0.90",
            "--attention-backend flashinfer",
        ]
    return " ".join(parts)


_DEFAULT_TEMPLATES: dict[str, str] = {
    "llamacpp": "llama-server -m {model} --host 0.0.0.0 --port {server_port}",
    "local": "llama-server -m {model} --host 0.0.0.0 --port {server_port}",
}


@dataclass
class ProviderConfig:
    """An LLM provider -- local server or remote API.

    ``launch_template`` is a Python format string resolved via
    ``template.format(**asdict(self))``.  When empty, a backend-specific
    default is used (vLLM, llama.cpp).  When no default exists for the
    backend the provider is treated as *unmanaged* (remote endpoint).
    """

    name: str = ""
    backend: str = "openai"
    model: str = ""
    slug: str = ""
    endpoint: str = ""
    api_key: str = ""
    server_port: int = 9123
    launch_template: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    context_window: int = 128000
    priority: int = 0
    roles: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.model and not self.slug:
            self.slug = _model_to_slug(self.model)
        if not self.endpoint and self.server_port:
            self.endpoint = f"http://127.0.0.1:{self.server_port}"

    # -- command building --------------------------------------------------

    def build_command(self) -> str:
        """Return the resolved server launch command, or ``""`` if unmanaged."""
        if self.launch_template:
            return self.launch_template.format(**asdict(self))
        if self.backend == "vllm":
            return _default_vllm_template(self.model).format(**asdict(self))
        tpl = _DEFAULT_TEMPLATES.get(self.backend, "")
        if tpl:
            return tpl.format(**asdict(self))
        return ""

    @property
    def managed(self) -> bool:
        """True when this provider requires a local server process."""
        return bool(self.build_command())

    # -- serialization -----------------------------------------------------

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
            launch_template=d.get("launch_template", d.get("server_command", "")),
            max_tokens=int(d.get("max_tokens", 4096)),
            temperature=float(d.get("temperature", 0.7)),
            context_window=int(d.get("context_window", 128000)),
            priority=int(d.get("priority", 0)),
            roles=roles if isinstance(roles, list) else [],
        )


def _default_provider() -> ProviderConfig:
    """Build a single default provider from ``llm.*`` config keys (backward compat)."""
    return ProviderConfig(
        name=option("llm.slug", str, "") or _model_to_slug(option("llm.model", str, "Qwen/Qwen3-Coder-Next-FP8") or ""),
        backend=option("llm.backend", str, "vllm") or "vllm",
        model=option("llm.model", str, "Qwen/Qwen3-Coder-Next-FP8") or "",
        slug=option("llm.slug", str, "") or "",
        endpoint=option("llm.endpoint", str, "") or "",
        api_key=option("llm.api_key", str, "") or "",
        server_port=option("llm.server_port", int, 9123) or 9123,
        launch_template=option("llm.server_command", str, "") or "",
        max_tokens=option("llm.max_tokens", int, 4096) or 4096,
        temperature=option("llm.temperature", float, 0.7) or 0.7,
        context_window=option("llm.context_window", int, 128000) or 128000,
        priority=100,
        roles=["worker"],
    )


def _load_providers_from_global() -> list[ProviderConfig]:
    """Read the ``providers`` list from the global config dict."""
    config = config_global.get() or {}
    raw = config.get("providers")
    if not isinstance(raw, list):
        return []
    return [ProviderConfig.from_dict(d) for d in raw if isinstance(d, dict)]


@dataclass
class AcaiConfig:
    workspace: str = defaultfield("workspace", str, "workspace")
    dump_rendered_request: bool = defaultfield("dump_rendered_request", bool, False)
    scribe: ScribeConfig = field(default_factory=ScribeConfig)
    curator: CuratorConfig = field(default_factory=CuratorConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig.from_global_config)
    git: GitConfig = field(default_factory=GitConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    ci: CIConfig = field(default_factory=CIConfig)
    providers: list[ProviderConfig] = field(default_factory=_load_providers_from_global)
    _active_name: str = ""

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

        if not os.path.isabs(self.audit.dir):
            self.audit.dir = os.path.join(ws, self.audit.dir)

        url = self.queue.url
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
            db_path = url[len("sqlite:///"):]
            if not os.path.isabs(db_path):
                self.queue.url = f"sqlite:///{os.path.join(ws, db_path)}"

        if not self.providers:
            self.providers = [_default_provider()]

    def active_provider(self) -> ProviderConfig:
        """Return the explicitly activated provider, or the highest-priority one."""
        if self._active_name:
            p = self.get_provider(self._active_name)
            if p:
                return p
        best = sorted(self.providers, key=lambda p: -p.priority)
        return best[0] if best else _default_provider()

    def set_active(self, name: str) -> None:
        """Explicitly activate a provider by name."""
        self._active_name = name

    def local_provider(self) -> ProviderConfig | None:
        """Return the highest-priority managed provider, or ``None``."""
        managed = [p for p in self.providers if p.managed]
        if not managed:
            return None
        return sorted(managed, key=lambda p: -p.priority)[0]

    def get_provider(self, name: str) -> ProviderConfig | None:
        """Look up a provider by name."""
        for p in self.providers:
            if p.name == name:
                return p
        return None


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


_PERSISTABLE_SECTIONS = ("sandbox", "worker", "git", "queue", "audit", "ci")


def config_to_dict(config: AcaiConfig) -> dict:
    """Serialise the mutable sections of *config* to a plain dict.

    Only sections that are safe for the settings UI are included.
    """
    out: dict = {"workspace": config.workspace}
    for section in _PERSISTABLE_SECTIONS:
        out[section] = asdict(getattr(config, section))
    return out


def save_config(workspace: str, config: AcaiConfig) -> None:
    """Persist the mutable config sections to ``workspace/acai.yaml``.

    Follows the same atomic-write pattern as :func:`save_providers`:
    reads the existing YAML, merges in updated sections, writes back.
    The ``providers`` key is left untouched.
    """
    import yaml

    path = _yaml_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing: dict = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}

    for section in _PERSISTABLE_SECTIONS:
        existing[section] = asdict(getattr(config, section))

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(existing, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp, path)


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

    existing["providers"] = [asdict(p) for p in providers]

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(existing, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp, path)
