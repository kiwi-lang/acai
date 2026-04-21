"""Node type registry for DynamicGraph workflows.

Each node type is a subclass of :class:`NodeType`.  Register custom
nodes with the :func:`register` decorator or call it manually.

Built-in types
--------------
start, agent, agent_call, accumulate, stream_transform,
for_each, tool_loop, tool, append, reasoning_message, print,
condition, output, fetch_conversation, background_agent

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
                               # | "message" | "message_list" | "any"
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


def validate_workflow(spec: dict) -> list[dict]:
    """Validate a workflow spec and return a list of type errors.

    Each error is a dict with keys: ``edge_id``, ``source_node``,
    ``target_node``, ``source_pin``, ``target_pin``, ``source_type``,
    ``target_type``, ``message``.
    """
    nodes_by_id: dict[str, dict] = {n["id"]: n for n in spec.get("nodes", [])}
    errors: list[dict] = []

    for edge in spec.get("edges", []):
        if edge.get("type") != "data":
            continue

        src_id = edge.get("source", "")
        tgt_id = edge.get("target", "")
        src_handle = edge.get("sourceHandle", "")
        tgt_handle = edge.get("targetHandle", "")

        src_node = nodes_by_id.get(src_id)
        tgt_node = nodes_by_id.get(tgt_id)
        if not src_node or not tgt_node:
            continue

        src_type_name = src_node.get("type", "")
        tgt_type_name = tgt_node.get("type", "")
        src_nt = get(src_type_name)
        tgt_nt = get(tgt_type_name)
        if not src_nt or not tgt_nt:
            continue

        src_pin = next((p for p in src_nt.pins if p.id == src_handle), None)
        tgt_pin = next((p for p in tgt_nt.pins if p.id == tgt_handle), None)
        if not src_pin or not tgt_pin:
            continue

        if not pin_types_compatible(src_pin.pin_type, tgt_pin.pin_type):
            src_label = src_node.get("data", {}).get("label", src_id)
            tgt_label = tgt_node.get("data", {}).get("label", tgt_id)
            errors.append({
                "edge_id": edge.get("id", ""),
                "source_node": src_id,
                "target_node": tgt_id,
                "source_pin": src_pin.label or src_handle,
                "target_pin": tgt_pin.label or tgt_handle,
                "source_type": src_pin.pin_type,
                "target_type": tgt_pin.pin_type,
                "message": (
                    f"{src_label}.{src_pin.label} ({src_pin.pin_type}) "
                    f"\u2192 {tgt_label}.{tgt_pin.label} ({tgt_pin.pin_type}): "
                    f"incompatible types"
                ),
            })

    connected_inputs: set[tuple[str, str]] = set()
    for edge in spec.get("edges", []):
        if edge.get("type") == "data":
            connected_inputs.add((edge.get("target", ""), edge.get("targetHandle", "")))

    for node in spec.get("nodes", []):
        nt = get(node.get("type", ""))
        if not nt:
            continue
        for pin in nt.pins:
            if pin.kind != "data" or pin.side != "left" or pin.optional:
                continue
            if (node["id"], pin.id) not in connected_inputs:
                label = node.get("data", {}).get("label", node["id"])
                errors.append({
                    "edge_id": "",
                    "source_node": "",
                    "target_node": node["id"],
                    "source_pin": "",
                    "target_pin": pin.label or pin.id,
                    "source_type": "",
                    "target_type": pin.pin_type,
                    "message": (
                        f"{label}.{pin.label}: required input is not connected"
                    ),
                })

    return errors


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
        message: dict = {}
        if reasoning:
            message = {
                "role": "system",
                "content": (
                    "## Prior Reasoning\n"
                    "The following analysis was produced about this task. "
                    "Use it to inform your response.\n\n"
                    + reasoning
                ),
            }
        yield {"type": "output", "data": message}


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
                 pin_type="stream", optional=False),
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


def _parse_curator_output(text: str) -> list[dict]:
    """Extract the documents list from curator JSON output."""
    
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        log.warning("Curator output is not valid JSON: %.200s", text)
        return []

    if isinstance(parsed, dict):
        docs = parsed.get("documents", [])
    elif isinstance(parsed, list):
        docs = parsed
    else:
        return []

    return [d for d in docs if isinstance(d, dict) and d.get("content")]


@register
class ParseKnowledgeNode(NodeType):
    """Parse curator JSON output into a formatted knowledge context.

    Takes the raw text from a curator agent and extracts the
    ``documents`` list.  Outputs a markdown-formatted
    ``knowledge_context`` string suitable for injection into an
    agent template.
    """

    type = "parse_knowledge"
    label = "Parse Knowledge"
    accent = Colors.cyan
    description = "Curator output → knowledge context"
    category = "Data"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_text", "text", Colors.green, "left", pin_type="string",
                 optional=False),
        Pin.data("data_knowledge_context", "knowledge_context", Colors.blue,
                 "right", pin_type="string"),
    ]

    async def execute(self, ctx: NodeContext):
        text = ctx.inputs.get("text", "")

        print(text)

        if isinstance(text, str):
            docs = _parse_curator_output(text)
        else:
            docs = text.get("documents")

        knowledge_context = ""
        if docs:
            parts = []
            for doc in docs:
                title = doc.get("title", "Untitled")
                body = doc.get("content", "")
                parts.append(f"### {title}\n\n{body}")
            knowledge_context = "\n\n---\n\n".join(parts)

        yield {"type": "output", "data": {
            "knowledge_context": knowledge_context,
        }}


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
