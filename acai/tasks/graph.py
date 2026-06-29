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
# Context limit enforcement
# ------------------------------------------------------------------

# Leave headroom for output tokens + model overhead + chat template
_HARD_LIMIT_RATIO = 0.85
_TRUNCATION_MARKER = "[Earlier messages were truncated to fit the context window]"

# Tool-loop safety limits
_MAX_TOOL_ROUNDS = 20
_MAX_TOOL_RESULT_CHARS = 50_000
_TOOL_RESULT_TRUNCATION_MSG = "\n...[truncated — result exceeded 50 KB]"

# Context budget warning threshold (emit event when usage exceeds this)
_CONTEXT_WARNING_RATIO = 0.70


def _estimate_tokens(messages: list[dict]) -> int:
    """Estimate token count using the model tokenizer (with char fallback).

    Prefers the accurate tokenizer from ``acai.utils.tokens`` when available.
    Falls back to conservative char/3.2 ratio otherwise.
    """
    try:
        from acai.utils.tokens import count_messages_tokens
        return count_messages_tokens(messages)
    except Exception:
        # Fallback: conservative char/3.2 (not 4, which underestimates code)
        total = 0
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                total += sum(len(p.get("text", "")) for p in c if isinstance(p, dict))
            for tc in m.get("tool_calls", []):
                fn = tc.get("function", {})
                total += len(fn.get("name", "")) + len(fn.get("arguments", ""))
        return int(total / 3.2)


