"""Abstract sandbox interface and factory.

Every sandbox backend must subclass :class:`Sandbox` and implement
``start``, ``stop``, ``running``, and ``endpoint``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from assai.orchestrator.config import SandboxConfig

log = logging.getLogger(__name__)


class Sandbox(ABC):
    """Lifecycle manager for a sandboxed ``assai mcp`` tool server.

    Implementations start a tool server in an isolated environment
    (container, namespace, …) and expose it over HTTP so the
    worker can proxy tool calls to it.
    """

    @abstractmethod
    def start(
        self,
        project_path: str,
        sandbox_config: SandboxConfig | None = None,
        session_id: str = "default",
        agent_name: str = "",
    ) -> None:
        """Start the sandbox (idempotent).

        Parameters
        ----------
        project_path:
            Host path to the project worktree.  Mounted at
            ``/workspace`` inside the sandbox.
        sandbox_config:
            Agent-level sandbox constraints (memory, GPU, network …).
        session_id:
            Unique identifier for the session — used to name the
            sandbox so multiple sessions can coexist.
        agent_name:
            Name of the agent that will work inside this sandbox.
            Used to configure git ``user.name`` / ``user.email``
            so commits are attributable to a specific agent.
        """

    @abstractmethod
    def stop(self) -> None:
        """Stop and clean up the sandbox."""

    @property
    @abstractmethod
    def running(self) -> bool:
        """True when the sandbox process / container is alive."""

    @property
    @abstractmethod
    def endpoint(self) -> str:
        """HTTP base URL of the tool server (e.g. ``http://127.0.0.1:9200``)."""


# ------------------------------------------------------------------
# Backend registry
# ------------------------------------------------------------------

_BACKEND_ALIASES: dict[str, str] = {
    "docker": "container",
    "podman": "container",
    "container": "container",
    "bubblewrap": "bubblewrap",
    "bwrap": "bubblewrap",
    "nsjail": "nsjail",
    "firecracker": "firecracker",
}


def create_sandbox(cfg: SandboxConfig) -> Sandbox:
    """Instantiate a sandbox from a :class:`SandboxConfig`.

    The ``cfg.type`` field selects the backend; backend-specific fields
    (``image``, ``kernel``, ``vcpu_count``, ``mcp_port``, …) are
    forwarded automatically.
    """
    canonical = _BACKEND_ALIASES.get(cfg.type.lower())
    if canonical is None:
        raise ValueError(
            f"Unknown sandbox backend {cfg.type!r}.  "
            f"Choose from: {sorted(_BACKEND_ALIASES)}"
        )

    port = cfg.mcp_port

    if canonical == "container":
        from assai.worker.sandbox.container import ContainerSandbox

        runtime: str | None = cfg.runtime or None
        if runtime is None and cfg.type.lower() in ("docker", "podman"):
            runtime = cfg.type.lower()
        return ContainerSandbox(
            image=cfg.image,
            container_port=port,
            runtime=runtime,
        )

    if canonical == "bubblewrap":
        from assai.worker.sandbox.bubblewrap import BubblewrapSandbox
        return BubblewrapSandbox(mcp_port=port)

    if canonical == "nsjail":
        from assai.worker.sandbox.nsjail import NsjailSandbox
        return NsjailSandbox(mcp_port=port)

    if canonical == "firecracker":
        from assai.worker.sandbox.firecracker import FirecrackerSandbox
        kwargs: dict = dict(mcp_port=port, vcpu_count=cfg.vcpu_count)
        if cfg.kernel:
            kwargs["kernel"] = cfg.kernel
        if cfg.rootfs:
            kwargs["rootfs"] = cfg.rootfs
        if cfg.firecracker_bin:
            kwargs["firecracker_bin"] = cfg.firecracker_bin
        return FirecrackerSandbox(**kwargs)

    raise ValueError(f"Backend {canonical!r} not yet implemented")
