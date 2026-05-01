"""Unified LLM interface with per-provider adapters.

All agents talk to models through the :class:`LLM` interface.  Each
backend (vLLM, OpenAI, Anthropic, …) has its own adapter that
translates the standard request into the provider's wire format.

``LLMServer`` manages a local server process (vLLM, llama.cpp, …).
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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generator

import requests

if TYPE_CHECKING:
    from acai.orchestrator.config import ProviderConfig


# ---------------------------------------------------------------------------
# Stream event types — yielded by LLM.stream()
# ---------------------------------------------------------------------------

@dataclass
class StreamEvent:
    """Base class for events yielded by ``LLM.stream()``."""

@dataclass
class ContentToken(StreamEvent):
    text: str

@dataclass
class ReasoningToken(StreamEvent):
    """A token from the model's reasoning/thinking chain."""
    text: str

@dataclass
class ToolCallDelta(StreamEvent):
    """A single incremental chunk for one tool call from the LLM."""
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str | None = None

@dataclass
class StreamDone(StreamEvent):
    """Signals the end of the LLM stream."""

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


# ---------------------------------------------------------------------------
# LLMServer — manages a local server process
# ---------------------------------------------------------------------------

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

    def start(self) -> None:
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


# ---------------------------------------------------------------------------
# LLM client interface
# ---------------------------------------------------------------------------

class LLM:
    """Base class — each provider adapter implements this."""

    def complete(self, messages: list[dict], **kwargs) -> str:
        raise NotImplementedError

    def complete_raw(self, messages: list[dict], tools: list[dict] | None = None,
                     **kwargs) -> dict:
        """Return the full assistant message dict (may contain tool_calls)."""
        raise NotImplementedError

    def stream(self, messages: list[dict], **kwargs) -> Generator[StreamEvent, None, None]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helpers shared across OpenAI-protocol adapters
# ---------------------------------------------------------------------------

def _strip_endpoint(endpoint: str) -> str:
    """Normalise an endpoint to a bare origin (no /v1/… suffix)."""
    ep = endpoint.rstrip("/")
    if ep.endswith("/v1/chat/completions"):
        ep = ep[: -len("/v1/chat/completions")]
    elif ep.endswith("/v1"):
        ep = ep[: -len("/v1")]
    return ep


def _parse_openai_sse(resp: requests.Response) -> Generator[StreamEvent, None, None]:
    """Parse an OpenAI-format SSE stream into :class:`StreamEvent` objects."""
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            break
        try:
            data = json.loads(data_str)
            delta = data.get("choices", [{}])[0].get("delta", {})

            reasoning = delta.get("reasoning") or delta.get("reasoning_content") or ""
            if reasoning:
                yield ReasoningToken(text=reasoning)

            content = delta.get("content", "")
            if content:
                yield ContentToken(text=content)

            for tc in delta.get("tool_calls", []):
                yield ToolCallDelta(
                    index=tc.get("index", 0),
                    id=tc.get("id"),
                    name=tc.get("function", {}).get("name"),
                    arguments=tc.get("function", {}).get("arguments"),
                )
        except (json.JSONDecodeError, IndexError):
            continue

    yield StreamDone()


def _error_or_raise(resp: requests.Response) -> None:
    """Log the response body on error, then raise."""
    if resp.ok:
        return
    try:
        body = resp.json()
    except Exception:
        body = resp.text[:2000]
    log.error("LLM request failed  status=%s  body=%s", resp.status_code, body)
    resp.raise_for_status()




# ===================================================================
# Provider adapters
# ===================================================================

