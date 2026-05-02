"""Node type registry for DynamicGraph workflows.

Each node type is a subclass of :class:`NodeType`.  Register custom
nodes with the :func:`register` decorator or call it manually.

Built-in types
--------------
start, agent, agent_call, accumulate, stream_transform,
for_each, tool_loop, tool, append, reasoning_message, print,
condition, output, fetch_conversation, background_agent,
set_variable, get_variable

Creating a custom node
----------------------
::

    from acai.tasks.nodes import NodeType, Pin, NodeContext, register

    @register
    class MyNode(NodeType):
        type = "my_node"
        label = "My Node"
        accent = "#c45555"
        description = "Does something cool"
        pins = [
            Pin.exec_in(),
            Pin.exec_out(),
            Pin.data("data_input", "input", Colors.green, "left",
                     pin_type="string"),
            Pin.data("data_output", "output", Colors.green, "right",
                     pin_type="stream[string]"),
            Pin.data("data_mode", "mode", Colors.amber, "left",
                     pin_type="string", choices=("fast", "balanced", "quality")),
            Pin.data("data_enabled", "enabled", Colors.green, "left",
                     pin_type="bool"),
        ]

        async def execute(self, ctx: NodeContext):
            value = ctx.inputs.get("input", "")
            # yield events to stream to the user
            yield {"type": "event", "data": {"event_type": "token", "data": {"token": "hi"}}}
            # yield output to populate downstream data pins
            yield {"type": "output", "data": {"output": do_something(value)}}
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from acai.tasks.dynamic import DynamicGraph

log = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def substitute(template: str, variables: dict[str, str]) -> str:
    """Replace ``{{name}}`` placeholders with values from *variables*."""
    def _repl(m: re.Match) -> str:
        return variables.get(m.group(1), m.group(0))
    return _VAR_RE.sub(_repl, template)


# ===================================================================
# Colors (must stay in sync with the frontend C palette)
# ===================================================================

class Colors:
    white  = "#ccc"      # noqa: E221
    green  = "#7fba55"   # noqa: E221
    blue   = "#5b9bd5"   # noqa: E221
    amber  = "#d4a44c"   # noqa: E221
    cyan   = "#5cc6c6"   # noqa: E221
    red    = "#c45555"   # noqa: E221
    purple = "#9b7ed0"   # noqa: E221
    pink   = "#e06090"   # noqa: E221


# ===================================================================
# PinDef
# ===================================================================

@dataclass(frozen=True, slots=True)
class Pin:
    id: str
    label: str
    color: str
    side: str          # "left" | "right"
    kind: str          # "exec" | "data"
    pin_type: str = "string"   # "string" | "bool" | "int" | "float" | "json"
                               # | "stream" | "stream[string]" | "stream[json]"
                               # | "message" | "message_list" | "format" | "any"
    choices: tuple[str, ...] = ()
    dynamic_choices: str = ""   # "agents" | "conversations" — frontend fetches list
    optional: bool = True

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id, "label": self.label, "color": self.color,
            "side": self.side, "kind": self.kind, "pin_type": self.pin_type,
            "optional": self.optional,
        }
        if self.choices:
            d["choices"] = list(self.choices)
        if self.dynamic_choices:
            d["dynamic_choices"] = self.dynamic_choices
        return d

    # Convenience constructors
    @staticmethod
    def exec_in() -> Pin:
        return Pin("exec_in", "", Colors.white, "left", "exec")

    @staticmethod
    def exec_out() -> Pin:
        return Pin("exec_out", "", Colors.white, "right", "exec")

    @staticmethod
    def exec(id: str, label: str, color: str, side: str) -> Pin:
        return Pin(id, label, color, side, "exec")

    @staticmethod
    def data(
        id: str,
        label: str,
        color: str,
        side: str,
        pin_type: str = "string",
        choices: tuple[str, ...] | list[str] = (),
        dynamic_choices: str = "",
        optional: bool = True,
    ) -> Pin:
        return Pin(
            id, label, color, side, "data",
            pin_type=pin_type,
            choices=tuple(choices),
            dynamic_choices=dynamic_choices,
            optional=optional,
        )


# ===================================================================
# Pin-type compatibility
# ===================================================================

def pin_types_compatible(source_type: str, target_type: str) -> bool:
    """Check whether two pin types are compatible for a data edge."""
    if source_type == "any" or target_type == "any":
        return True
    return source_type == target_type


# ===================================================================
# NodeContext — passed to every node's execute method
# ===================================================================

@dataclass
class NodeContext:
    graph: DynamicGraph
    node_id: str
    data: dict                 # per-node config from the spec
    inputs: dict[str, Any]     # resolved data-pin inputs
    work: dict                 # top-level work dict

    @property
    def agent_name(self) -> str:
        """Agent name selected in the chat input."""
        return self.work.get("agent", "default")

    @property
    def provider(self) -> str:
        """Provider name selected in the chat input (``"auto"`` if default)."""
        return self.work.get("provider", "auto")

    @property
    def model(self) -> str:
        """Resolved model identifier for the active provider."""
        return self.work.get("model", "")

    @property
    def enable_thinking(self) -> bool | None:
        """Thinking toggle state from the chat input (``None`` if unset)."""
        return self.work.get("enable_thinking")


# ===================================================================
# NodeType — base class
# ===================================================================

class NodeType:
    """Base class for all graph node types.

    Subclass attributes:

    * ``type``        — unique string identifier (e.g. ``"agent"``).
    * ``label``       — human-readable name.
    * ``accent``      — hex color for the node header.
    * ``description`` — short tooltip text.
    * ``category``    — grouping label for palette/menus (default ``"General"``).
    * ``pins``        — list of :class:`Pin` definitions.

    The ``execute`` method is an **async generator** that yields
    tagged event dicts:

    * ``{"type": "event", "data": ...}`` — forwarded to the user
      as-is (SSE tokens, tool progress, etc.).
    * ``{"type": "output", "data": {pin: value, ...}}`` — consumed
      by ``DynamicGraph`` to populate downstream data pins.
    """

    type: str = ""
    label: str = ""
    accent: str = "#888"
    description: str = ""
    category: str = "General"
    pins: list[Pin] = []

    @classmethod
    def dynamic_pins(
        cls,
        data: dict,
        spec: dict | None = None,
        **ctx: Any,
    ) -> list[Pin]:
        """Return extra pins that depend on node configuration.

        Override in subclasses whose pins change based on the node's
        ``data`` (e.g. a selected tool, a connected schema).  The
        type checker and frontend call this to resolve pin types for
        handles that aren't in the static ``pins`` list.

        Parameters
        ----------
        data:
            The node's ``data`` dict from the workflow spec.
        spec:
            The full workflow spec (nodes + edges) — needed when
            dynamic pins depend on other nodes in the graph.
        **ctx:
            Extra context, e.g. ``tool_defs`` for tool registries.
        """
        return []

    async def execute(self, ctx: NodeContext):
        """Async generator yielding ``{"type": ..., "data": ...}`` dicts."""
        yield {"type": "output", "data": {}}
        return  # noqa: B901

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "label": self.label,
            "accent": self.accent,
            "description": self.description,
            "category": self.category,
            "pins": [p.to_dict() for p in self.pins],
        }


# ===================================================================
# Registry
# ===================================================================

_REGISTRY: dict[str, NodeType] = {}


def register(cls: type[NodeType]) -> type[NodeType]:
    """Class decorator — register a node type."""
    inst = cls()
    if not inst.type:
        raise ValueError(f"{cls.__name__}.type must be a non-empty string")
    _REGISTRY[inst.type] = inst
    return cls


def get(type_name: str) -> NodeType | None:
    """Look up a registered node type by its string id."""
    return _REGISTRY.get(type_name)


def all_types() -> list[NodeType]:
    """Return all registered node types (ordered by registration)."""
    return list(_REGISTRY.values())


def describe_registry() -> str:
    """Generate a markdown description of all registered node types.

    Used to dynamically populate agent system prompts (e.g. graph_builder)
    so they always reflect the current node definitions.
    """
    pin_types_seen: set[str] = set()
    categories: dict[str, list[NodeType]] = {}
    for nt in _REGISTRY.values():
        categories.setdefault(nt.category, []).append(nt)
        for p in nt.pins:
            if p.kind == "data":
                pin_types_seen.add(p.pin_type)

    lines: list[str] = ["## Available Node Types\n"]

    for cat, nodes in categories.items():
        lines.append(f"### {cat} Nodes\n")
        for nt in nodes:
            lines.append(f"**{nt.type}** — {nt.description}")

            inputs = [p for p in nt.pins if p.side == "left"]
            outputs = [p for p in nt.pins if p.side == "right"]

            if inputs:
                parts = []
                for p in inputs:
                    req = ", required" if not p.optional else ""
                    if p.kind == "exec":
                        parts.append(f"`{p.id}`")
                    else:
                        parts.append(f"`{p.id}` ({p.pin_type}{req})")
                lines.append(f"- Inputs: {', '.join(parts)}")

            if outputs:
                parts = []
                for p in outputs:
                    if p.kind == "exec":
                        parts.append(f"`{p.id}`")
                    else:
                        parts.append(f"`{p.id}` ({p.pin_type})")
                lines.append(f"- Outputs: {', '.join(parts)}")

            lines.append("")

    lines.append("## Pin Type System\n")
    sorted_types = sorted(pin_types_seen)
    lines.append(f"Pin types: {', '.join(f'`{t}`' for t in sorted_types)}.")
    lines.append("- `format` carries a structured output format — distinct from `json`.")
    lines.append("- `any` is compatible with all types.")
    lines.append("- Otherwise, source and target pin types must match exactly.")
    lines.append("- Data pin IDs always start with `data_` (e.g. `data_context`).")
    lines.append("- Exec pin IDs always start with `exec_` (e.g. `exec_in`, `exec_out`).")

    return "\n".join(lines)


# ===================================================================
# Helpers
# ===================================================================

_AGENT_NODE_KEYS = frozenset({
    "label", "agent", "prompt_template", "stream_mode",
    "preview_message", "expression", "tool", "args", "args_json",
    "target_mode", "mode", "conversation_id", "debug",
})


def _extra_context(ctx: NodeContext) -> dict[str, Any] | None:
    """Collect extra template variables from node data and wired inputs.

    Any key in ``ctx.data`` or ``ctx.inputs`` that is not a well-known
    node configuration key is treated as an extra Jinja2 template variable.
    """
    extra: dict[str, Any] = {}
    for key, value in ctx.data.items():
        if key.startswith("_") or key in _AGENT_NODE_KEYS:
            continue
        extra[key] = value
    for key, value in ctx.inputs.items():
        if key.startswith("_") or key in _AGENT_NODE_KEYS:
            continue
        if key in ("agent", "context", "stream_mode", "phase"):
            continue
        extra[key] = value
    return extra or None


# ===================================================================
# Built-in node types
# ===================================================================

@register
class StartNode(NodeType):
    type = "start"
    label = "Start"
    accent = Colors.green
    description = "Entry point"
    category = "Flow"
    pins = [
        Pin.exec_out(),
        Pin.data("data_conversation", "conversation", Colors.blue, "right",
                 pin_type="message_list"),
        Pin.data("data_message", "message", Colors.amber, "right",
                 pin_type="message"),
        Pin.data("data_agent", "agent", Colors.purple, "right",
                 pin_type="string"),
        Pin.data("data_model", "model", Colors.cyan, "right",
                 pin_type="string"),
    ]

    async def execute(self, ctx: NodeContext):
        msg_text = (ctx.work.get("message", "")
                    or ctx.data.get("preview_message", ""))
        message: dict = {"role": "user", "content": msg_text}

        conversation: list = []
        conv_id = getattr(ctx.graph, "conversation", "")
        if conv_id and hasattr(ctx.graph, "chat"):
            conversation = list(ctx.graph.chat.read(conv_id))

        yield {"type": "output", "data": {
            "message": message,
            "conversation": conversation,
            "agent": ctx.agent_name,
            "model": ctx.model,
        }}


@register
class AgentCallNode(NodeType):
    """Pure LLM agent call — prepare, dispatch, return the stream.

    Calls ``prepare()`` then ``dispatch()``.  Token events are pushed
    to the user in real time by ``dispatch()`` via the tracker.

    Returns a bundled dict on the ``stream`` pin so downstream nodes
    (AccForward, ToolLoop, etc.) can inspect or transform the result.
    """

    type = "agent_call"
    label = "Agent Call"
    accent = Colors.blue
    description = "LLM call"
    category = "Agent"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_agent", "agent", Colors.cyan, "left", pin_type="string",
                 dynamic_choices="agents"),
        Pin.data("data_context", "context", Colors.blue, "left",
                 pin_type="message_list"),
        Pin.data("data_stream_mode", "stream_mode", Colors.amber, "left",
                 pin_type="string",
                 choices=("token", "reasoning", "silent")),
        Pin.data("data_format", "format", "#c06cdb", "left",
                 pin_type="format"),
        Pin.data("data_stream", "stream", Colors.green, "right",
                 pin_type="stream"),
        Pin.data("data_payload", "payload", Colors.blue, "right",
                 pin_type="json"),
    ]

    async def execute(self, ctx: NodeContext):
        agent_name = (ctx.inputs.get("agent", "") or ctx.data.get("agent", "default"))
        stream_mode = (ctx.inputs.get("stream_mode", "") or ctx.data.get("stream_mode", "token"))
        context = ctx.inputs.get("context", [])

        agent_work = dict(ctx.work)

        if isinstance(context, list):
            context_str = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}"
                for m in context if isinstance(m, dict)
            ) if context else ""
        else:
            context_str = str(context)

        agent_work["message"] = context_str

        if ctx.data.get("prompt_template"):
            variables = {"context": context_str, "input": context_str,
                         "message": ctx.work.get("message", "")}
            agent_work["message"] = substitute(
                ctx.data["prompt_template"], variables,
            )

        extra = _extra_context(ctx)
        prepare_kwargs: dict[str, Any] = {}
        if extra:
            prepare_kwargs["extra_context"] = extra
        log.info("AgentCallNode[%s] agent=%s inputs=%s extra=%s",
                 ctx.node_id, agent_name, list(ctx.inputs.keys()),
                 list(extra.keys()) if extra else None)
        payload = ctx.graph.prepare(agent_name, agent_work, **prepare_kwargs)

        if isinstance(context, list) and context:
            prepared = payload.get("messages", [])
            system_msgs = [m for m in prepared if m.get("role") == "system"]
            payload["messages"] = system_msgs + list(context)

        fmt = ctx.inputs.get("format")
        if fmt and isinstance(fmt, dict):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": fmt.get("title", "structured_output"),
                    "strict": True,
                    "schema": fmt,
                },
            }

        yield {
            "type": "output",
            "data": {
                "stream": ctx.graph.dispatch(payload, stream_mode=stream_mode),
                "payload": payload,
            },
        }


@register
class AccumulateNode(NodeType):
    """Consume a token stream, forward events to the user, and accumulate.

    Each event from the stream is yielded through to the user while
    the full text and reasoning are accumulated for downstream pins.
    """

    type = "accumulate"
    label = "Accumulate"
    accent = Colors.green
    description = "Stream to response"
    category = "Agent"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_stream", "stream", Colors.green, "left",
                 pin_type="stream", optional=False),
        Pin.data("data_event_mode", "event_mode", Colors.purple, "left",
                 pin_type="string"),
        Pin.data("data_response", "response", Colors.amber, "right",
                 pin_type="message"),
        Pin.data("data_text", "text", Colors.green, "right",
                 pin_type="string"),
        Pin.data("data_reasoning", "reasoning", Colors.purple, "right",
                 pin_type="string"),
        Pin.data("data_tool_calls", "tool_calls", Colors.amber, "right",
                 pin_type="json"),
    ]

    async def execute(self, ctx: NodeContext):
        from acai.tasks.graph import Acc

        stream = ctx.inputs.get("stream")
        if stream is None:
            yield {"type": "output", "data": {
                "response": {"role": "assistant", "content": ""},
                "text": "",
                "reasoning": "",
                "tool_calls": [],
            }}
            return

        event_mode = (ctx.inputs.get("event_mode")
                      or ctx.data.get("event_mode", ""))

        acc = Acc(stream)
        async for event in acc:
            if event_mode == "silent":
                continue
            if event_mode and event.get("event_type") in ("token", "reasoning"):
                event = {**event, "event_type": event_mode}
            yield {"type": "event", "data": event}

        yield {"type": "output", "data": {
            "response": {"role": "assistant", "content": acc.text},
            "text": acc.text,
            "reasoning": acc.reasoning,
            "tool_calls": acc.tool_calls,
        }}


@register
class SimpleAgentNode(NodeType):
    """Agent call + accumulate in a single node.

    Equivalent to wiring ``agent_call.data_stream → accumulate.data_stream``
    but collapsed into one canvas node for simpler workflows.
    """

    type = "simple_agent"
    label = "Simple Agent"
    accent = Colors.blue
    description = "Agent call + accumulate (combined)"
    category = "Agent"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_agent", "agent", Colors.cyan, "left", pin_type="string",
                 dynamic_choices="agents"),
        Pin.data("data_context", "context", Colors.blue, "left",
                 pin_type="message_list"),
        Pin.data("data_stream_mode", "stream_mode", Colors.amber, "left",
                 pin_type="string",
                 choices=("token", "reasoning", "silent")),
        Pin.data("data_format", "format", "#c06cdb", "left",
                 pin_type="format"),
        Pin.data("data_response", "response", Colors.amber, "right",
                 pin_type="message"),
        Pin.data("data_text", "text", Colors.green, "right",
                 pin_type="string"),
        Pin.data("data_reasoning", "reasoning", Colors.purple, "right",
                 pin_type="string"),
        Pin.data("data_tool_calls", "tool_calls", Colors.amber, "right",
                 pin_type="json"),
        Pin.data("data_payload", "payload", Colors.blue, "right",
                 pin_type="json"),
    ]

    async def execute(self, ctx: NodeContext):
        from acai.tasks.graph import Acc

        agent_name = (ctx.inputs.get("agent", "") or ctx.data.get("agent", "default"))
        stream_mode = (ctx.inputs.get("stream_mode", "") or ctx.data.get("stream_mode", "token"))
        context = ctx.inputs.get("context", [])

        agent_work = dict(ctx.work)

        if isinstance(context, list):
            context_str = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}"
                for m in context if isinstance(m, dict)
            ) if context else ""
        else:
            context_str = str(context)

        agent_work["message"] = context_str

        if ctx.data.get("prompt_template"):
            variables = {"context": context_str, "input": context_str,
                         "message": ctx.work.get("message", "")}
            agent_work["message"] = substitute(
                ctx.data["prompt_template"], variables,
            )

        extra = _extra_context(ctx)
        prepare_kwargs: dict[str, Any] = {}
        if extra:
            prepare_kwargs["extra_context"] = extra
        log.info("SimpleAgentNode[%s] agent=%s inputs=%s extra=%s",
                 ctx.node_id, agent_name, list(ctx.inputs.keys()),
                 list(extra.keys()) if extra else None)
        payload = ctx.graph.prepare(agent_name, agent_work, **prepare_kwargs)

        if isinstance(context, list) and context:
            prepared = payload.get("messages", [])
            system_msgs = [m for m in prepared if m.get("role") == "system"]
            payload["messages"] = system_msgs + list(context)

        fmt = ctx.inputs.get("format")
        if fmt and isinstance(fmt, dict):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": fmt.get("title", "structured_output"),
                    "strict": True,
                    "schema": fmt,
                },
            }

        stream = ctx.graph.dispatch(payload, stream_mode=stream_mode)

        acc = Acc(stream)
        async for event in acc:
            if stream_mode == "silent":
                continue
            yield {"type": "event", "data": event}

        yield {"type": "output", "data": {
            "response": {"role": "assistant", "content": acc.text},
            "text": acc.text,
            "reasoning": acc.reasoning,
            "tool_calls": acc.tool_calls,
            "payload": payload,
        }}


@register
class StreamTransformNode(NodeType):
    """Relabel stream event modes (e.g. token -> reasoning).

    Takes the bundled stream dict from :class:`AgentCallNode` (or a
    plain token list) and rewrites the ``mode`` field of every event
    to the value configured in ``node.data.target_mode``.  Useful when
    you want the agent to dispatch in default ``"token"`` mode but
    display the result as ``"reasoning"`` (or vice-versa) without
    changing the agent node itself.
    """

    type = "stream_transform"
    label = "Stream Transform"
    accent = Colors.purple
    description = "Relabel stream mode"
    category = "Agent"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_stream", "stream", Colors.green, "left",
                 pin_type="stream", optional=False),
        Pin.data("data_stream_out", "stream_out", Colors.green, "right",
                 pin_type="stream"),
    ]

    async def execute(self, ctx: NodeContext):
        stream = ctx.inputs.get("stream")
        target_mode = ctx.data.get("target_mode", "reasoning")

        async def transformed():
            if stream is None:
                return
            async for event in stream:
                if event.get("event_type") in ("token", "reasoning"):
                    yield {**event, "event_type": target_mode}
                else:
                    yield event

        yield {"type": "output", "data": {"stream_out": transformed()}}


@register
class ToolFollowUpLoopNode(NodeType):
    """Dispatch tool calls and re-call the agent until no tools remain.

    Receives the initial ``response`` message, ``tool_calls``, and
    the original ``payload`` from an upstream :class:`AccumulateNode`.
    If there are tool calls, dispatches them, appends results to the
    conversation, and re-calls the LLM in a loop.  If there are no
    tool calls, passes the response through unchanged.
    """

    type = "tool_followup_loop"
    label = "Tool Follow-Up"
    accent = Colors.amber
    description = "Execute tools & re-call LLM"
    category = "Agent"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_response_in", "response", Colors.blue, "left",
                 pin_type="message"),
        Pin.data("data_tool_calls", "tool_calls", Colors.amber, "left",
                 pin_type="json"),
        Pin.data("data_payload", "payload", Colors.blue, "left",
                 pin_type="json", optional=False),
        Pin.data("data_event_mode", "event_mode", Colors.purple, "left",
                 pin_type="string"),
        Pin.data("data_follow_up", "follow_up", Colors.pink, "left",
                 pin_type="bool"),
        Pin.data("data_response", "response", Colors.amber, "right",
                 pin_type="message"),
        Pin.data("data_messages", "messages", Colors.blue, "right",
                 pin_type="message_list"),
    ]

    async def execute(self, ctx: NodeContext):  # noqa: C901
        from acai.tasks.graph import Acc

        response = ctx.inputs.get("response", {"role": "assistant", "content": ""})
        tool_calls = ctx.inputs.get("tool_calls", [])
        payload = ctx.inputs.get("payload", {})
        event_mode = (ctx.inputs.get("event_mode")
                      or ctx.data.get("event_mode", ""))
        follow_up_raw = ctx.inputs.get("follow_up", ctx.data.get("follow_up", True))
        follow_up = follow_up_raw is True or str(follow_up_raw).lower() in ("true", "1", "yes", "on")

        base_messages = list(payload.get("messages", []))
        new_messages: list[dict] = []

        if not tool_calls:
            yield {"type": "output", "data": {
                "response": response,
                "messages": [],
            }}
            return

        text = response.get("content", "") if isinstance(response, dict) else ""
        new_messages.append({
            "role": "assistant",
            "content": text or None,
            "tool_calls": tool_calls,
        })

        while tool_calls:
            for call in tool_calls:
                fn = call.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    tool_args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                yield {"type": "event", "data": {
                    "event_type": "tool_start",
                    "data": {"node_id": ctx.node_id,
                             "tool_name": tool_name, "args": tool_args},
                }}

                try:
                    result_text = await ctx.graph.dispatch_tool(
                        tool_name, tool_args,
                    )
                except Exception as exc:
                    log.exception("tool dispatch error: %s", tool_name)
                    result_text = (
                        f"[Tool error] {type(exc).__name__}: {exc}"
                    )

                new_messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result_text,
                })

                yield {"type": "event", "data": {
                    "event_type": "tool_end",
                    "data": {"node_id": ctx.node_id,
                             "tool_name": tool_name,
                             "result_preview": result_text[:2000]},
                }}

            if not follow_up:
                break

            payload = dict(payload, messages=base_messages + new_messages)
            acc = Acc(ctx.graph.dispatch(payload))
            async for event in acc:
                if event_mode and event.get("event_type") in ("token", "reasoning"):
                    event = {**event, "event_type": event_mode}
                yield {"type": "event", "data": event}

            tool_calls = acc.tool_calls
            text = acc.text

            if tool_calls:
                new_messages.append({
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": tool_calls,
                })

        if follow_up:
            final_response = {"role": "assistant", "content": acc.text}
        else:
            final_response = response

        new_messages.append(final_response)
        yield {"type": "output", "data": {
            "response": final_response,
            "messages": new_messages,
        }}



@register
class BackgroundAgentNode(NodeType):
    """All-in-one background agent: prepare → dispatch → tool loop.

    Emits phase-scoped events (``{phase}_start``, ``{phase}_token``,
    ``{phase}_tool_start/end``, ``{phase}_end``) so the frontend
    groups everything inside the agent's own collapsible bubble.
    """

    type = "background_agent"
    label = "Background Agent"
    accent = "#667eea"
    description = "Silent agent with tools"
    category = "Agent"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_agent", "agent", Colors.cyan, "left", pin_type="string",
                 dynamic_choices="agents"),
        Pin.data("data_context", "context", Colors.blue, "left",
                 pin_type="message_list"),
        Pin.data("data_phase", "phase", Colors.purple, "left",
                 pin_type="string"),
        Pin.data("data_format", "format", "#c06cdb", "left",
                 pin_type="format"),
        Pin.data("data_response", "response", Colors.amber, "right",
                 pin_type="message"),
        Pin.data("data_text", "text", Colors.green, "right",
                 pin_type="string"),
    ]

    async def execute(self, ctx: NodeContext):  # noqa: C901
        from acai.tasks.graph import Acc

        agent_name = (ctx.inputs.get("agent", "")
                      or ctx.data.get("agent", "default"))
        phase = (ctx.inputs.get("phase", "")
                 or ctx.data.get("phase", "")
                 or ctx.data.get("label", agent_name)).lower().replace(" ", "_")
        context = ctx.inputs.get("context", [])

        agent_work = dict(ctx.work)
        if isinstance(context, list):
            context_str = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}"
                for m in context if isinstance(m, dict)
            ) if context else ""
        else:
            context_str = str(context)
        agent_work["message"] = context_str

        extra = _extra_context(ctx)
        prepare_kwargs: dict[str, Any] = {}
        if extra:
            prepare_kwargs["extra_context"] = extra

        payload = ctx.graph.prepare(agent_name, agent_work, **prepare_kwargs)

        if isinstance(context, list) and context:
            prepared = payload.get("messages", [])
            system_msgs = [m for m in prepared if m.get("role") == "system"]
            payload["messages"] = system_msgs + list(context)

        fmt = ctx.inputs.get("format")
        if fmt and isinstance(fmt, dict):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": fmt.get("title", "structured_output"),
                    "strict": True,
                    "schema": fmt,
                },
            }

        yield {"type": "event", "data": {
            "event_type": f"{phase}_start",
            "data": {"agent": agent_name},
        }}

        acc = Acc(ctx.graph.dispatch(payload))
        async for event in acc:
            if event.get("event_type") in ("token", "reasoning"):
                yield {"type": "event", "data": {
                    **event, "event_type": f"{phase}_token",
                }}
            else:
                yield {"type": "event", "data": event}

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

                yield {"type": "event", "data": {
                    "event_type": f"{phase}_tool_start",
                    "data": {"tool_name": tool_name, "args": tool_args},
                }}
                try:
                    result_text = await ctx.graph.dispatch_tool(
                        tool_name, tool_args,
                    )
                except Exception as exc:
                    log.exception("%s tool error: %s", phase, tool_name)
                    result_text = (
                        f"[Tool error] {type(exc).__name__}: {exc}"
                    )

                followup.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result_text,
                })
                yield {"type": "event", "data": {
                    "event_type": f"{phase}_tool_end",
                    "data": {"tool_name": tool_name,
                             "result_preview": result_text[:2000]},
                }}

            payload = dict(payload, messages=followup)
            acc = Acc(ctx.graph.dispatch(payload))
            async for event in acc:
                pass  # silent on follow-up rounds

        yield {"type": "event", "data": {
            "event_type": f"{phase}_end",
            "data": {"status": "done", "text_length": len(acc.text)},
        }}
        yield {"type": "output", "data": {
            "response": {"role": "assistant", "content": acc.text},
            "text": acc.text,
        }}


@register
class AppendNode(NodeType):
    type = "append"
    label = "Append"
    accent = Colors.purple
    description = "Append item to array"
    category = "Data"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_a", "array", Colors.blue, "left",
                 pin_type="message_list"),
        Pin.data("data_b", "item", Colors.amber, "left", pin_type="any"),
        Pin.data("data_result", "result", Colors.blue, "right",
                 pin_type="message_list"),
    ]

    async def execute(self, ctx: NodeContext):
        a = ctx.inputs.get("a", [])
        b = ctx.inputs.get("b", {})
        if not isinstance(a, list):
            a = [a] if a else []
        result = list(a)
        if isinstance(b, dict):
            result.append(b)
        elif isinstance(b, list):
            result.extend(b)
        elif b:
            result.append({"role": "assistant", "content": str(b)})
        yield {"type": "output", "data": {"result": result}}


@register
class ExtendNode(NodeType):
    type = "extend"
    label = "Extend"
    accent = Colors.purple
    description = "Merge two message lists"
    category = "Data"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_a", "first", Colors.blue, "left",
                 pin_type="message_list"),
        Pin.data("data_b", "second", Colors.amber, "left",
                 pin_type="message_list"),
        Pin.data("data_result", "result", Colors.blue, "right",
                 pin_type="message_list"),
    ]

    async def execute(self, ctx: NodeContext):
        a = ctx.inputs.get("a", [])
        b = ctx.inputs.get("b", [])
        if not isinstance(a, list):
            a = [a] if a else []
        if not isinstance(b, list):
            b = [b] if b else []
        yield {"type": "output", "data": {"result": list(a) + list(b)}}


@register
class ReasoningMessageNode(NodeType):
    """Wrap a reasoning string into a system message.

    Takes the accumulated reasoning text (e.g. from an AccumulateNode)
    and outputs a single message dict that can be appended to a
    conversation via the Append node before passing to an AgentCall.
    """

    type = "reasoning_message"
    label = "Reasoning Message"
    accent = Colors.purple
    description = "Wrap reasoning into a system message"
    category = "Data"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_reasoning", "reasoning", Colors.purple, "left",
                 pin_type="string", optional=False),
        Pin.data("data_message", "message", Colors.blue, "right",
                 pin_type="message"),
    ]

    async def execute(self, ctx: NodeContext):
        reasoning = ctx.inputs.get("reasoning", "")
        if reasoning:
            message: dict | None = {
                "role": "system",
                "content": (
                    "## Prior Reasoning\n"
                    "The following analysis was produced about this task. "
                    "Use it to inform your response.\n\n"
                    + reasoning
                ),
            }
        else:
            message = None
        yield {"type": "output", "data": {"message": message}}


@register
class ContentNode(NodeType):
    """Extract the content string from a message dict."""

    type = "content"
    label = "Content"
    accent = Colors.green
    description = "Extract content from a message"
    category = "Data"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_message", "message", Colors.blue, "left",
                 pin_type="message", optional=False),
        Pin.data("data_content", "content", Colors.green, "right",
                 pin_type="string"),
    ]

    async def execute(self, ctx: NodeContext):
        msg = ctx.inputs.get("message", {})
        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        yield {"type": "output", "data": {"content": content}}


@register
class RoleNode(NodeType):
    """Extract the role string from a message dict."""

    type = "role"
    label = "Role"
    accent = Colors.amber
    description = "Extract role from a message"
    category = "Data"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_message", "message", Colors.blue, "left",
                 pin_type="message", optional=False),
        Pin.data("data_role", "role", Colors.amber, "right",
                 pin_type="string"),
    ]

    async def execute(self, ctx: NodeContext):
        msg = ctx.inputs.get("message", {})
        role = msg.get("role", "") if isinstance(msg, dict) else ""
        yield {"type": "output", "data": {"role": role}}


@register
class ConditionNode(NodeType):
    type = "condition"
    label = "Condition"
    accent = Colors.red
    description = "Branch on expression"
    category = "Flow"
    pins = [
        Pin.exec_in(),
        Pin.exec("exec_true", "true", Colors.green, "right"),
        Pin.exec("exec_false", "false", Colors.red, "right"),
        Pin.data("data_value", "value", Colors.green, "left", pin_type="any",
                 optional=False),
    ]

    async def execute(self, ctx: NodeContext):
        value = ctx.inputs.get("value", "")
        if isinstance(value, (list, dict)):
            input_for_eval = json.dumps(value, ensure_ascii=False)
        else:
            input_for_eval = str(value)
        expression = ctx.data.get("expression", "True")
        try:
            result = bool(eval(  # noqa: S307
                expression,
                {"__builtins__": {}},
                {"input": input_for_eval, "value": value, "len": len},
            ))
        except Exception:
            result = True
        yield {"type": "output", "data": {"_condition": result, "value": value}}


@register
class OutputNode(NodeType):
    type = "output"
    label = "Output"
    accent = Colors.cyan
    description = "Final response"
    category = "Flow"
    pins = [
        Pin.exec_in(),
        Pin.data("data_response", "stream", Colors.green, "left",
                 pin_type="any", optional=False),
    ]

    async def execute(self, ctx: NodeContext):
        stream = ctx.inputs.get("stream", [])

        if hasattr(stream, "__aiter__"):
            async for event in stream:
                yield {"type": "event", "data": event}
    
        elif isinstance(stream, (list, tuple)):
            for event in stream:
                yield {"type": "event", "data": event}

        yield {"type": "output", "data": {}}


@register
class PrintNode(NodeType):
    """Debug node — JSON-dump the input value and send it to the user."""

    type = "print"
    label = "Print"
    accent = Colors.cyan
    description = "Display value"
    category = "Debug"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_value", "value", Colors.green, "left", pin_type="any"),
    ]

    async def execute(self, ctx: NodeContext):
        value = ctx.inputs.get("value", None)
        try:
            text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
        yield {"type": "event", "data": {
            "event_type": "print",
            "data": {"node_id": ctx.node_id,
                     "label": ctx.data.get("label", "Print"),
                     "text": text},
        }}
        yield {"type": "output", "data": {}}


@register
class LoadKnowledgeNode(NodeType):
    """Load knowledge documents by path and build a knowledge_context string.

    Accepts a list of document paths (``subject/subsubject/title``)
    and reads each from the knowledge store.  Outputs a formatted
    markdown string suitable for injection into an agent template.
    """

    type = "load_knowledge"
    label = "Load Knowledge"
    accent = Colors.cyan
    description = "Load knowledge files by path"
    category = "Data"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_paths", "paths", Colors.green, "left", pin_type="json",
                 optional=False),
        Pin.data("data_knowledge_context", "knowledge_context", Colors.blue,
                 "right", pin_type="string"),
    ]

    async def execute(self, ctx: NodeContext):
        import os
        from acai.orchestrator.knowledge import KnowledgeStore

        paths = ctx.inputs.get("paths", [])
        if isinstance(paths, str):
            try:
                paths = json.loads(paths)
            except (json.JSONDecodeError, TypeError):
                paths = [p.strip() for p in paths.split(",") if p.strip()]

        if not isinstance(paths, list):
            paths = []

        knowledge_dir = os.path.join(ctx.graph.config.workspace, "knowledge")
        store = KnowledgeStore(knowledge_dir)

        parts: list[str] = []
        for doc_path in paths[:10]:
            doc = store.get_by_path(str(doc_path))
            if doc and doc.content:
                parts.append(f"### {doc.subject}/{doc.subsubject}/{doc.title}\n\n{doc.content}")
            else:
                log.debug("load_knowledge: %r not found or empty", doc_path)

        knowledge_context = "\n\n---\n\n".join(parts) if parts else ""
        log.info("load_knowledge: loaded %d/%d docs, %d chars",
                 len(parts), len(paths), len(knowledge_context))

        yield {"type": "output", "data": {
            "knowledge_context": knowledge_context,
        }}


@register
class SetVariableNode(NodeType):
    """Store a value under a named key for retrieval by Get Variable nodes.

    Variables are scoped to the current workflow execution and persist
    across all subsequent nodes.  Writing to the same name overwrites
    the previous value.
    """

    type = "set_variable"
    label = "Set Variable"
    accent = Colors.amber
    description = "Store a named variable"
    category = "Data"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_name", "name", Colors.amber, "left",
                 pin_type="string", optional=False),
        Pin.data("data_value", "value", Colors.green, "left",
                 pin_type="any", optional=False),
    ]

    async def execute(self, ctx: NodeContext):
        name = ctx.inputs.get("name", "") or ctx.data.get("name", "")
        value = ctx.inputs.get("value")

        if not name:
            log.warning("SetVariable: empty name, skipping")
            yield {"type": "output", "data": {}}
            return

        store = ctx.work.setdefault("_variables", {})
        store[name] = value

        yield {"type": "event", "data": {
            "event_type": "variable_set",
            "data": {"node_id": ctx.node_id, "name": name,
                     "preview": str(value)[:200]},
        }}
        yield {"type": "output", "data": {}}


@register
class GetVariableNode(NodeType):
    """Retrieve a previously stored variable by name.

    If the variable has not been set, outputs ``None`` (or a fallback
    default if one is wired into the ``default`` pin).
    """

    type = "get_variable"
    label = "Get Variable"
    accent = Colors.amber
    description = "Read a named variable"
    category = "Data"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_name", "name", Colors.amber, "left",
                 pin_type="string", optional=False),
        Pin.data("data_default", "default", Colors.green, "left",
                 pin_type="any"),
        Pin.data("data_value", "value", Colors.green, "right",
                 pin_type="any"),
    ]

    async def execute(self, ctx: NodeContext):
        name = ctx.inputs.get("name", "") or ctx.data.get("name", "")
        default = ctx.inputs.get("default")

        store = ctx.work.get("_variables", {})
        value = store.get(name, default)

        yield {"type": "output", "data": {"value": value}}


@register
class FetchConversationNode(NodeType):
    """Load a conversation by ID — or from the test chat when debugging.

    * **debug = false** (default): reads the conversation specified by
      ``conversation_id`` from ``ChatStore``.
    * **debug = true**: uses the message history accumulated in the
      workflow builder's *Test Chat* panel (passed via
      ``work["test_conversation"]`` at run time).

    Wire the ``conversation`` output into any node that expects a
    ``message_list`` (e.g. ``AgentCall.context``, ``Append.array``).
    """

    type = "fetch_conversation"
    label = "Fetch Conversation"
    accent = Colors.cyan
    description = "Load conversation history"
    category = "Data"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_conversation_id", "conversation_id", Colors.cyan,
                 "left", pin_type="string", dynamic_choices="conversations"),
        Pin.data("data_debug", "debug", Colors.amber, "left",
                 pin_type="bool"),
        Pin.data("data_conversation", "conversation", Colors.blue, "right",
                 pin_type="message_list"),
    ]

    async def execute(self, ctx: NodeContext):
        debug = ctx.inputs.get("debug", ctx.data.get("debug", False))
        if isinstance(debug, str):
            debug = debug.lower() in ("true", "1", "yes")

        if debug:
            conversation = ctx.work.get("test_conversation", [])
            if isinstance(conversation, str):
                conversation = conversation.strip()
                if conversation.startswith("["):
                    try:
                        conversation = json.loads(conversation)
                    except json.JSONDecodeError:
                        conversation = []
                else:
                    conversation = []
        else:
            conv_id = (
                ctx.inputs.get("conversation_id", "")
                or ctx.data.get("conversation_id", "")
            )
            conversation = []
            if conv_id and hasattr(ctx.graph, "chat"):
                conversation = list(ctx.graph.chat.read(conv_id))

        yield {"type": "output", "data": {"conversation": conversation}}


@register
class SkillCallNode(NodeType):
    """Call a registered tool/skill by name.

    The user selects a tool from a dropdown.  The frontend dynamically
    generates input pins for each of the tool's parameters and an output
    pin for the result.  At execution time, all wired parameter inputs
    are collected and the tool is dispatched through the worker.
    """

    type = "skill_call"
    label = "Skill Call"
    accent = "#e06090"
    description = "Call a tool or skill"
    category = "Agent"
    _JSON_TO_PIN = {
        "string": "string", "integer": "int", "number": "float",
        "boolean": "bool", "object": "json", "array": "json",
    }

    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_tool", "tool", "#e06090", "left",
                 pin_type="string", dynamic_choices="tools"),
        Pin.data("data_result", "result", Colors.green, "right",
                 pin_type="string"),
    ]

    @classmethod
    def dynamic_pins(cls, data: dict, spec: dict | None = None, **ctx: Any) -> list[Pin]:
        tool_defs: list[dict] = ctx.get("tool_defs", [])
        tool_name = data.get("tool", "")
        if not tool_name or not tool_defs:
            return []
        for td in tool_defs:
            if td.get("function", {}).get("name") != tool_name:
                continue
            props = td["function"].get("parameters", {}).get("properties", {})
            required = set(td["function"].get("parameters", {}).get("required", []))
            pins: list[Pin] = []
            for name, schema in props.items():
                jtype = schema.get("type", "string")
                pins.append(Pin.data(
                    f"data_{name}", name, Colors.green, "left",
                    pin_type=cls._JSON_TO_PIN.get(jtype, "string"),
                    optional=name not in required,
                ))
            return pins
        return []

    async def execute(self, ctx: NodeContext):
        tool_name = ctx.inputs.get("tool", "") or ctx.data.get("tool", "")
        if not tool_name:
            log.warning("SkillCall: no tool selected")
            yield {"type": "output", "data": {"result": ""}}
            return

        _SKIP = {p.id.removeprefix("data_") for p in self.pins
                 if p.kind == "data"}
        args: dict[str, Any] = {}
        for key, value in ctx.inputs.items():
            if key in _SKIP:
                continue
            args[key] = value

        yield {"type": "event", "data": {
            "event_type": "tool_start",
            "data": {"node_id": ctx.node_id, "tool_name": tool_name, "args": args},
        }}

        try:
            result_text = await ctx.graph.dispatch_tool(tool_name, args)
        except Exception as exc:
            log.exception("SkillCall dispatch error: %s", tool_name)
            result_text = f"[Tool error] {type(exc).__name__}: {exc}"

        yield {"type": "event", "data": {
            "event_type": "tool_end",
            "data": {"node_id": ctx.node_id, "tool_name": tool_name,
                     "result_preview": result_text[:2000]},
        }}

        yield {"type": "output", "data": {"result": result_text}}


@register
class ToolCallNode(SkillCallNode):
    """Alias for SkillCallNode so users can find it as 'Tool Call'."""

    type = "tool_call"
    label = "Tool Call"
    description = "Call a tool"


def _extract_json_text(text: str) -> str:
    """Extract JSON from text, stripping ```json fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


_FIELD_TYPE_MAP = {
    "str": {"type": "string"},
    "string": {"type": "string"},
    "int": {"type": "integer"},
    "integer": {"type": "integer"},
    "float": {"type": "number"},
    "number": {"type": "number"},
    "bool": {"type": "boolean"},
    "boolean": {"type": "boolean"},
    "str[]": {"type": "array", "items": {"type": "string"}},
    "string[]": {"type": "array", "items": {"type": "string"}},
    "int[]": {"type": "array", "items": {"type": "integer"}},
    "integer[]": {"type": "array", "items": {"type": "integer"}},
    "float[]": {"type": "array", "items": {"type": "number"}},
    "number[]": {"type": "array", "items": {"type": "number"}},
    "bool[]": {"type": "array", "items": {"type": "boolean"}},
    "boolean[]": {"type": "array", "items": {"type": "boolean"}},
}


def _fields_to_schema(fields: list[dict]) -> dict:
    """Build an OpenAI-compatible JSON schema from a list of {name, type} dicts."""
    properties: dict = {}
    required: list[str] = []
    for f in fields:
        name = f.get("name", "").strip()
        ftype = f.get("type", "str").strip().lower()
        if not name:
            continue
        properties[name] = _FIELD_TYPE_MAP.get(ftype, {"type": "string"})
        required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


@register
class ReplyTypeNode(NodeType):
    """Define the expected output format for an agent reply.

    The user adds fields (name + type) in the property panel.  At
    execution time a JSON schema is built from those fields and output
    on the ``format`` pin so it can be wired into an AgentCall or
    ReadReply node.
    """

    type = "reply_type"
    label = "Reply Type"
    accent = "#c06cdb"
    description = "Define structured output format"
    category = "Data"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_format", "format", "#c06cdb", "right",
                 pin_type="format"),
    ]

    async def execute(self, ctx: NodeContext):
        fields_raw = ctx.data.get("fields", "[]")
        try:
            fields = json.loads(fields_raw) if isinstance(fields_raw, str) else fields_raw
        except (json.JSONDecodeError, TypeError):
            log.warning("ReplyType: invalid fields data: %.200s", fields_raw)
            fields = []
        schema = _fields_to_schema(fields)
        yield {"type": "output", "data": {"format": schema}}


@register
class ReadReplyNode(NodeType):
    """Parse an agent response according to a Reply Type format.

    Extracts JSON from the response content (handles ````` fences),
    parses it, and exposes each field from the format as a separate
    output pin (dynamically added by the frontend).  Also outputs the
    full parsed dict and raw JSON string.
    """

    type = "read_reply"
    label = "Read Reply"
    accent = "#c06cdb"
    description = "Parse structured agent reply"
    category = "Data"
    _FIELD_TO_PIN = {
        "str": "string", "string": "string",
        "int": "int", "integer": "int",
        "float": "float", "number": "float",
        "bool": "bool", "boolean": "bool",
        "str[]": "json", "string[]": "json",
        "int[]": "json", "integer[]": "json",
        "float[]": "json", "number[]": "json",
        "bool[]": "json", "boolean[]": "json",
    }

    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_reply", "reply", Colors.blue, "left",
                 pin_type="message", optional=False),
        Pin.data("data_reply_type", "reply_type", "#c06cdb", "left",
                 pin_type="format", optional=False),
    ]

    @classmethod
    def dynamic_pins(cls, data: dict, spec: dict | None = None, **ctx: Any) -> list[Pin]:
        if spec is None:
            return []
        node_id = data.get("_node_id", "")
        nodes_by_id = {n["id"]: n for n in spec.get("nodes", [])}
        for edge in spec.get("edges", []):
            if edge.get("target") != node_id:
                continue
            src = nodes_by_id.get(edge.get("source", ""))
            if not src or src.get("type") != "reply_type":
                continue
            try:
                fields = json.loads(
                    src.get("data", {}).get("fields", "[]"))
            except (json.JSONDecodeError, TypeError):
                fields = []
            pins: list[Pin] = []
            for f in fields:
                name = f.get("name", "")
                if not name:
                    continue
                ftype = f.get("type", "str")
                pins.append(Pin.data(
                    f"data_{name}", name, "#c06cdb", "right",
                    pin_type=cls._FIELD_TO_PIN.get(ftype, "string"),
                ))
            return pins
        return []

    async def execute(self, ctx: NodeContext):
        reply = ctx.inputs.get("reply", {})
        content = reply.get("content", "") if isinstance(reply, dict) else str(reply)

        raw_json = _extract_json_text(content)
        try:
            parsed = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            log.warning("ReadReply: could not parse JSON from response: %.200s", raw_json)
            parsed = {}

        out: dict[str, Any] = {}
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                out[key] = value
        yield {"type": "output", "data": out}
