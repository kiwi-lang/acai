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

Concrete subclasses live in ``acai/tasks/``:

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

from acai.utils.audit import AuditTrail, NullAuditTrail

if TYPE_CHECKING:
    from acai.orchestrator.agent_store import AgentDef, AgentStore
    from acai.orchestrator.chat import ChatStore
    from acai.orchestrator.config import AcaiConfig
    from acai.orchestrator.load_balancer import WorkerInfo
    from acai.orchestrator.projects import ProjectStore
    from acai.orchestrator.stream import StreamTracker

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
    building blocks.  See ``acai.tasks`` for concrete implementations.
    """

    def __init__(
        self,
        worker: WorkerInfo,
        *,
        agent_store: AgentStore,
        chat: ChatStore,
        config: AcaiConfig,
        tracker: StreamTracker | None = None,
        projects: ProjectStore | None = None,
        tool_registry: Any = None,
        audit: AuditTrail | NullAuditTrail | None = None,
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
        self.audit = audit or NullAuditTrail()
        self.stream_id = stream_id
        self.conversation = conversation
        self._allowed_tools: set[str] | None = None
        self._last_work: dict | None = None
        self._agent_uses_sandbox: bool = False
        self._agent_scope: str = "global"
        self._scope_context: dict = {}
        self._workflow_dir: str | None = None
        self._last_agent_dir: str | None = None

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
        instance = cls(worker, **kwargs)
        if work.get("workflow_dir"):
            instance._workflow_dir = work["workflow_dir"]
        return instance

    # ------------------------------------------------------------------
    # Agent helpers
    # ------------------------------------------------------------------

    def agent(self, name: str) -> AgentDef | None:
        """Fetch an agent definition by name.

        When ``_workflow_dir`` is set, agents bundled inside the
        workflow's ``agents/`` directory take precedence over globally
        registered agents.
        """
        if self._workflow_dir:
            import os
            wf_agent_dir = os.path.join(self._workflow_dir, "agents", name)
            if os.path.isfile(os.path.join(wf_agent_dir, "definition.json")):
                local = self.agent_store._load_from(wf_agent_dir, builtin=False)
                if local is not None:
                    self._last_agent_dir = wf_agent_dir
                    return local
        self._last_agent_dir = None
        return self.agent_store.get(name)

    def _resolve_tools(self, agent_def: AgentDef) -> tuple[list[dict] | None, str]:
        """Resolve MCP tool schemas and a human-readable summary."""
        if not agent_def.tools or self.tool_registry is None:
            return None, ""

        allowed_perms = set(agent_def.tool_permissions) if agent_def.tool_permissions else None
        allowed_res = set(agent_def.resource_permissions) if agent_def.resource_permissions else None
        tool_defs = self.tool_registry.mcp_definitions(
            namespaces=agent_def.tools,
            allowed_permissions=allowed_perms,
            allowed_resources=allowed_res,
        )
        if not tool_defs:
            self._allowed_tools = set()
            return None, ""

        self._allowed_tools = {
            td["function"]["name"]
            for td in tool_defs
            if "function" in td and "name" in td["function"]
        }

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

        * ``extra_context`` — dict of additional variables passed to the
          Jinja2 template via :func:`hydrate_task`.
        """
        from acai.orchestrator.agent_store import hydrate_task, resolve_task

        with self.audit.span("prepare", phase="prepare", agent=agent_name):
            self._last_work = work
            agent_def = self.agent(agent_name) or self.agent("default")
            tool_defs, tools_desc = self._resolve_tools(agent_def)

            self._agent_uses_sandbox = bool(
                agent_def and agent_def.uses_sandbox
            )

            self._agent_scope = (agent_def.scope if agent_def else "global") or "global"
            self._scope_context = self._build_scope_context(work, kwargs)

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

            extra_context = kwargs.get("extra_context") or work.get("extra_context")

            wf_template_src = None
            if self._last_agent_dir:
                import os
                tpl_path = os.path.join(self._last_agent_dir, agent_def.system_template)
                if os.path.isfile(tpl_path):
                    with open(tpl_path) as _f:
                        wf_template_src = _f.read()

            messages = hydrate_task(
                agent_def,
                self.agent_store,
                resolved,
                tools_description=tools_desc,
                extra_context=extra_context,
                template_src=wf_template_src,
            )

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
        import time as _time
        import aiohttp
        from acai.orchestrator.iterator import AsyncSSEIterator

        url = f"{self.worker.url}/llm/complete"

        self.audit.save_payload(f"prepare-{payload.get('agent', 'unknown')}", payload)

        _dispatch_t0 = _time.monotonic()
        _first_token_t: float | None = None
        _last_token_t: float | None = None
        _token_count = 0

        async with self.audit.aspan(
            "dispatch", phase="dispatch",
            worker=self.worker.url, stream_mode=stream_mode,
            agent=payload.get("agent", ""),
        ):
            try:
                async for event in AsyncSSEIterator(url, json=payload):
                    etype = event.event
                    try:
                        edata = event.json()
                    except (json.JSONDecodeError, ValueError):
                        edata = {}

                    if etype in ("token", "reasoning"):
                        _token_count += 1
                        now = _time.monotonic()
                        if _first_token_t is None:
                            _first_token_t = now
                        _last_token_t = now

                    if etype == "done":
                        if _token_count > 0:
                            ttft = round((_first_token_t - _dispatch_t0) * 1000, 2)
                            gen = round((_last_token_t - _dispatch_t0) * 1000, 2)
                            itl = round(
                                gen / max(_token_count - 1, 1), 2,
                            )
                            self.audit.record(
                                "dispatch.tokens", phase="dispatch",
                                ttft_ms=ttft,
                                token_count=_token_count,
                                itl_ms=itl,
                                generation_ms=gen,
                            )
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
        """Dispatch a tool call to the worker and return the result text.

        The worker handles sandbox proxying internally — the
        orchestrator always sends every tool call to the same worker
        endpoint.  Sandbox configuration is passed inside the
        ``context`` dict so the worker can start a sandbox lazily.

        The ``project_path`` is included in context so the sandbox
        proxy can scope each sandbox to a single project.
        """
        if self._allowed_tools is not None and tool_name not in self._allowed_tools:
            log.warning("blocked disallowed tool call: %s", tool_name)
            return (
                f"[Tool error] Tool '{tool_name}' is not permitted for this agent. "
                "Check the agent's tool namespaces and permissions."
            )

        scope_err = self._check_scope(tool_name, args)
        if scope_err:
            return scope_err

        from acai.orchestrator.dispatcher import dispatch_tool

        base_url = self.worker.url.rsplit("/worker", 1)[0]
        ctx: dict = {
            "conversation": self.conversation,
            "orchestrator_url": self.config.worker.orchestrator_url,
        }
        if self._agent_uses_sandbox:
            ctx["uses_sandbox"] = True

        project_path = self._resolve_project_path()
        if project_path:
            ctx["project_path"] = project_path

        result = await dispatch_tool(base_url, tool_name, args, context=ctx)
        if result.error:
            return f"[Tool error] {result.error}"
        return result.text or ""

    def _resolve_project_path(self) -> str:
        """Resolve the project's filesystem path from the work dict."""
        if self._last_work is None:
            return ""
        project_name = self._last_work.get("project", "")
        if not project_name:
            return ""
        if self.projects is not None:
            proj = self.projects.get(project_name)
            if proj is not None and proj.path:
                return proj.path
        return ""

    def _build_scope_context(self, work: dict, kwargs: dict) -> dict:
        """Extract scope-binding identifiers from the work context.

        When the agent has ``scope="project"``, this dict is used by
        ``dispatch_tool`` to validate that tool arguments stay within
        the current execution boundary.
        """
        import os

        ctx: dict = {}

        wf_dir = work.get("workflow_dir", "")
        if wf_dir:
            ctx["workflow_id"] = os.path.basename(wf_dir)

        extra = kwargs.get("extra_context") or work.get("extra_context") or {}
        if isinstance(extra, dict) and "workflow_id" in extra:
            ctx["workflow_id"] = extra["workflow_id"]

        if "workflow_id" in work:
            ctx["workflow_id"] = work["workflow_id"]

        project = work.get("project", "")
        if project:
            ctx["project"] = project

        return ctx

    def _check_scope(self, tool_name: str, args: dict) -> str:
        """Validate tool arguments against the agent's scope context.

        Returns an error string if the call is blocked, empty string
        otherwise.
        """
        if self._agent_scope != "project" or not self._scope_context:
            return ""
        if self.tool_registry is None:
            return ""
        td = self.tool_registry.get(tool_name)
        if td is None or not td.scope_key:
            return ""

        expected = self._scope_context.get(td.scope_key)
        if not expected:
            return ""

        actual = args.get(td.scope_key)
        if actual and actual != expected:
            log.warning(
                "scope violation: %s expected %s=%s, got %s",
                tool_name, td.scope_key, expected, actual,
            )
            return (
                f"[Scope error] Tool '{tool_name}' is scoped to the current project. "
                f"Expected {td.scope_key}='{expected}', got '{actual}'."
            )
        return ""

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

        self.audit.record(
            "response.saved", phase="response",
            text_length=len(acc.text),
            reasoning_length=len(acc.reasoning),
        )
        self.audit.save_payload("response", {
            "text": acc.text,
            "reasoning": acc.reasoning,
        })

    async def _run_with_tools(self, payload: dict) -> AsyncIterator[dict]:
        """Dispatch a payload and handle the tool-call follow-up loop.

        Yields SSE event dicts.  After iteration the final ``Acc`` is
        available as ``self._last_acc`` for callers that need the text.
        Does NOT yield ``done`` — the caller decides when the graph ends.
        """
        _tool_round = 0
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

                async with self.audit.aspan(
                    "tool", phase="tool",
                    tool=tool_name, args=tool_args,
                    tool_round=_tool_round,
                ):
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
                        "content": result_text,
                        "name": tool_name,
                    })

                end_ev = {
                    "event_type": "tool_end",
                    "data": {
                        "conversation": self.conversation,
                        "tool_name": tool_name,
                        "result_preview": result_text[:2000],
                    },
                }
                if self.tracker and self.stream_id:
                    self.tracker.push(self.stream_id, end_ev)
                yield end_ev

            followup_payload = dict(payload)
            followup_payload["messages"] = followup
            payload = followup_payload
            _tool_round += 1

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
