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

import json
import logging
import shlex
import subprocess
import time
from typing import TYPE_CHECKING, Generator

import requests

if TYPE_CHECKING:
    from assai.core.config import LLMConfig

log = logging.getLogger(__name__)

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

    def __init__(self, config: LLMConfig):
        self.config = config
        self.process: subprocess.Popen | None = None

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None

    @property
    def managed(self) -> bool:
        """True if this server manages a local process (not a remote endpoint)."""
        return self.config.backend not in ("openai",)

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the LLM server if not already running."""
        if self.is_running():
            return
        if not self.managed:
            return

        cmd = self._build_command()
        log.info("starting LLM server: %s", cmd)
        self.process = subprocess.Popen(
            shlex.split(cmd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("LLM server started (pid %d)", self.process.pid)
        self.wait_healthy()

    def stop(self, timeout: float = 30) -> None:
        """Terminate the LLM server to free GPU."""
        if self.process is None:
            return
        pid = self.process.pid
        log.info("stopping LLM server (pid %d)", pid)
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("LLM server did not exit in %ds, sending SIGKILL", timeout)
            self.process.kill()
            self.process.wait(timeout=5)
        self.process = None

    def wait_healthy(self, retries: int = 120, interval: float = 2.0) -> None:
        """Poll the LLM health endpoint until it responds."""
        url = f"{self.config.endpoint}/health"
        for i in range(retries):
            try:
                r = requests.get(url, timeout=5)
                if r.status_code < 500:
                    log.info("LLM server healthy after %d checks", i + 1)
                    return
            except requests.ConnectionError:
                pass
            time.sleep(interval)
        log.warning("LLM server did not become healthy after %d attempts", retries)

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
            "--port", str(cfg.server_port),
            "--enable-auto-tool-choice",
            "--tool-call-parser", parser,
            "--enable-prefix-caching",
            "--kv-cache-dtype", "fp8",
            "--max-num-seqs", "1",
        ]

        if "qwen3-coder" in cfg.model.lower().replace("/", "-"):
            slug = cfg.model.rsplit("/", 1)[-1].lower().replace("_", "-")
            parts += [
                "--served-model-name", slug,
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
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            api_key=config.api_key,
        )
    raise ValueError(f"Unknown LLM backend: {config.backend}")
