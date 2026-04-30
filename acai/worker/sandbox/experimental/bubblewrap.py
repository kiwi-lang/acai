"""Bubblewrap (bwrap) sandbox backend — **experimental**.

Runs ``acai mcp`` inside a ``bwrap`` user-namespace sandbox on the
host.  No container image is needed — the host Python environment is
re-used with restricted filesystem and network access.

Status: **experimental / stub** — raises ``NotImplementedError``.
Use ``podman`` (the default) for production workloads.
"""

from __future__ import annotations

import logging

from acai.worker.sandbox.base import Sandbox
from acai.orchestrator.config import SandboxConfig

log = logging.getLogger(__name__)


class BubblewrapSandbox(Sandbox):
    """``bwrap``-based sandbox (Linux only).

    The tool server process runs with restricted mounts and
    optional network isolation via ``bwrap`` flags.
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
            raise RuntimeError("bubblewrap sandbox not started")
        return f"http://127.0.0.1:{self.mcp_port}"

    def start(
        self,
        project_path: str,
        sandbox_config: SandboxConfig | None = None,
        session_id: str = "default",
        agent_name: str = "",
    ) -> None:
        # TODO: implement bwrap launch
        #   bwrap \
        #     --ro-bind /usr /usr \
        #     --ro-bind /lib /lib \
        #     --ro-bind /lib64 /lib64 \
        #     --bind <project_path> /workspace \
        #     --proc /proc \
        #     --dev /dev \
        #     --tmpfs /tmp \
        #     --unshare-net (if !sandbox_config.network) \
        #     -- acai mcp --host 127.0.0.1 --port <mcp_port>
        raise NotImplementedError(
            "Bubblewrap sandbox backend is not yet implemented.  "
            "Use 'docker' or 'podman' for now."
        )

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            self._process.wait(timeout=10)
            self._process = None
