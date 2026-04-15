"""Task graph — composable multi-agent pipeline.

``TaskGraph`` is the base class for all agent execution graphs.
Subclasses implement ``run()`` to define the agent flow:

* ``prepare(agent_name, work)`` — render an agent's Jinja2 template
  into an LLM-ready payload.
* ``dispatch(payload)`` — stream the payload to a worker, yielding
  SSE event dicts as they arrive.
* ``_run_with_tools(payload)`` — dispatch + tool-call follow-up loop.
* ``run(work)`` — execute the full graph (subclasses must override).

``Acc`` wraps any async event stream, passing each event through
while accumulating text, reasoning, and tool-call results internally.

Concrete subclasses live in ``assai/tasks/``:

* ``ConverseGraph`` — single agent + tool loop (default conversation).
* ``ThinkGraph``    — thinker agent → reply agent + tool loop.

Usage in a FastAPI endpoint::

    async with lb.acquire() as worker:
        graph = ConverseGraph.from_work(worker, work,
            agent_store=store, chat=chat, config=cfg, ...)
        async for event in graph.run(work):
            yield sse_format(event)
"""

from __future__ import annotations

import json
import logging
import traceback as _tb
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, TYPE_CHECKING

if TYPE_CHECKING:
    from assai.orchestrator.agent_store import AgentDef, AgentStore
    from assai.orchestrator.chat import ChatStore
    from assai.orchestrator.config import AssaiConfig
    from assai.orchestrator.load_balancer import WorkerInfo
    from assai.orchestrator.projects import ProjectStore
    from assai.orchestrator.stream import StreamTracker

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Acc — pass-through stream accumulator
# ------------------------------------------------------------------

class Acc:
    """Accumulate a stream of token/reasoning events so we can use the results.

    Wraps an async iterable of SSE event dicts.  Each event is yielded
    through unchanged while the text, reasoning, and tool-call fields
    are accumulated internally.

    Usage::

        acc = Acc(graph.dispatch(payload))
        async for event in acc:
            yield event        # forward to client

        # after iteration, accumulated results are available:
        print(acc.text)
        print(acc.reasoning)
        print(acc.tool_calls)
    """

    def __init__(self, stream: AsyncIterator[dict]):
        self._stream = stream
        self.text: str = ""
        self.reasoning: str = ""
        self.tool_calls: list[dict] = []
        self._tc_buf: dict[int, dict] = {}

    async def __aiter__(self) -> AsyncIterator[dict]:
        async for event in self._stream:
            self._accumulate(event)
            yield event

    def _accumulate(self, event: dict) -> None:
        etype = event.get("event_type", "")
        data = event.get("data", {})

        if etype == "token":
            self.text += data.get("token", "")
        elif etype == "reasoning":
            self.reasoning += data.get("token", "")
        elif etype == "tool_call_delta":
            idx = data.get("index", 0)
            if idx not in self._tc_buf:
                self._tc_buf[idx] = {
                    "id": data.get("id", ""),
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                }
            entry = self._tc_buf[idx]
            if data.get("id"):
                entry["id"] = data["id"]
            if data.get("name"):
                entry["function"]["name"] = data["name"]
            if data.get("arguments"):
                entry["function"]["arguments"] += data["arguments"]
            self.tool_calls = [
                self._tc_buf[i] for i in sorted(self._tc_buf)
            ]


# ------------------------------------------------------------------
# TaskProxy — lightweight stand-in for a queue Task
# ------------------------------------------------------------------

@dataclass
class _TaskProxy:
    """Minimal task-like object accepted by ``resolve_task``."""
    id: str = ""
    kind: str = "converse"
    title: str = ""
    description: str = ""
    priority: int = 0
    project: str = ""
    agent: str = "default"
    gpu: int = 0
    parent_task: str = ""
    root_task: str = ""
    worktree: str = ""
    spec: str = ""
    spec_path: str = ""
    conversation: str = ""
    enable_thinking: bool | None = None


# ------------------------------------------------------------------
# TaskGraph
# ------------------------------------------------------------------

