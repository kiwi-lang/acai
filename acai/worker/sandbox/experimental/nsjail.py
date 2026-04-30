"""nsjail sandbox backend — **experimental**.

Runs ``acai mcp`` inside an ``nsjail`` process-level sandbox.
Provides fine-grained control over syscalls, cgroups, and resource
limits.

Status: **experimental / stub** — raises ``NotImplementedError``.
Use ``podman`` (the default) for production workloads.
"""

from __future__ import annotations

import logging

from acai.worker.sandbox.base import Sandbox
from acai.orchestrator.config import SandboxConfig

log = logging.getLogger(__name__)


class NsjailSandbox(Sandbox):
    """``nsjail``-based sandbox (Linux only).

    Uses nsjail's protobuf config for mount points, rlimits,
    seccomp-bpf, and cgroup constraints.
    """

    def __init__(self, mcp_port: int = 9200):
        self.mcp_port = mcp_port
        self._process = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def endpoint(self) -> str:
        if not self.running:
            raise RuntimeError("nsjail sandbox not started")
        return f"http://127.0.0.1:{self.mcp_port}"

    def start(
        self,
        project_path: str,
        sandbox_config: SandboxConfig | None = None,
        session_id: str = "default",
        agent_name: str = "",
    ) -> None:
        # TODO: implement nsjail launch with protobuf config
        raise NotImplementedError(
            "nsjail sandbox backend is not yet implemented.  "
            "Use 'docker' or 'podman' for now."
        )

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            self._process.wait(timeout=10)
            self._process = None
