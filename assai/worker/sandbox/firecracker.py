"""Firecracker microVM sandbox backend.

Launches an ``assai mcp`` tool server inside a Firecracker microVM.
Firecracker provides hardware-level isolation via KVM with extremely
fast boot times (~125 ms), making it suitable for per-task sandboxing.

Requirements:
    - ``firecracker`` binary on PATH (or configured path).
    - ``/dev/kvm`` available (KVM-capable host).
    - A guest kernel (vmlinux) and root filesystem (ext4 image) that
      contain Python and the ``assai`` package.  The rootfs should
      have ``assai mcp`` as the init or service process.

The microVM gets:
    - A TAP network interface for host ↔ guest HTTP traffic.
    - The project worktree shared via a virtio block device (or 9p/virtiofs
      once Firecracker adds support — for now the rootfs must include the
      project files or they are synced in after boot).

Architecture::

    Host                          MicroVM (KVM)
    ┌─────────────┐               ┌──────────────────┐
    │ Orchestrator │─── HTTP ────▶│  assai mcp :9200  │
    │  TaskGraph   │  (TAP net)   │  /workspace       │
    └─────────────┘               └──────────────────┘
        │                              ▲
        └── firecracker API ───────────┘
            (Unix socket)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import time
from assai.worker.sandbox.base import Sandbox
from assai.orchestrator.config import SandboxConfig

log = logging.getLogger(__name__)

_DEFAULT_KERNEL = "/opt/firecracker/vmlinux"
_DEFAULT_ROOTFS = "/opt/firecracker/rootfs.ext4"
_DEFAULT_FIRECRACKER = "firecracker"


class FirecrackerSandbox(Sandbox):
    """Firecracker microVM sandbox.

    Each sandbox session gets its own microVM with a dedicated TAP
    interface and ephemeral API socket.

    Parameters
    ----------
    kernel:
        Path to the uncompressed guest kernel (vmlinux).
    rootfs:
        Path to the root filesystem image (ext4).  The image should
        contain the ``assai`` package and boot into a state where
        ``assai mcp --host 0.0.0.0 --port 9200`` is running.
    mcp_port:
        Port the tool server listens on inside the guest.
    vcpu_count:
        Number of virtual CPUs for the microVM.
    mem_size_mib:
        Guest memory in MiB.
    firecracker_bin:
        Path to the ``firecracker`` binary.
    """

    def __init__(
        self,
        kernel: str = _DEFAULT_KERNEL,
        rootfs: str = _DEFAULT_ROOTFS,
        mcp_port: int = 9200,
        vcpu_count: int = 2,
        mem_size_mib: int = 1024,
        firecracker_bin: str = _DEFAULT_FIRECRACKER,
    ):
        self.kernel = kernel
        self.rootfs = rootfs
        self.mcp_port = mcp_port
        self.vcpu_count = vcpu_count
        self.mem_size_mib = mem_size_mib
        self.firecracker_bin = firecracker_bin

        self._process: subprocess.Popen | None = None
        self._socket_path: str | None = None
        self._tap_name: str | None = None
        self._guest_ip: str | None = None
        self._workdir: str | None = None

    # ------------------------------------------------------------------
    # Sandbox interface
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def endpoint(self) -> str:
        if self._guest_ip is None:
            raise RuntimeError("firecracker sandbox not started")
        return f"http://{self._guest_ip}:{self.mcp_port}"

    def start(
        self,
        project_path: str,
        sandbox_config: SandboxConfig | None = None,
        session_id: str = "default",
        agent_name: str = "",
    ) -> None:
        if self.running:
            log.debug("firecracker sandbox already running")
            return

        fc = shutil.which(self.firecracker_bin)
        if fc is None:
            raise RuntimeError(
                f"firecracker binary not found: {self.firecracker_bin!r}. "
                "Install from https://github.com/firecracker-microvm/firecracker/releases"
            )
        if not os.path.exists("/dev/kvm"):
            raise RuntimeError(
                "/dev/kvm not available — Firecracker requires KVM. "
                "Ensure the host supports hardware virtualisation."
            )

        self._workdir = tempfile.mkdtemp(prefix=f"assai-fc-{session_id}-")
        self._socket_path = os.path.join(self._workdir, "firecracker.sock")
        self._tap_name = f"fctap-{session_id[:8]}"
        self._guest_ip = "172.16.0.2"
        host_ip = "172.16.0.1"
        mask_long = "255.255.255.0"

        # Apply sandbox_config overrides
        vcpus = self.vcpu_count
        mem = self.mem_size_mib
        if sandbox_config is not None:
            if sandbox_config.memory_limit:
                mem = _parse_memory_mib(sandbox_config.memory_limit)

        # -- networking: create TAP device ---
        self._setup_tap(self._tap_name, host_ip, mask_long)

        boot_args = (
            "console=ttyS0 reboot=k panic=1 pci=off "
            f"ip={self._guest_ip}::{host_ip}:{mask_long}::eth0:off"
        )

        # -- overlay rootfs so the original stays clean ---
        overlay_rootfs = os.path.join(self._workdir, "rootfs.ext4")
        subprocess.run(
            ["cp", "--reflink=auto", self.rootfs, overlay_rootfs],
            check=True, capture_output=True,
        )

        # -- start firecracker process ---
        log.info(
            "starting firecracker  kernel=%s  rootfs=%s  vcpu=%d  mem=%dMiB",
            self.kernel, overlay_rootfs, vcpus, mem,
        )
        self._process = subprocess.Popen(
            [fc, "--api-sock", self._socket_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._workdir,
        )

        self._wait_for_socket()

        # -- configure & boot via the API socket ---
        self._api_put("/boot-source", {
            "kernel_image_path": self.kernel,
            "boot_args": boot_args,
        })

        self._api_put("/drives/rootfs", {
            "drive_id": "rootfs",
            "path_on_host": overlay_rootfs,
            "is_root_device": True,
            "is_read_only": False,
        })

        self._api_put("/machine-config", {
            "vcpu_count": vcpus,
            "mem_size_mib": mem,
        })

        self._api_put("/network-interfaces/eth0", {
            "iface_id": "eth0",
            "guest_mac": "AA:FC:00:00:00:01",
            "host_dev_name": self._tap_name,
        })

        self._api_put("/actions", {"action_type": "InstanceStart"})
        log.info("firecracker microVM started (pid=%d)", self._process.pid)

        self._wait_healthy()

        if agent_name:
            self._configure_git_identity(agent_name)

        log.info(
            "firecracker sandbox ready  endpoint=%s  agent=%s",
            self.endpoint, agent_name or "(default)",
        )

    def stop(self) -> None:
        if self._process is not None:
            log.info("stopping firecracker sandbox (pid=%d)", self._process.pid)
            try:
                self._api_put("/actions", {"action_type": "SendCtrlAltDel"})
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
                self._process.wait(timeout=5)
            self._process = None

        self._teardown_tap()

        if self._workdir and os.path.isdir(self._workdir):
            import shutil as _shutil
            _shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None

        self._socket_path = None
        self._guest_ip = None

    # ------------------------------------------------------------------
    # TAP networking
    # ------------------------------------------------------------------

    def _setup_tap(self, tap_name: str, host_ip: str, mask: str) -> None:
        """Create a TAP device and assign it an IP for host ↔ guest traffic."""
        cmds = [
            ["ip", "tuntap", "add", "dev", tap_name, "mode", "tap"],
            ["ip", "addr", "add", f"{host_ip}/24", "dev", tap_name],
            ["ip", "link", "set", tap_name, "up"],
        ]
        for cmd in cmds:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                log.warning("TAP setup (%s) failed: %s", " ".join(cmd), proc.stderr.strip())

        # Enable IP forwarding for the tap
        subprocess.run(
            ["sysctl", "-w", "net.ipv4.ip_forward=1"],
            capture_output=True, text=True,
        )

    def _teardown_tap(self) -> None:
        if self._tap_name is None:
            return
        subprocess.run(
            ["ip", "link", "del", self._tap_name],
            capture_output=True, text=True,
        )
        self._tap_name = None

    # ------------------------------------------------------------------
    # Firecracker API helpers
    # ------------------------------------------------------------------

    def _wait_for_socket(self, timeout: float = 5.0) -> None:
        """Wait for the Firecracker API socket to appear."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(self._socket_path):
                return
            time.sleep(0.05)
        raise RuntimeError(
            f"Firecracker API socket did not appear at {self._socket_path}"
        )

    def _api_put(self, path: str, body: dict) -> dict:
        """Send a PUT request to the Firecracker API via Unix socket."""
        payload = json.dumps(body)
        request = (
            f"PUT {path} HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"\r\n"
            f"{payload}"
        )

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self._socket_path)
            sock.sendall(request.encode())
            sock.settimeout(10)
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\r\n\r\n" in response:
                    header, _, body_bytes = response.partition(b"\r\n\r\n")
                    status_line = header.split(b"\r\n")[0].decode()
                    if "204" in status_line or "200" in status_line:
                        break
                    if "4" in status_line[:15] or "5" in status_line[:15]:
                        break
        finally:
            sock.close()

        resp_text = response.decode(errors="replace")
        if "204" in resp_text[:30] or "200" in resp_text[:30]:
            log.debug("API PUT %s → OK", path)
            return {}

        log.error("API PUT %s failed:\n%s", path, resp_text[:500])
        raise RuntimeError(f"Firecracker API PUT {path} failed: {resp_text[:200]}")

    # ------------------------------------------------------------------
    # Health / git
    # ------------------------------------------------------------------

    def _wait_healthy(self, retries: int = 60, interval: float = 1.0) -> None:
        import requests

        url = f"{self.endpoint}/health"
        for i in range(retries):
            try:
                r = requests.get(url, timeout=2)
                if r.status_code == 200:
                    log.info("firecracker sandbox healthy after %d checks", i + 1)
                    return
            except (requests.ConnectionError, requests.Timeout):
                pass

            if not self.running:
                raise RuntimeError("Firecracker process exited before becoming healthy")

            time.sleep(interval)

        raise RuntimeError(
            f"Firecracker sandbox did not become healthy after {retries * interval:.0f}s"
        )

    def _configure_git_identity(self, agent_name: str) -> None:
        """Set git identity inside the guest via the tool server."""
        import requests

        email = f"{agent_name}@assai.localhost"
        try:
            for cmd in [
                f"git config --global user.name '{agent_name}'",
                f"git config --global user.email '{email}'",
            ]:
                resp = requests.post(
                    f"{self.endpoint}/tools/call",
                    json={"tool": "shell.run", "args": {"command": cmd}},
                    timeout=10,
                )
                resp.raise_for_status()
            log.info("firecracker git identity: %s <%s>", agent_name, email)
        except Exception as exc:
            log.warning("git config inside firecracker failed: %s", exc)


def _parse_memory_mib(limit: str) -> int:
    """Parse a memory string like ``"4G"`` or ``"512M"`` to MiB."""
    limit = limit.strip().upper()
    if limit.endswith("G"):
        return int(float(limit[:-1]) * 1024)
    if limit.endswith("M"):
        return int(float(limit[:-1]))
    try:
        return int(limit)
    except ValueError:
        return 1024
