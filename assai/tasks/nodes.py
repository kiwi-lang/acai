"""Node type registry for DynamicGraph workflows.

Each node type is a subclass of :class:`NodeType`.  Register custom
nodes with the :func:`register` decorator or call it manually.

Built-in types
--------------
start, agent, agent_call, accumulate, stream_transform,
for_each, tool_loop, tool, append, print, condition, output,
fetch_conversation

Creating a custom node
----------------------
::

    from assai.tasks.nodes import NodeType, Pin, NodeContext, register

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
    from assai.tasks.dynamic import DynamicGraph

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
        if key in ("agent", "context", "reasoning", "stream_mode"):
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
    pins = [
        Pin.exec_out(),
        Pin.data("data_message", "message", Colors.amber, "right",
                 pin_type="message"),
    ]

    async def execute(self, ctx: NodeContext):
        msg_text = (ctx.work.get("message", "")
                    or ctx.data.get("preview_message", ""))
        message: dict = {"role": "user", "content": msg_text}
        yield {"type": "output", "data": {"message": message}}


@register
class AgentNode(NodeType):
    """LLM agent call — outputs a token stream.

    Dispatches to the worker, collects tokens via ``Acc``, and
    handles tool-call follow-ups internally.  The token events are
    pushed to the user in real time by ``dispatch()`` / tracker.
    The only data output is ``stream``: a list of token-event dicts.
    """

    type = "agent"
    label = "Agent"
    accent = Colors.blue
    description = "LLM agent call"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_agent", "agent", Colors.cyan, "left", pin_type="string",
                 dynamic_choices="agents"),
        Pin.data("data_context", "context", Colors.blue, "left",
                 pin_type="message_list"),
        Pin.data("data_stream", "stream", Colors.green, "right",
                 pin_type="stream[string]"),
    ]

    async def execute(self, ctx: NodeContext):  # noqa: C901
        from assai.tasks.graph import Acc

        agent_name = (ctx.inputs.get("agent", "")
                      or ctx.data.get("agent", "default"))
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
        payload = ctx.graph.prepare(
            agent_name, agent_work,
            **({"extra_context": extra} if extra else {}),
        )

        if isinstance(context, list) and context:
            payload["messages"] = (
                list(context) + payload.get("messages", [])[-1:]
            )

        acc = Acc(ctx.graph.dispatch(payload))
        async for event in acc:
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

                try:
                    result_text = await ctx.graph.dispatch_tool(
                        tool_name, tool_args,
                    )
                except Exception as exc:
                    log.exception("tool dispatch error: %s", tool_name)
                    result_text = (
                        f"[Tool error] {type(exc).__name__}: {exc}"
                    )

                followup.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result_text,
                })

            followup_payload = dict(payload)
            followup_payload["messages"] = followup
            payload = followup_payload

            acc = Acc(ctx.graph.dispatch(followup_payload))
            async for event in acc:
                yield {"type": "event", "data": event}

        yield {"type": "output", "data": {
            "stream": {"text": acc.text, "reasoning": acc.reasoning,
                       "tool_calls": acc.tool_calls, "payload": payload},
        }}


@register
class ForwardNode(NodeType):
    """Forward a token stream to the user via tracker.

    Pushes each token as an SSE event through the stream tracker.
    The ``mode`` field controls display: ``"token"`` (default) for a
    regular reply bubble, ``"reasoning"`` for a collapsible thinking
    bubble.
    """

    type = "forward"
    label = "Forward"
    accent = Colors.purple
    description = "Stream to user"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_stream", "stream", Colors.green, "left",
                 pin_type="stream", optional=False),
    ]

    async def execute(self, ctx: NodeContext):
        stream = ctx.inputs.get("stream")
        if stream is None:
            yield {"type": "output", "data": {}}
            return
        async for event in stream:
            yield {"type": "event", "data": event}
        yield {"type": "output", "data": {}}


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
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_agent", "agent", Colors.cyan, "left", pin_type="string",
                 dynamic_choices="agents"),
        Pin.data("data_context", "context", Colors.blue, "left",
                 pin_type="message_list"),
        Pin.data("data_reasoning", "reasoning", Colors.purple, "left",
                 pin_type="string"),
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
        reasoning = ctx.inputs.get("reasoning", "")
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
        if reasoning:
            prepare_kwargs["reasoning"] = reasoning
        if extra:
            prepare_kwargs["extra_context"] = extra
        payload = ctx.graph.prepare(agent_name, agent_work, **prepare_kwargs)

        if isinstance(context, list) and context:
            payload["messages"] = (
                list(context) + payload.get("messages", [])[-1:]
            )

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
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_stream", "stream", Colors.green, "left",
                 pin_type="stream", optional=False),
        Pin.data("data_response", "response", Colors.amber, "right",
                 pin_type="message"),
        Pin.data("data_reasoning", "reasoning", Colors.purple, "right",
                 pin_type="string"),
    ]

    async def execute(self, ctx: NodeContext):
        from assai.tasks.graph import Acc

        stream = ctx.inputs.get("stream")
        if stream is None:
            yield {"type": "output", "data": {
                "response": {"role": "assistant", "content": ""},
                "reasoning": "",
            }}
            return

        acc = Acc(stream)
        async for event in acc:
            yield {"type": "event", "data": event}

        yield {"type": "output", "data": {
            "response": {"role": "assistant", "content": acc.text},
            "reasoning": acc.reasoning,
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
class ForEachNode(NodeType):
    """Iterate over an array, firing the body exec pin per item.

    Execution logic is handled by
    :class:`~assai.tasks.dynamic.DynamicGraph` — see the call-stack
    mechanism there.  This node only declares pins.
    """

    type = "for_each"
    label = "For Each"
    accent = Colors.amber
    description = "Loop over array"
    pins = [
        Pin.exec_in(),
        Pin.exec("exec_body", "body", Colors.green, "right"),
        Pin.exec("exec_then", "then", Colors.white, "right"),
        Pin.data("data_array", "array", Colors.blue, "left", pin_type="json",
                 optional=False),
        Pin.data("data_item", "item", Colors.green, "right", pin_type="any"),
        Pin.data("data_index", "index", Colors.amber, "right", pin_type="int"),
    ]

    async def execute(self, ctx: NodeContext):
        yield {"type": "output", "data": {}}
        return  # noqa: B901


@register
class ToolLoopNode(NodeType):
    """Stream-transformer: handle tool calls from an agent reply.

    Takes the bundled output of :class:`AgentCallNode` on the
    ``stream`` pin.  If there are tool calls, dispatches them and
    re-calls the agent in a loop.  Follow-up tokens are pushed to
    the user via ``dispatch()`` / tracker automatically.

    Conceptually: **stream in -> (tool handling) -> stream out**.
    """

    type = "tool_loop"
    label = "Tool Loop"
    accent = Colors.amber
    description = "Handle tool calls"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_stream", "stream", Colors.green, "left",
                 pin_type="stream", optional=False),
        Pin.data("data_payload", "payload", Colors.blue, "left",
                 pin_type="json", optional=False),
        Pin.data("data_response", "response", Colors.amber, "right",
                 pin_type="message"),
    ]

    async def execute(self, ctx: NodeContext):  # noqa: C901
        from assai.tasks.graph import Acc

        stream = ctx.inputs.get("stream")
        if stream is None:
            yield {"type": "output", "data": {
                "response": {"role": "assistant", "content": ""},
            }}
            return

        acc = Acc(stream)
        async for event in acc:
            yield {"type": "event", "data": event}

        payload = ctx.inputs.get("payload", {})
        while acc.tool_calls:
            followup = list(payload.get("messages", []))
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

                followup.append({
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

            payload = dict(payload, messages=followup)
            acc = Acc(ctx.graph.dispatch(payload))
            async for event in acc:
                yield {"type": "event", "data": event}

        yield {"type": "output", "data": {
            "response": {"role": "assistant", "content": acc.text},
        }}


@register
class ToolNode(NodeType):
    type = "tool"
    label = "Tool"
    accent = Colors.amber
    description = "Single tool call"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_tool", "tool", Colors.cyan, "left", pin_type="string",
                 optional=False),
        Pin.data("data_input", "input", Colors.green, "left", pin_type="string"),
        Pin.data("data_result", "result", Colors.green, "right",
                 pin_type="string"),
    ]

    async def execute(self, ctx: NodeContext):
        tool_name = str(
            ctx.inputs.get("tool", "") or ctx.data.get("tool", ""),
        )
        if not tool_name:
            yield {"type": "output", "data": {
                "result": "[Error] Tool node has no tool name",
            }}
            return

        raw_args = ctx.data.get("args") or {}
        node_input = str(ctx.inputs.get("input", ""))
        variables = {"input": node_input}
        args: dict[str, Any] = {}
        for k, v in raw_args.items():
            args[k] = substitute(str(v), variables) if isinstance(v, str) else v

        try:
            result = await ctx.graph.dispatch_tool(tool_name, args)
        except Exception as exc:
            log.exception("tool node error: %s", tool_name)
            result = f"[Tool error] {type(exc).__name__}: {exc}"

        yield {"type": "output", "data": {"result": result}}


@register
class AppendNode(NodeType):
    type = "append"
    label = "Append"
    accent = Colors.purple
    description = "Append item to array"
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
class ConditionNode(NodeType):
    type = "condition"
    label = "Condition"
    accent = Colors.red
    description = "Branch on expression"
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