class VLLMAdapter(LLM):
    """Adapter for vLLM and other local OpenAI-compatible servers.

    Supports vLLM extensions: ``chat_template_kwargs`` for thinking
    control, Qwen3 thinking prefix hack.
    """

    def __init__(self, config: ProviderConfig):
        self.endpoint = _strip_endpoint(config.endpoint)
        self.model = config.slug
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature
        self.api_key = config.api_key
        self.timeout = 300

    def _url(self) -> str:
        return f"{self.endpoint}/v1/chat/completions"

    def _headers(self) -> dict:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _prepare_messages(self, messages: list[dict], **kwargs) -> list[dict]:
        """Apply Qwen3 thinking prefix hack if needed."""
        enable_thinking = kwargs.get("enable_thinking")
        if enable_thinking is None or not messages:
            return messages
        msgs = [dict(m) for m in messages]
        if enable_thinking:
            prefix = "<think>\n"
            suffix = "\nI have to give the solution based on the reasoning directly now."
        else:
            prefix = "</think>\n"
            suffix = ""
        for msg in reversed(msgs):
            if msg.get("role") == "user":
                msg["content"] = prefix + (msg.get("content") or "") + suffix
                break
        return msgs

    def _payload(self, messages: list[dict], stream: bool = False, **kwargs) -> dict:
        payload: dict = {
            "model": kwargs.get("model", self.model),
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": stream,
        }
        enable_thinking = kwargs.get("enable_thinking")
        if enable_thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
        if kwargs.get("response_format"):
            payload["response_format"] = kwargs["response_format"]
        if kwargs.get("tools"):
            payload["tools"] = kwargs["tools"]
        return payload

    def complete(self, messages, **kwargs):
        msgs = self._prepare_messages(messages, **kwargs)
        payload = self._payload(msgs, stream=False, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def complete_raw(self, messages, tools=None, **kwargs):
        msgs = self._prepare_messages(messages, **kwargs)
        if tools:
            kwargs["tools"] = tools
        payload = self._payload(msgs, stream=False, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

    def stream(self, messages, **kwargs):
        msgs = self._prepare_messages(messages, **kwargs)
        payload = self._payload(msgs, stream=True, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), stream=True,
                             timeout=self.timeout)
        _error_or_raise(resp)
        yield from _parse_openai_sse(resp)


class OpenAIAdapter(LLM):
    """Adapter for the OpenAI API.

    Key differences from vLLM:
    - Uses ``max_completion_tokens`` instead of ``max_tokens``
    - No ``chat_template_kwargs``
    - No thinking prefix hack
    """

    def __init__(self, config: ProviderConfig):
        self.endpoint = _strip_endpoint(config.endpoint)
        self.model = config.slug or config.model
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature
        self.api_key = config.api_key
        self.timeout = 300

    def _url(self) -> str:
        return f"{self.endpoint}/v1/chat/completions"

    def _headers(self) -> dict:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _payload(self, messages: list[dict], stream: bool = False, **kwargs) -> dict:
        payload: dict = {
            "model": kwargs.get("model", self.model),
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_completion_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if kwargs.get("response_format"):
            payload["response_format"] = kwargs["response_format"]
        if kwargs.get("tools"):
            payload["tools"] = kwargs["tools"]
        return payload

    def complete(self, messages, **kwargs):
        payload = self._payload(messages, stream=False, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), timeout=self.timeout)
        _error_or_raise(resp)
        return resp.json()["choices"][0]["message"]["content"]

    def complete_raw(self, messages, tools=None, **kwargs):
        if tools:
            kwargs["tools"] = tools
        payload = self._payload(messages, stream=False, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), timeout=self.timeout)
        _error_or_raise(resp)
        return resp.json()["choices"][0]["message"]

    def stream(self, messages, **kwargs):
        payload = self._payload(messages, stream=True, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), stream=True,
                             timeout=self.timeout)
        _error_or_raise(resp)
        yield from _parse_openai_sse(resp)


