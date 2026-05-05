"""Podman / Docker sandbox backend.

Starts a container running ``acai mcp`` and proxies tool calls to it.
The container CLI (``podman`` or ``docker``) is auto-detected at
construction time but can be overridden.

Rootless Podman
~~~~~~~~~~~~~~~

The default (and recommended) mode is **rootless Podman**.  This runs
containers without root privileges.  Key adaptations:

* ``--userns=keep-id`` maps the host UID/GID into the container so
  bind-mounted files are owned by the current user.
* Port resolution handles multi-line output (IPv4 + IPv6) from
  ``podman port``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time

from acai.worker.sandbox.base import Sandbox
from acai.orchestrator.config import SandboxConfig

log = logging.getLogger(__name__)


def _detect_runtime() -> str:
    """Return ``"podman"`` or ``"docker"`` depending on what is on PATH."""
    for candidate in ("podman", "docker"):
        if shutil.which(candidate):
            return candidate
    raise RuntimeError("Neither podman nor docker found on PATH")


def _is_rootless(runtime: str) -> bool:
    """Detect whether the runtime is running in rootless mode."""
    if runtime != "podman":
        return False
    try:
        proc = subprocess.run(
            [runtime, "info", "--format", "{{.Host.Security.Rootless}}"],
            capture_output=True, text=True, timeout=10,
        )
        return proc.stdout.strip().lower() == "true"
    except Exception:
        return os.getuid() != 0


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
    """Manages a Podman/Docker container running ``acai mcp``.

    The project worktree is bind-mounted at the **same absolute path**
    inside the container so that file paths are consistent between
    host and sandbox.  One container is started per *session_id* and
    reused until :meth:`stop` is called.

    When *rootless* is ``True`` (the default for Podman), the
    container uses ``--userns=keep-id`` so that files created inside
    the container are owned by the host user — no root required.
    """

    def __init__(
        self,
        image: str = "acai-sandbox",
        container_port: int = 9200,
        runtime: str | None = None,
        rootless: bool | None = None,
    ):
        self.container_port = container_port
        self.runtime = runtime or _detect_runtime()
        if rootless is None:
            self.rootless = _is_rootless(self.runtime)
        else:
            self.rootless = rootless
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
        name = f"acai-sandbox-{session_id}"

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

        self._configure_git_identity(agent_name or "acai-agent")
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
        looks for a ``Containerfile`` in the acai repo root and
        builds it with streaming output so long builds remain visible
        in the logs.
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
        build_proc = subprocess.Popen(
            build_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output_lines: list[str] = []
        assert build_proc.stdout is not None
        for line in build_proc.stdout:
            stripped = line.rstrip()
            output_lines.append(stripped)
            if stripped:
                log.info("sandbox build: %s", stripped)
        rc = build_proc.wait()
        if rc != 0:
            raise RuntimeError(
                f"Failed to build sandbox image {tag!r} "
                f"(rc={rc}):\n" + "\n".join(output_lines[-20:])
            )
        log.info("sandbox image %r built successfully", tag)

    def _build_run_cmd(
        self,
        name: str,
        project_path: str,
        sandbox_config: SandboxConfig | None,
    ) -> list[str]:
        abs_path = os.path.abspath(project_path)
        cmd = [
            self.runtime, "run", "-d",
            "--name", name,
            "-p", f":{self.container_port}",
            "-v", f"{abs_path}:{abs_path}",
            "-w", abs_path,
        ]

        if self.rootless:
            cmd += ["--userns=keep-id"]

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
        """Extract the host port from ``podman port`` / ``docker port``.

        Rootless Podman may return multiple lines (IPv4 + IPv6)::

            0.0.0.0:32768
            [::]:32768

        We take the first line and extract the port number.
        """
        proc = subprocess.run(
            [self.runtime, "port", name, str(self.container_port)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"failed to resolve port: {proc.stderr.strip()}")
        first_line = proc.stdout.strip().splitlines()[0]
        port_str = first_line.rsplit(":", 1)[-1]
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
        email = f"{agent_name}@acai.localhost"
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
