"""Unified LLM interface for agent use.

All agents talk to models through this abstraction.  The default
implementation speaks the OpenAI-compatible ``/v1/chat/completions``
protocol, which covers OpenAI, llama.cpp, vLLM, and any compatible
endpoint.

``LLMServer`` manages a local server process (vLLM, llama.cpp, …) on
the GPU.  It auto-generates the serve command from the config when
``server_command`` is empty.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import signal
import shlex
import subprocess
import time
from typing import TYPE_CHECKING, Generator

import requests

if TYPE_CHECKING:
    from assai.core.config import LLMConfig

log = logging.getLogger(__name__)


def _pid_alive(pid: int) -> bool:
    """Check whether a process with *pid* is still alive."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_tree(pid: int, sig: int = signal.SIGTERM) -> None:
    """Send *sig* to the process group rooted at *pid*.

    Falls back to killing just the pid if the pgid doesn't match.
    """
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


# ---------------------------------------------------------------------------
# Tool-call parser heuristics
# ---------------------------------------------------------------------------

_MODEL_PARSER_MAP: list[tuple[str, str]] = [
    ("qwen3-coder", "qwen3_xml"),
    ("qwen3_coder", "qwen3_xml"),
    ("qwen2.5", "hermes"),
    ("qwq", "hermes"),
    ("llama-4", "llama4_pythonic"),
    ("llama-3", "llama3_json"),
    ("mistral", "mistral"),
    ("deepseek-v3", "deepseek_v3"),
    ("deepseek-r1", "deepseek_v3"),
    ("granite-4", "granite4"),
    ("granite-3", "granite"),
    ("hermes", "hermes"),
]


def _guess_tool_parser(model: str) -> str:
    lower = model.lower().replace("/", "-").replace("_", "-")
    for pattern, parser in _MODEL_PARSER_MAP:
        if pattern in lower:
            return parser
    return "hermes"


# ---------------------------------------------------------------------------
# LLMServer — manages a local server process
# ---------------------------------------------------------------------------

