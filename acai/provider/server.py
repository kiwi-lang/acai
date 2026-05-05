"""LLMServer — manages the lifecycle of a local LLM server process."""

from __future__ import annotations

import glob
import logging
import os
import re
import signal
import shlex
import subprocess
import time
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from acai.provider.config import ProviderConfig

log = logging.getLogger(__name__)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_tree(pid: int, sig: int = signal.SIGTERM) -> None:
    try:
        pgid = os.getpgid(pid)
        if pgid == pid:
            os.killpg(pgid, sig)
            log.info("sent signal %d to process group %d", sig, pgid)
            return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.kill(pid, sig)
        log.info("sent signal %d to pid %d", sig, pid)
    except (ProcessLookupError, PermissionError):
        pass


class LLMServerError(RuntimeError):
    """Raised when the LLM server fails to start or crashes."""


class LLMServer:
    """Manages the lifecycle of a local LLM server process."""

    def __init__(self, config: ProviderConfig, workspace: str = "workspace"):
        self.config = config
        self.process: subprocess.Popen | None = None
        self._ws = os.path.abspath(workspace)
        self._log_dir = os.path.join(self._ws, "logs")
        self._lock_path = os.path.join(self._ws, "llm_server.lock")
        self._log_file = None
        self._current_log_path: str | None = None

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None

    @property
    def managed(self) -> bool:
        return self.config.managed

    def is_running(self) -> bool:
        if self.process is not None and self.process.poll() is None:
            return True
        if self.process is None and os.path.isfile(self._lock_path):
            other_pid = self._read_lock()
            if other_pid and _pid_alive(other_pid):
                return True
        return False

    # ------------------------------------------------------------------
    # Log access
    # ------------------------------------------------------------------

    def latest_log_path(self) -> str | None:
        if self._current_log_path and os.path.isfile(self._current_log_path):
            return self._current_log_path
        pattern = os.path.join(self._log_dir, "llm_server_*.log")
        files = sorted(glob.glob(pattern))
        return files[-1] if files else None

    def read_log(self, tail: int = 200) -> str:
        path = self.latest_log_path()
        if path is None:
            return "(no log file found)"
        try:
            with open(path, errors="replace") as f:
                lines = f.readlines()
            return "".join(lines[-tail:])
        except OSError as exc:
            return f"(error reading log: {exc})"

    # ------------------------------------------------------------------
    # Lock file
    # ------------------------------------------------------------------

    def _write_lock(self, pid: int) -> None:
        os.makedirs(os.path.dirname(self._lock_path), exist_ok=True)
        with open(self._lock_path, "w") as f:
            f.write(str(pid))
        log.debug("wrote lock file %s  pid=%d", self._lock_path, pid)

    def _read_lock(self) -> int | None:
        try:
            with open(self._lock_path) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def _clear_lock(self) -> None:
        try:
            os.remove(self._lock_path)
            log.debug("removed lock file %s", self._lock_path)
        except OSError:
            pass

    def _kill_stale_lock(self) -> None:
        other_pid = self._read_lock()
        if other_pid is None:
            return
        if _pid_alive(other_pid):
            log.warning(
                "stale LLM server detected (pid %d), killing before start",
                other_pid,
            )
            _kill_tree(other_pid, signal.SIGTERM)
            for _ in range(30):
                time.sleep(1)
                if not _pid_alive(other_pid):
                    break
            else:
                _kill_tree(other_pid, signal.SIGKILL)
                time.sleep(2)
        self._clear_lock()

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start_process(self) -> None:
        """Launch the server subprocess without waiting for health.

        Use :meth:`is_healthy` or :meth:`wait_healthy` afterwards.
        """
        if self.is_running():
            log.info("LLM server already running (pid %s)", self.pid or self._read_lock())
            return
        if not self.managed:
            return

        self._kill_stale_lock()

        from acai.orchestrator.env import build_env

        cmd = self.config.build_command()
        env = build_env()

        os.makedirs(self._log_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(self._log_dir, f"llm_server_{ts}.log")
        self._current_log_path = log_path
        self._log_file = open(log_path, "w", buffering=1)

        log.info("starting LLM server: %s", cmd)
        log.info("LLM server logs → %s", log_path)
        self.process = subprocess.Popen(
            shlex.split(cmd),
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        self._write_lock(self.process.pid)
        log.info("LLM server started (pid %d)", self.process.pid)

    def start(self) -> None:
        """Launch and block until the server is healthy."""
        self.start_process()
        self.wait_healthy()

    def stop(self, timeout: float = 30) -> None:
        if self.process is None:
            return
        pid = self.process.pid
        log.info("stopping LLM server (pid %d)", pid)

        _kill_tree(pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("LLM server did not exit in %ds, sending SIGKILL", timeout)
            _kill_tree(pid, signal.SIGKILL)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        self.process = None
        self._clear_lock()
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    _RE_SHARD_PROGRESS = re.compile(
        r"Loading safetensors checkpoint shards:\s*(\d+)%\s+Completed\s*\|\s*(\d+)/(\d+)"
    )
    _RE_WEIGHTS_DONE = re.compile(r"Loading weights took")

    def _check_alive(self) -> None:
        if self.process is not None and self.process.poll() is not None:
            rc = self.process.returncode
            self._clear_lock()
            tail = self.read_log(tail=50)
            msg = (
                f"LLM server process died during startup "
                f"(exit code {rc}). Last log lines:\n{tail}"
            )
            log.error(msg)
            self.process = None
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            raise LLMServerError(msg)

    def _wait_model_loaded(self, timeout: float = 1800) -> None:
        path = self._current_log_path
        if path is None:
            return

        log.info("waiting for model checkpoint loading ...")
        deadline = time.monotonic() + timeout
        last_pct = -1
        file_pos = 0

        while time.monotonic() < deadline:
            self._check_alive()

            try:
                with open(path, errors="replace") as f:
                    f.seek(file_pos)
                    new_data = f.read()
                    file_pos = f.tell()
            except OSError:
                time.sleep(2)
                continue

            if not new_data:
                time.sleep(2)
                continue

            for line in new_data.splitlines():
                m = self._RE_SHARD_PROGRESS.search(line)
                if m:
                    pct, done, total = int(m.group(1)), m.group(2), m.group(3)
                    if pct != last_pct:
                        log.info("loading shards: %3d%% | %s/%s", pct, done, total)
                        last_pct = pct
                    if pct >= 100:
                        log.info("checkpoint loading complete")
                        return

                if self._RE_WEIGHTS_DONE.search(line):
                    log.info("model weights loaded: %s", line.strip().split("INFO")[-1].strip() if "INFO" in line else line.strip())
                    return

            time.sleep(2)

        log.warning("timed out waiting for checkpoint loading after %.0fs", timeout)

    def is_healthy(self) -> bool:
        """Non-blocking check: return ``True`` if the health endpoint responds."""
        url = f"{self.config.endpoint}/health"
        try:
            r = requests.get(url, timeout=2)
            return r.status_code < 500
        except (requests.ConnectionError, requests.Timeout):
            return False

    def wait_healthy(self, retries: int = 120, interval: float = 2.0) -> None:
        self._wait_model_loaded()

        url = f"{self.config.endpoint}/health"
        log.info("checkpoint loaded, polling health endpoint %s ...", url)

        for i in range(retries):
            self._check_alive()

            try:
                r = requests.get(url, timeout=5)
                if r.status_code < 500:
                    log.info("LLM server healthy after %d health checks", i + 1)
                    return
            except requests.ConnectionError:
                pass
            time.sleep(interval)

        log.warning("LLM server did not become healthy after %d attempts", retries)
        raise LLMServerError(
            f"LLM server not healthy after checkpoint load + {retries * interval:.0f}s. "
            f"Check logs: {self._current_log_path}"
        )