class TaskGraph:
    """Base class for multi-agent pipelines bound to a worker.

    Subclass and implement ``run()`` to define the agent flow.
    Use ``prepare()`` / ``dispatch()`` / ``_run_with_tools()`` as
    building blocks.  See ``assai.tasks`` for concrete implementations.
    """

    def __init__(
        self,
        worker: WorkerInfo,
        *,
        agent_store: AgentStore,
        chat: ChatStore,
        config: AssaiConfig,
        tracker: StreamTracker | None = None,
        projects: ProjectStore | None = None,
        tool_registry: Any = None,
        stream_id: str = "",
        conversation: str = "",
    ):
        self.worker = worker
        self.agent_store = agent_store
        self.chat = chat
        self.config = config
        self.tracker = tracker
        self.projects = projects
        self.tool_registry = tool_registry
        self.stream_id = stream_id
        self.conversation = conversation

    @classmethod
    def from_work(
        cls,
        worker: WorkerInfo,
        work: dict,
        **kwargs,
    ) -> TaskGraph:
        """Create a TaskGraph from a worker and a work dict."""
        kwargs.setdefault("conversation", work.get("conversation", ""))
        kwargs.setdefault("stream_id", work.get("stream_id", ""))
        return cls(worker, **kwargs)

    # ------------------------------------------------------------------
    # Agent helpers
    # ------------------------------------------------------------------

    def agent(self, name: str) -> AgentDef | None:
        """Fetch an agent definition by name."""
        return self.agent_store.get(name)

    def _resolve_tools(self, agent_def: AgentDef) -> tuple[list[dict] | None, str]:
        """Resolve MCP tool schemas and a human-readable summary."""
        if not agent_def.tools or self.tool_registry is None:
            return None, ""

        tool_defs = self.tool_registry.mcp_definitions(namespaces=agent_def.tools)
        if not tool_defs:
            return None, ""

        lines: list[str] = []
        for td in tool_defs:
            fn = td.get("function", {})
            params = fn.get("parameters", {}).get("properties", {})
            lines.append(f"- **{fn.get('name', '')}**: {fn.get('description', '')}")
            for k, v in params.items():
                lines.append(f"  - {k}: {v.get('description', v.get('type', ''))}")

        return tool_defs, "\n".join(lines)

    # ------------------------------------------------------------------
    # prepare — render an agent template into an LLM payload
    # ------------------------------------------------------------------

    def prepare(self, agent_name: str, work: dict, **kwargs) -> dict:
        """Prepare work using an agent — render its Jinja2 template.

        Returns a hydrated payload dict ready for ``dispatch()``.

        Extra keyword arguments:

        * ``reasoning`` — injected as a prior-reasoning system message.
        * ``extra_context`` — dict of additional variables passed to the
          Jinja2 template via :func:`hydrate_task`.
        """
        from assai.orchestrator.agent_store import hydrate_task, resolve_task

        agent_def = self.agent(agent_name) or self.agent("default")
        tool_defs, tools_desc = self._resolve_tools(agent_def)

        task_proxy = _TaskProxy(
            id=work.get("task_id", ""),
            kind=work.get("kind", "converse"),
            title=work.get("title", ""),
            project=work.get("project", ""),
            agent=agent_name,
            spec_path=work.get("spec_path", ""),
            conversation=self.conversation,
            enable_thinking=work.get("enable_thinking"),
        )
        resolved = resolve_task(task_proxy, self.config, self.chat, self.projects)

        extra_context = kwargs.get("extra_context")
        messages = hydrate_task(
            agent_def, 
            self.agent_store, 
            resolved,
            tools_description=tools_desc,
            extra_context=extra_context,
        )

        reasoning = kwargs.get("reasoning", "")
        if reasoning:
            msg = {
                "role": "system",
                "content": (
                    "## Prior Reasoning\n"
                    "The following analysis was produced about this task. "
                    "Use it to inform your response.\n\n"
                    + reasoning
                ),
            }
            pos = 1 if messages and messages[0].get("role") == "system" else 0
            messages.insert(pos, msg)

        payload: dict = {
            "task_id": work.get("task_id", ""),
            "kind": "llm_complete",
            "messages": messages,
            "conversation": self.conversation,
            "agent": agent_name,
        }
        if tool_defs:
            payload["tools"] = tool_defs
        if agent_def:
            payload["compressor"] = agent_def.compressor

        provider_info = work.get("provider_override")
        if provider_info:
            payload["provider"] = provider_info

        if task_proxy.enable_thinking is not None:
            payload["enable_thinking"] = task_proxy.enable_thinking

        return payload

    # ------------------------------------------------------------------
    # dispatch — stream a payload through the worker
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        payload: dict,
        *,
        stream_mode: str = "token",
    ) -> AsyncIterator[dict]:
        """Send a payload to the worker and yield SSE event dicts.

        Each yielded dict has ``event_type`` and ``data`` keys.
        The terminal ``done`` event from the worker is consumed but
        **not** yielded — use ``run()`` for graph-level completion.
        """
        import aiohttp
        from assai.orchestrator.iterator import AsyncSSEIterator

        url = f"{self.worker.url}/llm/complete"

        try:
            async for event in AsyncSSEIterator(url, json=payload):
                etype = event.event
                try:
                    edata = event.json()
                except (json.JSONDecodeError, ValueError):
                    edata = {}

                if etype == "done":
                    return

                if etype == "token" and stream_mode == "reasoning":
                    ev = {"event_type": "reasoning", "data": edata}
                else:
                    ev = {"event_type": etype, "data": edata}

                if self.tracker and self.stream_id and stream_mode != "silent":
                    self.tracker.push(self.stream_id, ev)

                yield ev

                if etype == "error":
                    return

        except aiohttp.ClientError as exc:
            log.error("dispatch worker error: %s", exc)
            ev = {"event_type": "error", "data": {
                "message": f"Worker connection error: {exc}",
                "traceback": _tb.format_exc(),
            }}
            if self.tracker and self.stream_id:
                self.tracker.push(self.stream_id, ev)
            yield ev
        except Exception as exc:
            log.exception("dispatch error")
            ev = {"event_type": "error", "data": {
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": _tb.format_exc(),
            }}
            if self.tracker and self.stream_id:
                self.tracker.push(self.stream_id, ev)
            yield ev

    # ------------------------------------------------------------------
    # dispatch_tool — single tool call via the worker
    # ------------------------------------------------------------------

    async def dispatch_tool(self, tool_name: str, args: dict) -> str:
        """Dispatch a tool call to the worker and return the result text."""
        from assai.orchestrator.dispatcher import dispatch_tool
        base_url = self.worker.url.rsplit("/worker", 1)[0]
        ctx: dict = {
            "conversation": self.conversation,
            "orchestrator_url": self.config.worker.orchestrator_url,
        }
        result = await dispatch_tool(base_url, tool_name, args, context=ctx)
        return result.text or ""

    # ------------------------------------------------------------------
    # Helpers shared by all graph subclasses
    # ------------------------------------------------------------------

    def _error_event(self, message: str, tb: str = "") -> dict:
        """Build an error event dict and push it to the tracker."""
        ev = {"event_type": "error", "data": {
            "message": message,
            **({"traceback": tb} if tb else {}),
        }}
        if self.tracker and self.stream_id:
            self.tracker.push(self.stream_id, ev)
        return ev

    def _done_event(self) -> dict:
        """Build a done event and push it to the tracker."""
        ev: dict = {"event_type": "done", "data": {}}
        if self.tracker and self.stream_id:
            self.tracker.push(self.stream_id, ev)
        return ev

    def _save_response(self, acc: Acc) -> None:
        """Persist the final assistant response to chat."""
        if acc.text and self.conversation:
            msg: dict = {"role": "assistant", "content": acc.text}
            if acc.reasoning:
                msg["reasoning"] = acc.reasoning
            self.chat.append(self.conversation, msg)

    async def _run_with_tools(self, payload: dict) -> AsyncIterator[dict]:
        """Dispatch a payload and handle the tool-call follow-up loop.

        Yields SSE event dicts.  After iteration the final ``Acc`` is
        available as ``self._last_acc`` for callers that need the text.
        Does NOT yield ``done`` — the caller decides when the graph ends.
        """
        acc = Acc(self.dispatch(payload))
        async for event in acc:
            yield event
            if event.get("event_type") == "error":
                self._last_acc = acc
                return

        while acc.tool_calls:
            followup = list(payload["messages"])
            followup.append({
                "role": "assistant",
                "content": acc.text or None,
                "tool_calls": acc.tool_calls,
            })

            for call in acc.tool_calls:
                fn = call.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    tool_args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                start_ev = {
                    "event_type": "tool_start",
                    "data": {
                        "conversation": self.conversation,
                        "tool_name": tool_name,
                        "args": tool_args,
                    },
                }
                if self.tracker and self.stream_id:
                    self.tracker.push(self.stream_id, start_ev)
                yield start_ev

                try:
                    result_text = await self.dispatch_tool(tool_name, tool_args)
                except Exception as exc:
                    log.exception("tool dispatch error: %s", tool_name)
                    result_text = f"[Tool error] {type(exc).__name__}: {exc}"

                followup.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result_text,
                })

                if self.conversation:
                    self.chat.append(self.conversation, {
                        "role": "tool_call",
                        "content": json.dumps(
                            {"tool": tool_name, "args": tool_args},
                            ensure_ascii=False,
                        ),
                        "name": tool_name,
                    })
                    self.chat.append(self.conversation, {
                        "role": "tool_result",
                        "content": result_text[:500],
                        "name": tool_name,
                    })

                end_ev = {
                    "event_type": "tool_end",
                    "data": {
                        "conversation": self.conversation,
                        "tool_name": tool_name,
                        "result_preview": result_text[:200],
                    },
                }
                if self.tracker and self.stream_id:
                    self.tracker.push(self.stream_id, end_ev)
                yield end_ev

            followup_payload = dict(payload)
            followup_payload["messages"] = followup
            payload = followup_payload

            acc = Acc(self.dispatch(followup_payload))
            async for event in acc:
                yield event
                if event.get("event_type") == "error":
                    self._last_acc = acc
                    return

        self._last_acc = acc

    # ------------------------------------------------------------------
    # run — abstract, subclasses must override
    # ------------------------------------------------------------------

    async def run(self, work: dict) -> AsyncIterator[dict]:
        """Execute the task graph.  Yields SSE event dicts.

        Subclasses must override this method.
        """
        raise NotImplementedError("Subclass TaskGraph and implement run()")
        yield  # type: ignore[misc]  # pragma: no cover
