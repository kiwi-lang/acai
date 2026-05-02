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


from acai.provider.config import (  # noqa: F401
    ModelConfig,
    ProviderConfig,
    _model_to_slug,
    _default_provider,
    _load_providers_from_global,
)


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


from acai.provider.config import (  # noqa: F401
    load_providers,
    save_providers,
    _provider_to_dict,
)


# ---------------------------------------------------------------------------
# Config persistence helpers (workspace/acai.yaml)
# ---------------------------------------------------------------------------

def _yaml_path(workspace: str) -> str:
    return os.path.join(os.path.abspath(workspace), "acai.yaml")


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


