"""Podman-based sandbox for running agent tools in isolation.

``SandboxManager`` handles the lifecycle of a Podman container that
runs the ``assai mcp`` tool server.  The worker proxies tool calls
to this container instead of running them in-process.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assai.core.agent_store import SandboxConfig

log = logging.getLogger(__name__)

SANDBOXED_NAMESPACES: set[str] = {"code", "git", "shell", "filesystem"}


def is_sandboxed(tool_name: str) -> bool:
    """Return True if *tool_name* should run inside the sandbox."""
    ns = tool_name.split(".", 1)[0] if "." in tool_name else ""
    return ns in SANDBOXED_NAMESPACES


class SandboxManager:
    """Manages a Podman container running the ``assai mcp`` tool server.

    Lifecycle is per-session: one container is started and reused for
    all tasks until ``stop()`` is called (typically at worker shutdown).
    """

    def __init__(
        self,
        image: str = "assai-sandbox",
        container_port: int = 9200,
    ):
        self.image = image
        self.container_port = container_port
        self._container_name: str | None = None
        self._host_port: int | None = None
        self._project_path: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        if self._container_name is None:
            return False
        return self._is_alive(self._container_name)

    @property
    def endpoint(self) -> str:
        """HTTP base URL of the tool server running in the container."""
        if self._host_port is None:
            raise RuntimeError("sandbox not started")
        return f"http://127.0.0.1:{self._host_port}"

    def start(
        self,
        project_path: str,
        sandbox_config: SandboxConfig | None = None,
        session_id: str = "default",
    ) -> None:
        """Start the sandbox container (idempotent).

        If a container for this session is already running, this is a
        no-op.  If the project path changed the old container is
        replaced.
        """
        name = f"assai-sandbox-{session_id}"

        if self._container_name == name and self.running:
            if self._project_path == project_path:
                log.debug("sandbox already running: %s", name)
                return
            log.info("project path changed, restarting sandbox")
            self.stop()

        self._kill_stale(name)
        self._guard_branch(project_path)

        cmd = self._build_run_cmd(name, project_path, sandbox_config)
        log.info("starting sandbox: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"podman run failed (rc={proc.returncode}): {proc.stderr.strip()}"
            )

        self._container_name = name
        self._project_path = project_path
        self._host_port = self._resolve_host_port(name)

        self._wait_healthy()
        log.info(
            "sandbox ready  name=%s  endpoint=%s",
            name, self.endpoint,
        )

    def stop(self) -> None:
        """Stop and remove the sandbox container."""
        if self._container_name is None:
            return
        name = self._container_name
        log.info("stopping sandbox: %s", name)
        subprocess.run(
            ["podman", "stop", "-t", "5", name],
            capture_output=True, text=True,
        )
        subprocess.run(
            ["podman", "rm", "-f", name],
            capture_output=True, text=True,
        )
        self._container_name = None
        self._host_port = None
        self._project_path = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_run_cmd(
        self,
        name: str,
        project_path: str,
        sandbox_config: SandboxConfig | None,
    ) -> list[str]:
        cmd = [
            "podman", "run", "-d",
            "--name", name,
            "-p", f"0:{self.container_port}",
            "-v", f"{project_path}:/workspace:z",
        ]

        if sandbox_config is not None:
            if sandbox_config.memory_limit:
                cmd += ["--memory", sandbox_config.memory_limit]
            if sandbox_config.gpu:
                cmd += ["--device", "nvidia.com/gpu=all"]
            if not sandbox_config.network:
                cmd += ["--network", "none"]
            for p in sandbox_config.readonly_paths:
                cmd += ["-v", f"{p}:{p}:ro,z"]
            for p in sandbox_config.writable_paths:
                cmd += ["-v", f"{p}:{p}:z"]
        cmd.append(self.image)
        return cmd

    def _resolve_host_port(self, name: str) -> int:
        """Query podman for the host port mapped to the container port."""
        proc = subprocess.run(
            ["podman", "port", name, str(self.container_port)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"failed to resolve port: {proc.stderr.strip()}")
        mapping = proc.stdout.strip()
        # Output format: "0.0.0.0:12345" or "[::]:12345"
        port_str = mapping.rsplit(":", 1)[-1]
        return int(port_str)

    def _wait_healthy(self, retries: int = 30, interval: float = 1.0) -> None:
        """Poll the /health endpoint until the tool server is ready."""
        import requests

        url = f"{self.endpoint}/health"
        for i in range(retries):
            try:
                r = requests.get(url, timeout=2)
                if r.status_code == 200:
                    log.info("sandbox healthy after %d checks", i + 1)
                    return
            except requests.ConnectionError:
                pass
            time.sleep(interval)
        raise RuntimeError(
            f"sandbox did not become healthy after {retries * interval:.0f}s"
        )

    def _is_alive(self, name: str) -> bool:
        proc = subprocess.run(
            ["podman", "inspect", "--format", "{{.State.Running}}", name],
            capture_output=True, text=True,
        )
        return proc.stdout.strip().lower() == "true"

    def _kill_stale(self, name: str) -> None:
        """Remove any leftover container with the same name."""
        if self._is_alive(name):
            log.warning("removing stale sandbox container: %s", name)
            subprocess.run(["podman", "stop", "-t", "3", name], capture_output=True)
        subprocess.run(["podman", "rm", "-f", name], capture_output=True)

    @staticmethod
    def _guard_branch(project_path: str) -> None:
        """Verify the worktree is on the expected branch (no detached HEAD)."""
        proc = subprocess.run(
            ["git", "-C", project_path, "branch", "--show-current"],
            capture_output=True, text=True,
        )
        branch = proc.stdout.strip()
        if not branch:
            log.warning(
                "sandbox project at %s has detached HEAD — proceeding anyway",
                project_path,
            )
        else:
            log.info("sandbox branch guard OK: %s on branch %s", project_path, branch)