class LLMServer:
    """Manages the lifecycle of a local LLM server process (vLLM, llama.cpp, …).

    When ``server_command`` is empty in the config, the command is
    auto-generated from ``backend`` and ``model``.  For ``backend=openai``
    no process is managed (remote endpoint).
    """

    def __init__(self, config: LLMConfig, workspace: str = "workspace"):
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
        """True if this server manages a local process (not a remote endpoint)."""
        return self.config.backend not in ("openai",)

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
        """Return the path to the most recent LLM server log file."""
        if self._current_log_path and os.path.isfile(self._current_log_path):
            return self._current_log_path
        pattern = os.path.join(self._log_dir, "llm_server_*.log")
        files = sorted(glob.glob(pattern))
        return files[-1] if files else None

    def read_log(self, tail: int = 200) -> str:
        """Read the last *tail* lines of the latest log file."""
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
        """If a lock file exists with a live process, kill it and clean up."""
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

    def start(self) -> None:
        """Start the LLM server if not already running.

        Raises :class:`LLMServerError` if the process crashes during
        startup (e.g. OOM, bad arguments).
        """
        if self.is_running():
            log.info("LLM server already running (pid %s)", self.pid or self._read_lock())
            return
        if not self.managed:
            return

        self._kill_stale_lock()

        from assai.core.env import build_env

        cmd = self._build_command()
        env = build_env()

        os.makedirs(self._log_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(self._log_dir, f"llm_server_{ts}.log")
        self._current_log_path = log_path
        self._log_file = open(log_path, "w")

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
        self.wait_healthy()

    def stop(self, timeout: float = 30) -> None:
        """Terminate the LLM server and all its child processes."""
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
        """Raise if the subprocess has exited."""
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
        """Tail the log file and wait for checkpoint loading to finish.

        Logs progress like ``Loading shards: 25% | 10/40`` so the user
        can see what's happening instead of silence during the 5-min load.
        """
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

    def wait_healthy(self, retries: int = 120, interval: float = 2.0) -> None:
        """Wait for the LLM server to become healthy.

        Phase 1 — monitor the log for checkpoint loading progress.
        Phase 2 — poll ``/health`` once loading is done.

        Raises :class:`LLMServerError` if the process exits or never
        becomes healthy.
        """
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

    # ------------------------------------------------------------------
    # Command generation
    # ------------------------------------------------------------------

    def _build_command(self) -> str:
        cfg = self.config
        if cfg.server_command:
            return cfg.server_command

        if cfg.backend == "vllm":
            return self._vllm_command()
        if cfg.backend in ("llamacpp", "local"):
            return (
                f"llama-server -m {cfg.model} "
                f"--host 0.0.0.0 --port {cfg.server_port}"
            )
        return cfg.server_command or ""

    def _vllm_command(self) -> str:
        cfg = self.config
        parser = _guess_tool_parser(cfg.model)

        parts = [
            "vllm", "serve", shlex.quote(cfg.model),
            "--served-model-name", cfg.slug,
            "--port", str(cfg.server_port),
            "--enable-auto-tool-choice",
            "--tool-call-parser", parser,
            "--enable-prefix-caching",
            "--kv-cache-dtype", "fp8",
            "--max-num-seqs", "1",
        ]

        if "qwen3-coder" in cfg.model.lower().replace("/", "-"):
            parts += [
                "--max-model-len", "170000",
                "--gpu-memory-utilization", "0.90",
                "--attention-backend", "flashinfer",
            ]

        return " ".join(parts)


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

class LLM:
    """Base class — override ``complete`` / ``stream`` for new backends."""

    def complete(self, messages: list[dict], **kwargs) -> str:
        raise NotImplementedError

    def complete_raw(self, messages: list[dict], tools: list[dict] | None = None,
                     **kwargs) -> dict:
        """Return the full assistant message dict (may contain tool_calls)."""
        raise NotImplementedError

    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        raise NotImplementedError


class OpenAICompatibleLLM(LLM):
    """Talks to any OpenAI-compatible endpoint (OpenAI, llama.cpp, vLLM)."""

    def __init__(self, endpoint: str, model: str = "",
                 max_tokens: int = 4096, temperature: float = 0.7,
                 api_key: str = "", timeout: int = 300):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _payload(self, messages, stream=False, **kwargs):
        return {
            "model": kwargs.get("model", self.model),
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": stream,
        }

    def complete(self, messages, **kwargs):
        payload = self._payload(messages, stream=False, **kwargs)
        resp = requests.post(
            f"{self.endpoint}/v1/chat/completions",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def complete_raw(self, messages, tools=None, **kwargs):
        payload = self._payload(messages, stream=False, **kwargs)
        if tools:
            payload["tools"] = tools
        resp = requests.post(
            f"{self.endpoint}/v1/chat/completions",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

    def stream(self, messages, **kwargs):
        payload = self._payload(messages, stream=True, **kwargs)
        if kwargs.get("tools"):
            payload["tools"] = kwargs["tools"]
        resp = requests.post(
            f"{self.endpoint}/v1/chat/completions",
            json=payload,
            headers=self._headers(),
            stream=True,
            timeout=self.timeout,
        )
        resp.raise_for_status()

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                data = json.loads(data_str)
                content = (
                    data.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content", "")
                )
                if content:
                    yield content
            except (json.JSONDecodeError, IndexError):
                continue


def create_llm(config: LLMConfig) -> LLM:
    """Factory: build an LLM from an ``LLMConfig``."""
    if config.backend in ("openai", "llamacpp", "vllm", "local"):
        return OpenAICompatibleLLM(
            endpoint=config.endpoint,
            model=config.slug,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            api_key=config.api_key,
        )
    raise ValueError(f"Unknown LLM backend: {config.backend}")
