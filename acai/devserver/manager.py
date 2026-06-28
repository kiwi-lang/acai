"""Process manager for dev services.

Manages a set of named subprocesses with lifecycle control and log capture.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_RING_SIZE = 2000
STOP_TIMEOUT = 5.0


class ServiceStatus(str, Enum):
    stopped = "stopped"
    running = "running"
    crashed = "crashed"


@dataclass
class ServiceSpec:
    """Declarative specification for a managed service."""

    name: str
    command: str
    cwd: str = "."
    env: dict[str, str] = field(default_factory=dict)
    auto_start: bool = True


@dataclass
class _RunningService:
    """Runtime state for a spawned process."""

    spec: ServiceSpec
    process: subprocess.Popen | None = None
    status: ServiceStatus = ServiceStatus.stopped
    started_at: float | None = None
    exit_code: int | None = None
    _log: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_RING_SIZE))
    _reader_thread: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def uptime(self) -> float | None:
        if self.status == ServiceStatus.running and self.started_at:
            return time.time() - self.started_at
        return None


class ProcessManager:
    """Manages a collection of dev services as subprocesses."""

    def __init__(self, specs: list[ServiceSpec], ring_size: int = DEFAULT_RING_SIZE,
                 log_dir: str | None = None):
        self._ring_size = ring_size
        self._log_dir = log_dir
        self._services: dict[str, _RunningService] = {}

        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        for spec in specs:
            self._services[spec.name] = _RunningService(
                spec=spec,
                _log=deque(maxlen=ring_size),
            )

    @property
    def service_names(self) -> list[str]:
        return list(self._services.keys())

    def get_service(self, name: str) -> _RunningService | None:
        return self._services.get(name)

    def start(self, name: str) -> dict[str, Any]:
        svc = self._services.get(name)
        if svc is None:
            return {"error": f"unknown service: {name}"}

        with svc._lock:
            if svc.status == ServiceStatus.running and svc.process and svc.process.poll() is None:
                return {"error": f"{name} is already running (pid={svc.process.pid})"}

            spec = svc.spec
            env = {**os.environ, **spec.env}
            cwd = os.path.abspath(spec.cwd)

            log.info("starting service %s: %s (cwd=%s)", name, spec.command, cwd)

            try:
                proc = subprocess.Popen(
                    spec.command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    env=env,
                    start_new_session=True,
                )
            except Exception as exc:
                svc.status = ServiceStatus.crashed
                svc.exit_code = -1
                svc._log.append(f"[spawner] failed to start: {exc}")
                return {"error": str(exc)}

            svc.process = proc
            svc.status = ServiceStatus.running
            svc.started_at = time.time()
            svc.exit_code = None
            svc._log.append(f"[spawner] started pid={proc.pid}")

            reader = threading.Thread(
                target=self._read_output,
                args=(svc,),
                daemon=True,
                name=f"log-{name}",
            )
            svc._reader_thread = reader
            reader.start()

        return {"ok": True, "pid": proc.pid}

    def stop(self, name: str) -> dict[str, Any]:
        svc = self._services.get(name)
        if svc is None:
            return {"error": f"unknown service: {name}"}

        with svc._lock:
            if svc.process is None or svc.process.poll() is not None:
                svc.status = ServiceStatus.stopped
                return {"ok": True, "already_stopped": True}

            pid = svc.process.pid
            pgid = os.getpgid(pid)
            log.info("stopping service %s (pid=%d, pgid=%d)", name, pid, pgid)
            svc._log.append(f"[spawner] stopping (SIGTERM)...")

            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        deadline = time.time() + STOP_TIMEOUT
        while time.time() < deadline:
            if svc.process.poll() is not None:
                break
            time.sleep(0.1)

        with svc._lock:
            if svc.process.poll() is None:
                svc._log.append(f"[spawner] SIGKILL after {STOP_TIMEOUT}s timeout")
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                svc.process.wait(timeout=2)

            svc.exit_code = svc.process.returncode
            svc.status = ServiceStatus.stopped
            svc._log.append(f"[spawner] stopped (exit_code={svc.exit_code})")

        return {"ok": True, "exit_code": svc.exit_code}

    def restart(self, name: str) -> dict[str, Any]:
        self.stop(name)
        return self.start(name)

    def status(self, name: str) -> dict[str, Any] | None:
        svc = self._services.get(name)
        if svc is None:
            return None
        self._refresh_status(svc)
        return self._serialize_status(svc)

    def status_all(self) -> list[dict[str, Any]]:
        result = []
        for svc in self._services.values():
            self._refresh_status(svc)
            result.append(self._serialize_status(svc))
        return result

    def logs(self, name: str, tail: int = 100) -> list[str] | None:
        svc = self._services.get(name)
        if svc is None:
            return None
        lines = list(svc._log)
        if tail and tail < len(lines):
            lines = lines[-tail:]
        return lines

    def start_all(self) -> None:
        for name, svc in self._services.items():
            if svc.spec.auto_start:
                result = self.start(name)
                if "error" in result:
                    log.error("  [%s] FAILED: %s", name, result["error"])
                else:
                    # Brief pause to catch instant crashes
                    time.sleep(0.3)
                    self._refresh_status(svc)
                    if svc.status == ServiceStatus.crashed:
                        log.error("  [%s] crashed immediately (exit_code=%s)", name, svc.exit_code)
                        last_lines = list(svc._log)[-5:]
                        for line in last_lines:
                            log.error("  [%s]   %s", name, line)
                    else:
                        log.info("  [%s] started (pid=%s)", name, result.get("pid"))

    def stop_all(self) -> None:
        for name in self._services:
            self.stop(name)

    def _refresh_status(self, svc: _RunningService) -> None:
        if svc.process is not None and svc.status == ServiceStatus.running:
            rc = svc.process.poll()
            if rc is not None:
                svc.exit_code = rc
                svc.status = ServiceStatus.crashed if rc != 0 else ServiceStatus.stopped
                svc._log.append(
                    f"[spawner] process exited (code={rc})"
                )

    def _serialize_status(self, svc: _RunningService) -> dict[str, Any]:
        return {
            "name": svc.spec.name,
            "command": svc.spec.command,
            "cwd": svc.spec.cwd,
            "status": svc.status.value,
            "pid": svc.process.pid if svc.process and svc.status == ServiceStatus.running else None,
            "uptime": svc.uptime(),
            "exit_code": svc.exit_code,
            "auto_start": svc.spec.auto_start,
        }

    def _log_path(self, name: str) -> str | None:
        if not self._log_dir:
            return None
        return os.path.join(self._log_dir, f"{name}.log")

    def _read_output(self, svc: _RunningService) -> None:
        """Background reader that drains stdout into the ring buffer and log file."""
        proc = svc.process
        if proc is None or proc.stdout is None:
            return

        log_file = None
        log_path = self._log_path(svc.spec.name)
        if log_path:
            try:
                log_file = open(log_path, "a", encoding="utf-8")
            except OSError:
                pass

        try:
            for raw_line in iter(proc.stdout.readline, b""):
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                svc._log.append(line)
                if log_file:
                    log_file.write(line + "\n")
                    log_file.flush()
        except (ValueError, OSError):
            pass
        finally:
            if proc.stdout:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
            if log_file:
                try:
                    log_file.close()
                except OSError:
                    pass
