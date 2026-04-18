"""Docker / Podman sandbox backend.

Starts a container running ``assai mcp`` and proxies tool calls to it.
The container CLI (``docker`` or ``podman``) is auto-detected at
construction time but can be overridden.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time

from assai.worker.sandbox.base import Sandbox
from assai.orchestrator.config import SandboxConfig

log = logging.getLogger(__name__)


def _detect_runtime() -> str:
    """Return ``"docker"`` or ``"podman"`` depending on what is on PATH."""
    for candidate in ("podman", "docker"):
        if shutil.which(candidate):
            return candidate
    raise RuntimeError("Neither docker nor podman found on PATH")


def _find_repo_root() -> str | None:
    """Walk up from this file to find the directory containing ``Containerfile``."""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.isfile(os.path.join(d, "Containerfile")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


class ContainerSandbox(Sandbox):
    """Manages a Docker/Podman container running ``assai mcp``.

    The project worktree is bind-mounted at ``/workspace`` inside the
    container.  One container is started per *session_id* and reused
    until :meth:`stop` is called.
    """

    def __init__(
        self,
        image: str = "assai-sandbox",
        container_port: int = 9200,
        runtime: str | None = None,
    ):
        self.container_port = container_port
        self.runtime = runtime or _detect_runtime()
        # Podman requires fully-qualified image names when no
        # unqualified-search registries are configured.
        if self.runtime == "podman" and "/" not in image:
            image = f"localhost/{image}"
        self.image = image
        self._container_name: str | None = None
        self._host_port: int | None = None
        self._project_path: str | None = None

    # ------------------------------------------------------------------
    # Sandbox interface
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        if self._container_name is None:
            return False
        return self._is_alive(self._container_name)

    @property
    def endpoint(self) -> str:
        if self._host_port is None:
            raise RuntimeError("sandbox not started")
        return f"http://127.0.0.1:{self._host_port}"

    def start(
        self,
        project_path: str,
        sandbox_config: SandboxConfig | None = None,
        session_id: str = "default",
        agent_name: str = "",
    ) -> None:
        name = f"assai-sandbox-{session_id}"

        if self._container_name == name and self.running:
            if self._project_path == project_path:
                log.debug("sandbox already running: %s", name)
                return
            log.info("project path changed, restarting sandbox")
            self.stop()

        self._kill_stale(name)
        self._ensure_image()

        cmd = self._build_run_cmd(name, project_path, sandbox_config)
        log.info("starting sandbox [%s]: %s", self.runtime, " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{self.runtime} run failed (rc={proc.returncode}): "
                f"{proc.stderr.strip()}"
            )

        self._container_name = name
        self._project_path = project_path
        self._host_port = self._resolve_host_port(name)

        self._configure_git_identity(agent_name or "assai-agent")
        self._wait_healthy()
        log.info("sandbox ready  name=%s  endpoint=%s", name, self.endpoint)

    def stop(self) -> None:
        if self._container_name is None:
            return
        name = self._container_name
        log.info("stopping sandbox: %s", name)
        subprocess.run(
            [self.runtime, "stop", "-t", "5", name],
            capture_output=True, text=True,
        )
        subprocess.run(
            [self.runtime, "rm", "-f", name],
            capture_output=True, text=True,
        )
        self._container_name = None
        self._host_port = None
        self._project_path = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_image(self) -> None:
        """Build the sandbox image automatically if it doesn't exist.

        Checks the local image store for ``self.image``.  If missing,
        looks for a ``Containerfile`` in the assai repo root and
        builds it.  This makes first-run seamless — no manual
        ``podman build`` / ``docker build`` step required.
        """
        proc = subprocess.run(
            [self.runtime, "image", "inspect", self.image],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return

        repo_root = _find_repo_root()
        if repo_root is None:
            raise RuntimeError(
                f"Sandbox image {self.image!r} not found and could not "
                f"locate Containerfile to auto-build it."
            )

        containerfile = os.path.join(repo_root, "Containerfile")
        # Strip the localhost/ prefix for the tag if present — podman
        # build adds it automatically for local images.
        tag = self.image
        log.info(
            "sandbox image %r not found — building from %s  (this may take a minute)",
            tag, containerfile,
        )
        build_cmd = [
            self.runtime, "build",
            "-t", tag,
            "-f", containerfile,
            repo_root,
        ]
        proc = subprocess.run(build_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to build sandbox image {tag!r} "
                f"(rc={proc.returncode}):\n{proc.stderr.strip()}"
            )
        log.info("sandbox image %r built successfully", tag)

    def _build_run_cmd(
        self,
        name: str,
        project_path: str,
        sandbox_config: SandboxConfig | None,
    ) -> list[str]:
        cmd = [
            self.runtime, "run", "-d",
            "--name", name,
            "-p", f":{self.container_port}",
            "-v", f"{project_path}:/workspace",
        ]

        if sandbox_config is not None:
            if sandbox_config.memory_limit:
                cmd += ["--memory", sandbox_config.memory_limit]
            if sandbox_config.gpu:
                cmd += ["--device", "nvidia.com/gpu=all"]
            if not sandbox_config.network:
                cmd += ["--network", "none"]
            for p in sandbox_config.readonly_paths:
                cmd += ["-v", f"{p}:{p}:ro"]
            for p in sandbox_config.writable_paths:
                cmd += ["-v", f"{p}:{p}"]

        cmd.append(self.image)
        return cmd

    def _resolve_host_port(self, name: str) -> int:
        proc = subprocess.run(
            [self.runtime, "port", name, str(self.container_port)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"failed to resolve port: {proc.stderr.strip()}")
        mapping = proc.stdout.strip()
        # "0.0.0.0:12345" or "[::]:12345"
        port_str = mapping.rsplit(":", 1)[-1]
        return int(port_str)

    def _wait_healthy(self, retries: int = 60, interval: float = 1.0) -> None:
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

            if not self.running:
                logs = self._container_logs()
                raise RuntimeError(
                    f"Sandbox container exited before becoming healthy.\n"
                    f"Container logs:\n{logs}"
                )

            time.sleep(interval)

        logs = self._container_logs()
        raise RuntimeError(
            f"sandbox did not become healthy after {retries * interval:.0f}s\n"
            f"Container logs:\n{logs}"
        )

    def _is_alive(self, name: str) -> bool:
        proc = subprocess.run(
            [self.runtime, "inspect", "--format", "{{.State.Running}}", name],
            capture_output=True, text=True,
        )
        return proc.stdout.strip().lower() == "true"

    def _kill_stale(self, name: str) -> None:
        if self._is_alive(name):
            log.warning("removing stale sandbox container: %s", name)
            subprocess.run(
                [self.runtime, "stop", "-t", "3", name],
                capture_output=True,
            )
        subprocess.run(
            [self.runtime, "rm", "-f", name],
            capture_output=True,
        )

    def _configure_git_identity(self, agent_name: str) -> None:
        """Set git user.name and user.email inside the container.

        This makes every commit attributable to the specific agent
        that produced it.
        """
        email = f"{agent_name}@assai.localhost"
        for key, value in [("user.name", agent_name), ("user.email", email)]:
            proc = subprocess.run(
                [self.runtime, "exec", self._container_name,
                 "git", "config", "--global", key, value],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                log.warning("git config %s failed: %s", key, proc.stderr.strip())
        log.info("sandbox git identity: %s <%s>", agent_name, email)

    def _container_logs(self, tail: int = 50) -> str:
        if self._container_name is None:
            return "(no container)"
        proc = subprocess.run(
            [self.runtime, "logs", "--tail", str(tail), self._container_name],
            capture_output=True, text=True,
        )
        return (proc.stdout + proc.stderr).strip() or "(empty)"