class AnthropicAdapter(LLM):
    """Adapter for the Anthropic Messages API.

    Translates the standard OpenAI-style messages list into
    Anthropic's wire format and parses the SSE stream back into
    the common :class:`StreamEvent` types.
    """

    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, config: ProviderConfig):
        ep = config.endpoint.rstrip("/")
        if ep.endswith("/v1/messages"):
            ep = ep[: -len("/v1/messages")]
        elif ep.endswith("/v1"):
            ep = ep[: -len("/v1")]
        self.endpoint = ep
        self.model = config.slug or config.model
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature
        self.api_key = config.api_key
        self.timeout = 300

    def _url(self) -> str:
        return f"{self.endpoint}/v1/messages"

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
        }

    @staticmethod
    def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
        """Extract system messages into a single string; return the rest."""
        system_parts: list[str] = []
        rest: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
            else:
                rest.append(m)
        return "\n\n".join(system_parts), rest

    def _convert_messages(self, messages: list[dict]) -> list[dict]:
        """Convert OpenAI-style messages to Anthropic format.

        Merges consecutive same-role messages (Anthropic requires
        strict alternation) and maps tool_calls / tool results.
        """
        out: list[dict] = []
        for m in messages:
            role = m.get("role", "user")
            if role == "system":
                continue
            if role == "tool":
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    }],
                })
                continue
            if role == "assistant" and m.get("tool_calls"):
                content: list[dict] = []
                if m.get("content"):
                    content.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    content.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": args,
                    })
                out.append({"role": "assistant", "content": content})
                continue

            out.append({"role": role, "content": m.get("content", "")})

        # Merge consecutive same-role messages
        merged: list[dict] = []
        for msg in out:
            if merged and merged[-1]["role"] == msg["role"]:
                prev = merged[-1]["content"]
                cur = msg["content"]
                if isinstance(prev, str) and isinstance(cur, str):
                    merged[-1]["content"] = prev + "\n\n" + cur
                elif isinstance(prev, list) and isinstance(cur, list):
                    merged[-1]["content"] = prev + cur
                elif isinstance(prev, str) and isinstance(cur, list):
                    merged[-1]["content"] = [{"type": "text", "text": prev}] + cur
                elif isinstance(prev, list) and isinstance(cur, str):
                    merged[-1]["content"] = prev + [{"type": "text", "text": cur}]
            else:
                merged.append(msg)
        return merged

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        """Convert OpenAI tool definitions to Anthropic format."""
        out: list[dict] = []
        for t in tools:
            fn = t.get("function", {})
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return out

    def _payload(self, messages: list[dict], stream: bool = False, **kwargs) -> dict:
        system_text, user_msgs = self._split_system(messages)
        converted = self._convert_messages(user_msgs)
        payload: dict = {
            "model": kwargs.get("model", self.model),
            "messages": converted,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "stream": stream,
        }
        if system_text:
            payload["system"] = system_text
        enable_thinking = kwargs.get("enable_thinking")
        if enable_thinking:
            payload["thinking"] = {"type": "enabled", "budget_tokens": self.max_tokens}
        tools = kwargs.get("tools")
        if tools:
            payload["tools"] = self._convert_tools(tools)
        return payload

    def complete(self, messages, **kwargs):
        payload = self._payload(messages, stream=False, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), timeout=self.timeout)
        _error_or_raise(resp)
        data = resp.json()
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "")
        return ""

    def complete_raw(self, messages, tools=None, **kwargs):
        if tools:
            kwargs["tools"] = tools
        payload = self._payload(messages, stream=False, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), timeout=self.timeout)
        _error_or_raise(resp)
        return self._to_openai_message(resp.json())

    @staticmethod
    def _to_openai_message(data: dict) -> dict:
        """Convert an Anthropic response to an OpenAI-style message dict."""
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in data.get("content", []):
            btype = block.get("type", "")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
        msg: dict = {"role": "assistant", "content": "\n".join(text_parts) or None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg

    def stream(self, messages, **kwargs):
        payload = self._payload(messages, stream=True, **kwargs)
        resp = requests.post(self._url(), json=payload,
                             headers=self._headers(), stream=True,
                             timeout=self.timeout)
        _error_or_raise(resp)
        yield from self._parse_anthropic_sse(resp)

    def _parse_anthropic_sse(self, resp: requests.Response) -> Generator[StreamEvent, None, None]:
        current_tool_id = ""
        current_tool_name = ""
        tool_index = 0

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use":
                    current_tool_id = block.get("id", "")
                    current_tool_name = block.get("name", "")
                    yield ToolCallDelta(
                        index=tool_index,
                        id=current_tool_id,
                        name=current_tool_name,
                        arguments="",
                    )

            elif etype == "content_block_delta":
                delta = event.get("delta", {})
                dtype = delta.get("type", "")
                if dtype == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        yield ContentToken(text=text)
                elif dtype == "thinking_delta":
                    thinking = delta.get("thinking", "")
                    if thinking:
                        yield ReasoningToken(text=thinking)
                elif dtype == "input_json_delta":
                    partial = delta.get("partial_json", "")
                    if partial:
                        yield ToolCallDelta(
                            index=tool_index,
                            id=None,
                            name=None,
                            arguments=partial,
                        )

            elif etype == "content_block_stop":
                if current_tool_name:
                    tool_index += 1
                    current_tool_id = ""
                    current_tool_name = ""

            elif etype == "message_stop":
                break

        yield StreamDone()


# ---------------------------------------------------------------------------
# Backward-compatible alias
# ---------------------------------------------------------------------------
OpenAICompatibleLLM = VLLMAdapter


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_ADAPTER_MAP: dict[str, type[LLM]] = {
    "vllm": VLLMAdapter,
    "llamacpp": VLLMAdapter,
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
}


def create_llm(config: ProviderConfig) -> LLM:
    """Build the right LLM adapter for the given provider config."""
    cls = _ADAPTER_MAP.get(config.backend, VLLMAdapter)
    return cls(config)
