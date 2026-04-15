"""Dispatcher — sends work to workers and consumes SSE responses.

The orchestrator uses these functions to push work directly to a
worker's HTTP endpoints and consume the streamed results.  LLM calls
return an SSE stream consumed via :class:`AsyncSSEIterator`; tool calls
are plain JSON request/response.

All errors are returned as :class:`DispatchResult` with the ``error``
field set — no exceptions escape.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import aiohttp

from assai.orchestrator.iterator import AsyncSSEIterator

if TYPE_CHECKING:
    from assai.orchestrator.stream import StreamTracker

log = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """Accumulated result of a single dispatch to a worker."""

    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None


# Backward-compatible alias
StepResult = DispatchResult


async def dispatch_llm(
    worker_url: str,
    payload: dict,
    *,
    stream_id: str = "",
    tracker: StreamTracker | None = None,
    stream_mode: str = "token",
) -> StepResult:
    """POST an LLM completion request to a worker and consume the SSE stream.

    Parameters
    ----------
    worker_url : str
        Base URL of the worker (e.g. ``http://host:port/worker``).
    payload : dict
        Full LLM payload (messages, tools, task_id, …).
    stream_id : str
        Stream id for the ``StreamTracker`` (usually the root task id).
    tracker : StreamTracker | None
        If provided, events are pushed to the tracker in real time.
    stream_mode : str
        How to forward events to the tracker:
        ``"token"`` (default), ``"reasoning"``, ``"silent"``, ``"tool"``.

    Returns
    -------
    StepResult
        Accumulated text, reasoning, tool calls, or error.
    """
    url = f"{worker_url}/llm/complete"
    task_id = payload.get("task_id", "")
    text = ""
    reasoning = ""
    tool_calls: dict[int, dict] = {}

    try:
        async for event in AsyncSSEIterator(url, json=payload):
            etype = event.event
            try:
                edata = event.json()
            except (json.JSONDecodeError, ValueError):
                edata = {}

            if etype == "token":
                token = edata.get("token", "")
                text += token
                if tracker and stream_mode == "token":
                    tracker.push(stream_id, {"event_type": "token", "data": edata})
                elif tracker and stream_mode == "reasoning":
                    tracker.push(stream_id, {"event_type": "reasoning", "data": edata})

            elif etype == "reasoning":
                token = edata.get("token", "")
                reasoning += token
                if tracker and stream_mode != "silent":
                    tracker.push(stream_id, {"event_type": "reasoning", "data": edata})

            elif etype == "tool_call_delta":
                idx = edata.get("index", 0)
                tc_id = edata.get("id")
                tc_name = edata.get("name")
                tc_args = edata.get("arguments")

                if idx not in tool_calls:
                    tool_calls[idx] = {
                        "id": tc_id or "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                entry = tool_calls[idx]
                if tc_id:
                    entry["id"] = tc_id
                if tc_name:
                    entry["function"]["name"] = tc_name
                if tc_args:
                    entry["function"]["arguments"] += tc_args

            elif etype == "done":
                log.info("[%s] dispatch_llm done  chars=%d", task_id, len(text))

            elif etype == "error":
                error_msg = edata.get("error", "unknown worker error")
                log.error("[%s] dispatch_llm error: %s", task_id, error_msg)
                if tracker and stream_mode != "silent":
                    tracker.push(stream_id, {"event_type": "error", "data": edata})
                return StepResult(error=error_msg)

            else:
                if tracker and stream_mode != "silent":
                    tracker.push(stream_id, {"event_type": etype, "data": edata})

    except aiohttp.ClientError as exc:
        error_msg = f"Worker connection error: {exc}"
        log.error("[%s] %s", task_id, error_msg)
        return StepResult(error=error_msg)
    except Exception as exc:
        error_msg = f"Dispatch error: {exc}"
        log.exception("[%s] %s", task_id, error_msg)
        return StepResult(error=error_msg)

    tool_calls_list = [tool_calls[i] for i in sorted(tool_calls)]
    return StepResult(text=text, reasoning=reasoning, tool_calls=tool_calls_list)


async def dispatch_tool(
    worker_url: str,
    tool_name: str,
    args: dict,
    *,
    context: dict | None = None,
    timeout: float = 300,
) -> StepResult:
    """POST a tool call to a worker and return the result.

    This is a plain JSON request/response (not SSE).

    Parameters
    ----------
    context : dict, optional
        Worker context dict forwarded to the tool endpoint so that
        tools like ``ui.toast`` can reach the orchestrator.

    Returns
    -------
    StepResult
        ``text`` holds the tool result string, or ``error`` is set.
    """
    url = f"{worker_url}/tools/call"
    payload: dict = {"tool": tool_name, "args": args}
    if context:
        payload["context"] = context
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                body = await resp.json()
                if resp.status >= 400:
                    error = body.get("error", f"HTTP {resp.status}")
                    log.error("tool %s failed: %s", tool_name, error)
                    return StepResult(error=error)
                result = body.get("result", "")
                log.info("tool %s done  chars=%d", tool_name, len(result))
                return StepResult(text=result)
    except aiohttp.ClientError as exc:
        error_msg = f"Tool dispatch error: {exc}"
        log.error("%s", error_msg)
        return StepResult(error=error_msg)
    except Exception as exc:
        error_msg = f"Tool dispatch error: {exc}"
        log.exception("%s", error_msg)
        return StepResult(error=error_msg)