def enforce_context_limit(
    messages: list[dict],
    context_window: int,
    max_tokens: int,
    *,
    keep_recent: int = 10,
) -> tuple[list[dict], bool]:
    """Truncate messages to fit within the context budget.

    Returns ``(messages, was_truncated)``.  Keeps the system message(s)
    at the start and the last *keep_recent* messages.  Drops the middle
    and inserts a truncation marker.
    """
    if not messages or context_window <= 0:
        return messages, False

    available = int((context_window - max_tokens) * _HARD_LIMIT_RATIO)
    estimated = _estimate_tokens(messages)

    if estimated <= available:
        return messages, False

    # Identify system messages at the start (there may be more than one)
    system_end = 0
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            system_end = i + 1
        else:
            break

    system_msgs = messages[:system_end]
    non_system = messages[system_end:]

    if len(non_system) <= keep_recent:
        return messages, False

    recent = non_system[-keep_recent:]
    system_tokens = _estimate_tokens(system_msgs)
    recent_tokens = _estimate_tokens(recent)

    if system_tokens + recent_tokens >= available:
        # Even recent messages are too large — progressively trim
        while len(recent) > 2 and _estimate_tokens(system_msgs) + _estimate_tokens(recent) >= available:
            recent = recent[1:]

    truncated = system_msgs + [
        {"role": "system", "content": _TRUNCATION_MARKER},
    ] + recent

    log.warning(
        "Context hard-truncated: ~%d tokens -> ~%d tokens "
        "(%d messages dropped, kept %d system + %d recent)",
        estimated, _estimate_tokens(truncated),
        len(non_system) - len(recent),
        len(system_msgs), len(recent),
    )
    return truncated, True


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
    prior_work: list = field(default_factory=list)


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
        input_queue: Any = None,
        task_runner: Any = None,
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
        self.input_queue = input_queue
        self.task_runner = task_runner
        self._ui_elements: list[dict] = []  # legacy — kept for done event compatibility
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
    # Model routing
    # ------------------------------------------------------------------

    def _route_model(self, agent_def: AgentDef, work: dict):
        """Use ModelRouter to pick a model from the agent's model set.

        Returns ``(ProviderConfig, ModelConfig, ModelSetEntry)`` or ``None``.
        """
        from acai.provider.router import ModelRouter

        set_name = work.get("model_set") or agent_def.model_set or ""
        complexity = work.get("complexity") or agent_def.complexity or "medium"

        model_set = (
            self.config.get_model_set(set_name) if set_name
            else self.config.default_model_set()
        )
        if model_set is None:
            return None

        remaining_budget = None
        if self.conversation:
            meta = self.chat.get_meta(self.conversation)
            if meta:
                budget = float(meta.get("budget", 0))
                if budget > 0:
                    remaining_budget = max(0.0, budget - float(meta.get("spent", 0)))

        router = ModelRouter(self.config.providers)
        return router.select(model_set, complexity=complexity, remaining_budget=remaining_budget)

    def _record_cost(self, payload: dict, done_data: dict) -> None:
        """Record the cost of a completed LLM call against the session budget."""
        entry_info = payload.get("_routed_entry")
        if not entry_info or not self.conversation:
            return

        output_tokens = done_data.get("output_tokens", 0)
        input_tokens = _estimate_tokens(payload.get("messages", []))

        price_input = entry_info.get("price_input", 0.0)
        price_output = entry_info.get("price_output", 0.0)
        cost = (
            (input_tokens * price_input) / 1_000_000
            + (output_tokens * price_output) / 1_000_000
        )
        if cost > 0:
            self.chat.record_spend(self.conversation, cost)
            self.audit.record(
                "dispatch.cost", phase="dispatch",
                cost_usd=round(cost, 8),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider=entry_info.get("provider", ""),
                model=entry_info.get("model", ""),
            )

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
                description=work.get("description", ""),
                worktree=work.get("worktree", ""),
                project=work.get("project", ""),
                agent=agent_name,
                spec_path=work.get("spec_path", ""),
                conversation=self.conversation,
                enable_thinking=work.get("enable_thinking"),
                prior_work=work.get("prior_work", []),
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

            # -- Enforce context limit to prevent OOM -----------------
            active_prov = self.config.active_provider()
            ctx_window = active_prov.context_window or 128000
            max_out = active_prov.max_tokens or 4096
            messages, was_truncated = enforce_context_limit(
                messages, ctx_window, max_out,
            )
            if was_truncated:
                self.audit.record(
                    "context.truncated", phase="prepare",
                    context_window=ctx_window,
                    estimated_tokens=_estimate_tokens(messages),
                )

            # Store context budget info for downstream use
            self._context_window = ctx_window
            self._max_output_tokens = max_out
            # ---------------------------------------------------------

            provider_info = work.get("provider_override")

            # -- Model routing via model sets --------------------------
            routed_entry = None
            if not provider_info and agent_def and self.config.model_sets:
                routed_entry = self._route_model(agent_def, work)
                if routed_entry is not None:
                    prov_cfg, model_cfg, entry = routed_entry
                    provider_info = {"name": prov_cfg.name, "model": model_cfg.slug}
                    routed_entry = entry
            # ---------------------------------------------------------

            if agent_def:
                override_name = (
                    provider_info.get("name")
                    if isinstance(provider_info, dict) else None
                )
                effective_provider = (
                    override_name
                    or work.get("provider")
                    or self.config.active_provider().name
                )
                if not agent_def.is_provider_allowed(effective_provider):
                    raise ValueError(
                        f"Agent '{agent_name}' is not allowed to run on "
                        f"provider '{effective_provider}'"
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

            if provider_info:
                payload["provider"] = provider_info

            if routed_entry is not None:
                payload["_routed_entry"] = {
                    "provider": routed_entry.provider,
                    "model": routed_entry.model,
                    "price_input": routed_entry.price_input,
                    "price_output": routed_entry.price_output,
                }

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
                        self._record_cost(payload, edata)
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

    def _resolve_tool_name(self, name: str) -> str:
        """Try to fix common LLM tool-name mistakes against the allowed set."""
        if self._allowed_tools is None or name in self._allowed_tools:
            return name
        # Bare function name without namespace (e.g. "read_file" -> "filesystem_read_file")
        for allowed in self._allowed_tools:
            if allowed.endswith(f"_{name}"):
                log.info("resolved tool alias %s -> %s", name, allowed)
                return allowed
        # Wrong namespace prefix — match on full function part after first underscore
        # (e.g. "filesystem_search_grep" -> "search_grep" where func="search_grep")
        if "_" in name:
            func_part = name.split("_", 1)[1]
            for allowed in self._allowed_tools:
                if "_" in allowed and allowed.split("_", 1)[1] == func_part:
                    log.info("resolved tool alias %s -> %s", name, allowed)
                    return allowed
        # Singular/plural mismatch (e.g. "task_create" -> "tasks_create")
        for allowed in self._allowed_tools:
            a_parts = allowed.split("_", 1)
            n_parts = name.split("_", 1)
            if len(a_parts) == 2 and len(n_parts) == 2 and a_parts[1] == n_parts[1]:
                if a_parts[0].rstrip("s") == n_parts[0].rstrip("s"):
                    log.info("resolved tool alias %s -> %s", name, allowed)
                    return allowed
        return name

    async def dispatch_tool(self, tool_name: str, args: dict) -> str:
        """Dispatch a tool call to the worker and return the result text.

        The worker handles sandbox proxying internally — the
        orchestrator always sends every tool call to the same worker
        endpoint.  Sandbox configuration is passed inside the
        ``context`` dict so the worker can start a sandbox lazily.

        The ``project_path`` is included in context so the sandbox
        proxy can scope each sandbox to a single project.
        """
        tool_name = self._resolve_tool_name(tool_name)

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
            "workspace": self.config.workspace,
        }
        if self._last_work and self._last_work.get("project"):
            ctx["project"] = self._last_work["project"]
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
        """Resolve the working directory path from the work dict.

        Prefers ``worktree`` (task clone / worktree) so the sandbox
        mounts the right directory.  Falls back to the project's
        canonical path.
        """
        if self._last_work is None:
            return ""
        wt = self._last_work.get("worktree", "")
        if wt:
            return wt
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

    async def _finalize_git(self, work: dict) -> dict | None:
        """Auto-commit and push when the task has a worktree.

        Returns a summary dict on success, ``None`` when skipped.
        Errors are logged but never propagated — git failures must
        not break the agent response stream.
        """
        import subprocess

        wt = work.get("worktree", "") if work else ""
        if not wt:
            return None

        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=wt, capture_output=True, text=True, timeout=15,
            )
            if status.returncode != 0 or not status.stdout.strip():
                return None

            subprocess.run(
                ["git", "add", "-A"],
                cwd=wt, capture_output=True, text=True, check=True, timeout=15,
            )

            title = work.get("title", "agent work")
            task_id = work.get("task_id", "")
            msg = f"{title}\n\nTask: {task_id}" if task_id else title

            commit = subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=wt, capture_output=True, text=True, timeout=30,
            )
            if commit.returncode != 0:
                if "nothing to commit" in (commit.stdout + commit.stderr):
                    return None
                log.warning("git commit failed: %s", commit.stderr.strip())
                return None

            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=wt, capture_output=True, text=True, timeout=5,
            )
            branch_name = branch.stdout.strip()

            push = subprocess.run(
                ["git", "push", "-u", "origin", branch_name],
                cwd=wt, capture_output=True, text=True, timeout=60,
            )
            if push.returncode != 0:
                log.warning("git push failed: %s", push.stderr.strip())

            result = {
                "committed": True,
                "branch": branch_name,
                "pushed": push.returncode == 0,
            }
            self.audit.record("git.finalize", phase="finalize", **result)
            return result

        except Exception:
            log.exception("_finalize_git failed for worktree %s", wt)
            return None

    def _done_event(self, git_result: dict | None = None) -> dict:
        """Build a done event and push it to the tracker."""
        data: dict = {}
        if git_result:
            data["git"] = git_result
        if self._ui_elements:
            data["ui_elements"] = self._ui_elements
            self._ui_elements = []
        ev: dict = {"event_type": "done", "data": data}
        if self.tracker and self.stream_id:
            self.tracker.push(self.stream_id, ev)
        return ev

    def _save_response(self, acc: Acc) -> None:
        """Persist the final assistant response to chat."""
        if acc.text and self.conversation:
            msg: dict = {"role": "assistant", "content": acc.text}
            if acc.reasoning:
                msg["reasoning"] = acc.reasoning
            try:
                self.chat.append(self.conversation, msg)
            except Exception:
                log.exception("Failed to save response to chat %s", self.conversation)

        self.audit.record(
            "response.saved", phase="response",
            text_length=len(acc.text),
            reasoning_length=len(acc.reasoning),
        )
        self.audit.save_payload("response", {
            "text": acc.text,
            "reasoning": acc.reasoning,
        })

    # ------------------------------------------------------------------
    # Interaction tool handling (UI element collection)
    # ------------------------------------------------------------------

    def _is_interaction_tool(self, tool_name: str) -> bool:
        """Check if a tool is an interaction tool (produces UI elements)."""
        from acai.tools.interaction import INTERACTION_TOOLS
        return tool_name in INTERACTION_TOOLS

    # ------------------------------------------------------------------
    # Sub-agent tool handling
    # ------------------------------------------------------------------

    def _is_subagent_tool(self, tool_name: str) -> bool:
        """Check if a tool is a subagent tool handled orchestrator-side."""
        from acai.tools.subagent import SUBAGENT_TOOLS
        return tool_name in SUBAGENT_TOOLS or tool_name in (
            "subagent_spawn_agent_async",
            "subagent_await_task",
            "subagent_check_task",
            "subagent_run_task",
        )

    async def _handle_subagent_tool(
        self, tool_name: str, args: dict, emit: list[dict],
    ) -> str:
        """Handle a subagent tool call. Returns the tool result text."""
        short_name = tool_name.replace("subagent_", "")

        if not self.task_runner:
            return json.dumps({"error": "Task runner not configured"})

        if short_name == "spawn_agent":
            return await self._spawn_agent_blocking(args, emit)
        elif short_name == "spawn_agent_async":
            return await self._spawn_agent_async(args, emit)
        elif short_name == "await_task":
            return await self._await_task(args)
        elif short_name == "check_task":
            return self._check_task(args)
        elif short_name == "run_task":
            return await self._run_background_task(args, emit)
        else:
            return json.dumps({"error": f"Unknown subagent tool: {tool_name}"})

    async def _spawn_agent_blocking(self, args: dict, emit: list[dict]) -> str:
        """Run a sub-agent inline and return its response."""
        agent_name = args.get("agent", "")
        message = args.get("message", "")
        context_text = args.get("context", "")
        max_iter = args.get("max_iterations", 10)

        if not agent_name or not message:
            return json.dumps({"error": "Both 'agent' and 'message' are required"})

        full_message = f"{context_text}\n\n{message}".strip() if context_text else message

        # Emit subagent_start event
        start_ev = {
            "event_type": "subagent_start",
            "data": {
                "agent": agent_name,
                "message_preview": full_message[:200],
                "conversation": self.conversation,
                "blocking": True,
            },
        }
        if self.tracker and self.stream_id:
            self.tracker.push(self.stream_id, start_ev)
        emit.append(start_ev)

        # Run a nested graph using the same infrastructure
        try:
            result = await self._run_nested_agent(
                agent_name, full_message, max_iterations=max_iter,
            )
        except Exception as exc:
            log.exception("Blocking sub-agent %s failed", agent_name)
            result = f"[Sub-agent error] {type(exc).__name__}: {exc}"

        # Emit subagent_complete
        complete_ev = {
            "event_type": "subagent_complete",
            "data": {
                "agent": agent_name,
                "result_preview": result[:500] if result else "",
                "conversation": self.conversation,
                "success": not result.startswith("[Sub-agent error]"),
            },
        }
        if self.tracker and self.stream_id:
            self.tracker.push(self.stream_id, complete_ev)
        emit.append(complete_ev)

        return result

    async def _spawn_agent_async(self, args: dict, emit: list[dict]) -> str:
        """Start a sub-agent in background, return task_id."""
        agent_name = args.get("agent", "")
        message = args.get("message", "")
        context_text = args.get("context", "")
        max_iter = args.get("max_iterations", 10)

        if not agent_name or not message:
            return json.dumps({"error": "Both 'agent' and 'message' are required"})

        full_message = f"{context_text}\n\n{message}".strip() if context_text else message

        async def _graph_factory(*, agent_name, message, conversation, **kw):
            return await self._run_nested_agent(
                agent_name, message, max_iterations=max_iter,
            )

        task_id = await self.task_runner.run_subagent_async(
            agent_name=agent_name,
            message=full_message,
            graph_factory=_graph_factory,
            parent_conversation=self.conversation,
        )

        start_ev = {
            "event_type": "subagent_start",
            "data": {
                "agent": agent_name,
                "task_id": task_id,
                "conversation": self.conversation,
                "blocking": False,
            },
        }
        if self.tracker and self.stream_id:
            self.tracker.push(self.stream_id, start_ev)
        emit.append(start_ev)

        return json.dumps({"task_id": task_id, "status": "running"})

    async def _await_task(self, args: dict) -> str:
        """Wait for a task to complete."""
        task_id = args.get("task_id", "")
        timeout = args.get("timeout", 300.0)

        if not task_id:
            return json.dumps({"error": "'task_id' is required"})

        try:
            task = await self.task_runner.wait_for_task(task_id, timeout=timeout)
            return json.dumps({
                "status": task.status.value,
                "result": task.result,
                "error": task.error,
            })
        except KeyError:
            return json.dumps({"error": f"Task {task_id!r} not found"})
        except TimeoutError:
            return json.dumps({"error": "Task did not complete in time", "timed_out": True})

    def _check_task(self, args: dict) -> str:
        """Non-blocking task status check."""
        task_id = args.get("task_id", "")
        if not task_id:
            return json.dumps({"error": "'task_id' is required"})

        task = self.task_runner.get_task(task_id)
        if task is None:
            return json.dumps({"error": f"Task {task_id!r} not found"})

        return json.dumps({
            "task_id": task_id,
            "status": task.status.value,
            "result": task.result if task.status.value == "completed" else "",
            "error": task.error if task.status.value == "failed" else "",
            "progress": task.progress,
        })

    async def _run_background_task(self, args: dict, emit: list[dict]) -> str:
        """Start a registered background task."""
        name = args.get("name", "")
        try:
            params = json.loads(args.get("params", "{}"))
        except (json.JSONDecodeError, TypeError):
            params = {}

        if not name:
            return json.dumps({"error": "'name' is required"})

        try:
            task_id = await self.task_runner.run_background(name, **params)
        except KeyError as exc:
            return json.dumps({"error": str(exc)})

        ev = {
            "event_type": "task_status",
            "data": {
                "task_id": task_id,
                "name": name,
                "status": "running",
                "conversation": self.conversation,
            },
        }
        if self.tracker and self.stream_id:
            self.tracker.push(self.stream_id, ev)
        emit.append(ev)

        return json.dumps({"task_id": task_id, "status": "running"})

    async def _run_nested_agent(
        self, agent_name: str, message: str, *, max_iterations: int = 10,
    ) -> str:
        """Run a nested agent graph and return its final text."""
        agent_def = self.agent(agent_name)
        if agent_def is None:
            return f"[Sub-agent error] Agent '{agent_name}' not found"

        from acai.orchestrator.agent_store import hydrate_task

        # Build a minimal work dict for the sub-agent
        work: dict = {
            "conversation": "",
            "messages": [{"role": "user", "content": message}],
            "agent": agent_name,
            "project": (self._last_work or {}).get("project", ""),
        }

        payload = self.prepare(agent_name, work)
        if not payload:
            return "[Sub-agent error] Failed to prepare payload"

        # Run the sub-agent's tool loop silently (no SSE forwarding)
        acc = Acc(self.dispatch(payload, stream_mode="silent"))
        async for _ in acc:
            pass

        _tool_round = 0
        while acc.tool_calls and _tool_round < max_iterations:
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

                if not tool_name:
                    result_text = "[Tool error] Empty function name"
                else:
                    try:
                        result_text = await self.dispatch_tool(tool_name, tool_args)
                    except Exception as exc:
                        result_text = f"[Tool error] {type(exc).__name__}: {exc}"

                if len(result_text) > _MAX_TOOL_RESULT_CHARS:
                    result_text = result_text[:_MAX_TOOL_RESULT_CHARS] + _TOOL_RESULT_TRUNCATION_MSG

                followup.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result_text,
                })

            payload = {**payload, "messages": followup}
            _tool_round += 1
            acc = Acc(self.dispatch(payload, stream_mode="silent"))
            async for _ in acc:
                pass

        return acc.text or ""

    async def _run_with_tools(self, payload: dict, *, max_iterations: int = _MAX_TOOL_ROUNDS) -> AsyncIterator[dict]:
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
            if _tool_round >= max_iterations:
                log.warning("tool loop hit max_iterations=%d, stopping", max_iterations)
                yield self._error_event(
                    f"Tool loop exceeded maximum iterations ({max_iterations})"
                )
                break

            followup = list(payload["messages"])
            followup.append({
                "role": "assistant",
                "content": acc.text or None,
                "tool_calls": acc.tool_calls,
            })

            # Persist intermediate assistant text so it's not lost
            if acc.text and self.conversation:
                self.chat.append(self.conversation, {
                    "role": "assistant",
                    "content": acc.text,
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

                if not tool_name:
                    result_text = "[Tool error] Tool call has an empty function name"
                elif self._is_subagent_tool(tool_name):
                    # Sub-agent tools are handled orchestrator-side
                    subagent_events: list[dict] = []
                    async with self.audit.aspan(
                        "tool", phase="subagent",
                        tool=tool_name, args=tool_args,
                        tool_round=_tool_round,
                    ):
                        try:
                            result_text = await self._handle_subagent_tool(
                                tool_name, tool_args, subagent_events,
                            )
                        except Exception as exc:
                            log.exception("subagent tool error: %s", tool_name)
                            result_text = f"[Tool error] {type(exc).__name__}: {exc}"
                    for sev in subagent_events:
                        yield sev
                else:
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

                if len(result_text) > _MAX_TOOL_RESULT_CHARS:
                    result_text = result_text[:_MAX_TOOL_RESULT_CHARS] + _TOOL_RESULT_TRUNCATION_MSG

                is_interaction = self._is_interaction_tool(tool_name)

                # Interaction tools execute on the user's browser — emit
                # the UI widget, then block until the user answers.
                if is_interaction and self.input_queue and self.conversation:
                    ui_element = None
                    try:
                        parsed = json.loads(result_text)
                        if isinstance(parsed, dict) and "ui_element" in parsed:
                            ui_element = parsed["ui_element"]
                    except (json.JSONDecodeError, TypeError):
                        pass

                    if ui_element:
                        # Emit SSE so the frontend renders the widget
                        interaction_ev = {
                            "event_type": "interaction",
                            "data": {"ui_element": ui_element},
                        }
                        if self.tracker and self.stream_id:
                            self.tracker.push(self.stream_id, interaction_ev)
                        yield interaction_ev

                        # Tell frontend we're waiting on the user, not processing
                        wait_ev = {
                            "event_type": "waiting_for_input",
                            "data": {"conversation": self.conversation},
                        }
                        if self.tracker and self.stream_id:
                            self.tracker.push(self.stream_id, wait_ev)
                        yield wait_ev

                        # Wait for the user's answer
                        from acai.orchestrator.input_queue import InputRequest
                        request = InputRequest(
                            conversation_id=self.conversation,
                            request_id=ui_element.get("id", tool_name),
                            question=ui_element.get("question", ui_element.get("message", "")),
                            options=ui_element.get("options", []),
                        )
                        try:
                            response = await self.input_queue.wait_for_input(
                                self.conversation, request,
                            )
                            result_text = response.get("text", "")
                        except TimeoutError:
                            result_text = "[No response — timed out]"

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
            # Enforce context limit on the growing followup messages
            ctx_window = getattr(self, "_context_window", None)
            max_out = getattr(self, "_max_output_tokens", None)
            if not ctx_window or not max_out:
                active_prov = self.config.active_provider()
                ctx_window = active_prov.context_window or 128000
                max_out = active_prov.max_tokens or 4096

            # Check budget before truncation
            estimated = _estimate_tokens(followup)
            available = int((ctx_window - max_out) * _HARD_LIMIT_RATIO)
            usage_ratio = estimated / available if available > 0 else 1.0

            if usage_ratio >= _CONTEXT_WARNING_RATIO:
                budget_ev = {
                    "event_type": "context_budget",
                    "data": {
                        "estimated_tokens": estimated,
                        "available_tokens": available,
                        "context_window": ctx_window,
                        "usage_percent": round(usage_ratio * 100, 1),
                        "tool_round": _tool_round,
                    },
                }
                if self.tracker and self.stream_id:
                    self.tracker.push(self.stream_id, budget_ev)
                yield budget_ev

            followup, was_truncated = enforce_context_limit(
                followup, ctx_window, max_out, keep_recent=12,
            )
            if was_truncated:
                log.warning(
                    "Tool loop round %d: context truncated (%d -> %d tokens)",
                    _tool_round, estimated, _estimate_tokens(followup),
                )
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
    # Context compression
    # ------------------------------------------------------------------

    async def _try_compress_conversation(self, work: dict) -> dict | None:
        """Attempt to compress the conversation if it's approaching the context limit.

        Calls the LLM to summarize old messages, then updates the
        conversation on disk so subsequent reads get the compressed
        version.  Returns an SSE event dict if compression occurred,
        or ``None`` if skipped.
        """
        from acai.orchestrator.agent_store import needs_compression, compress_messages
        from acai.provider import create_llm

        conv = self.conversation or work.get("conversation", "")
        if not conv:
            return None

        messages = self.chat.read(conv)
        if not messages:
            return None

        active_prov = self.config.active_provider()
        ctx_window = active_prov.context_window or 128000

        # Filter out display-only roles (same as resolve_task)
        _DISPLAY_ROLES = {"tool_call", "tool_result"}
        eligible = [m for m in messages if m.get("role") not in _DISPLAY_ROLES]

        if not needs_compression(eligible, ctx_window):
            return None

        log.info("Proactive compression starting for conversation %s", conv)

        try:
            llm = create_llm(active_prov)
            compressed, did_compress = compress_messages(
                eligible, ctx_window, llm,
            )
        except Exception:
            log.exception("Compression failed for conversation %s", conv)
            return None

        if not did_compress:
            return None

        # Persist the compressed conversation (replace full history)
        try:
            self.chat.write(conv, compressed)
        except Exception:
            log.exception("Failed to persist compressed conversation %s", conv)
            return None

        ev = {
            "event_type": "context_compressed",
            "data": {
                "conversation": conv,
                "original_messages": len(eligible),
                "compressed_messages": len(compressed),
            },
        }
        if self.tracker and self.stream_id:
            self.tracker.push(self.stream_id, ev)

        self.audit.record(
            "context.compressed", phase="prepare",
            original=len(eligible), compressed=len(compressed),
        )
        return ev

    # ------------------------------------------------------------------
    # run — abstract, subclasses must override
    # ------------------------------------------------------------------

    async def run(self, work: dict) -> AsyncIterator[dict]:
        """Execute the task graph.  Yields SSE event dicts.

        Subclasses must override this method.
        """
        raise NotImplementedError("Subclass TaskGraph and implement run()")
        yield  # type: ignore[misc]  # pragma: no cover
