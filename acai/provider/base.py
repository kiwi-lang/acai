"""LLM client interface and shared helpers.

All agents talk to models through the :class:`LLM` interface.  Each
backend adapter (vLLM, OpenAI, Anthropic, ...) implements this.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Generator

import requests

log = logging.getLogger(__name__)


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
    """Normalise an endpoint to a bare origin (no /v1/... suffix)."""
    ep = endpoint.rstrip("/")
    if ep.endswith("/v1/chat/completions"):
        ep = ep[: -len("/v1/chat/completions")]
    elif ep.endswith("/v1"):
        ep = ep[: -len("/v1")]
    return ep


class LLMRequestError(RuntimeError):
    """Raised when an LLM API returns an error response.

    Carries a human-readable message that includes the status code
    and the provider's error body so callers can surface it directly.
    """


def _error_or_raise(resp: requests.Response) -> None:
    """Log the response body on error, then raise :class:`LLMRequestError`."""
    if resp.ok:
        return
    try:
        body = resp.json()
    except Exception:
        body = resp.text[:2000]
    log.error("LLM request failed  status=%s  body=%s", resp.status_code, body)
    if isinstance(body, dict):
        detail = body.get("error", {})
        if isinstance(detail, dict):
            msg = detail.get("message", str(body))
        else:
            msg = str(detail) or str(body)
    else:
        msg = str(body)
    raise LLMRequestError(f"Failed: {resp.status_code} — {msg}")


def _parse_openai_sse(
    resp: requests.Response,
    *,
    split_reasoning: bool = True,
) -> Generator[StreamEvent, None, None]:
    """Parse an OpenAI-format SSE stream into :class:`StreamEvent` objects.

    vLLM with ``--reasoning-parser`` (e.g. Qwen3) may put plain assistant text in
    ``delta.reasoning`` / ``reasoning_content`` while leaving ``content`` empty.
    When *split_reasoning* is false (native thinking disabled), those fields are
    treated as normal completion text so the UI does not label the whole reply
    as "Reasoning".
    """
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            break
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        if data.get("error"):
            err = data["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise LLMRequestError(f"OpenAI stream error: {msg}")

        try:
            delta = data.get("choices", [{}])[0].get("delta", {})
        except (IndexError, AttributeError):
            continue

        reasoning = delta.get("reasoning") or delta.get("reasoning_content") or ""
        content = delta.get("content") or ""

        if split_reasoning:
            if reasoning:
                yield ReasoningToken(text=reasoning)
            if content:
                yield ContentToken(text=content)
        else:
            if reasoning:
                yield ContentToken(text=reasoning)
            if content:
                yield ContentToken(text=content)

        for tc in delta.get("tool_calls", []):
            yield ToolCallDelta(
                index=tc.get("index", 0),
                id=tc.get("id"),
                name=tc.get("function", {}).get("name"),
                arguments=tc.get("function", {}).get("arguments"),
            )

    yield StreamDone()
