"""Node type registry for DynamicGraph workflows.

Each node type is a subclass of :class:`NodeType`.  Register custom
nodes with the :func:`register` decorator or call it manually.

Built-in types
--------------
start, agent, agent_call, accumulate, stream_transform,
for_each, tool_loop, tool, append, reasoning_message, print,
condition, output, fetch_conversation

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
        if key in ("agent", "context", "stream_mode"):
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
        Pin.data("data_conversation", "conversation", Colors.blue, "right",
                 pin_type="message_list"),
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
        Pin.data("data_text", "text", Colors.green, "right",
                 pin_type="string"),
    ]

    async def execute(self, ctx: NodeContext):  # noqa: C901
        from assai.tasks.graph import Acc

        stream = ctx.inputs.get("stream")
        if stream is None:
            yield {"type": "output", "data": {
                "response": {"role": "assistant", "content": ""},
                "text": "",
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
            "text": acc.text,
        }}



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
class BackgroundAgentNode(NodeType):
    """Silent agent call with tool follow-ups.

    Runs an agent in ``silent`` stream mode, handling tool calls
    internally.  Emits ``{phase}_start``, ``{phase}_tool_start``,
    ``{phase}_tool_end``, and ``{phase}_end`` events so the frontend
    can show progress without streaming tokens.

    Any data-pin input whose key is not ``agent``, ``phase``, ``label``,
    or ``text`` is forwarded as Jinja2 ``extra_context`` to the agent
    template (e.g. ``assistant_response`` for a scribe agent).
    """

    type = "background_agent"
    label = "Background Agent"
    accent = Colors.purple
    description = "Silent agent with tool follow-ups"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_agent", "agent", Colors.cyan, "left", pin_type="string",
                 dynamic_choices="agents"),
        Pin.data("data_phase", "phase", Colors.amber, "left", pin_type="string"),
        Pin.data("data_text", "text", Colors.green, "right", pin_type="string"),
    ]

    _SKIP_KEYS = frozenset({"agent", "phase", "label", "text"})

    async def execute(self, ctx: NodeContext):  # noqa: C901
        from assai.tasks.graph import Acc

        agent_name = (ctx.inputs.get("agent", "")
                      or ctx.data.get("agent", "default"))
        phase = (ctx.inputs.get("phase", "")
                 or ctx.data.get("phase", "background"))

        extra: dict[str, Any] = {}
        for key, value in ctx.data.items():
            if key.startswith("_") or key in self._SKIP_KEYS:
                continue
            extra[key] = value
        for key, value in ctx.inputs.items():
            if key.startswith("_") or key in self._SKIP_KEYS:
                continue
            if isinstance(value, dict) and "content" in value:
                extra[key] = value["content"]
            else:
                extra[key] = value

        yield {"type": "event", "data": {
            "event_type": f"{phase}_start",
            "data": {"agent": agent_name},
        }}

        try:
            payload = ctx.graph.prepare(
                agent_name, ctx.work,
                **({"extra_context": extra} if extra else {}),
            )
        except Exception as exc:
            log.warning("%s prepare failed: %s", phase, exc)
            yield {"type": "event", "data": {
                "event_type": f"{phase}_end",
                "data": {"status": "skipped", "reason": str(exc)},
            }}
            yield {"type": "output", "data": {"text": ""}}
            return

        acc = Acc(ctx.graph.dispatch(payload, stream_mode="silent"))
        try:
            async for _ in acc:
                pass
        except Exception as exc:
            log.warning("%s dispatch failed: %s", phase, exc)
            yield {"type": "event", "data": {
                "event_type": f"{phase}_end",
                "data": {"status": "error", "reason": str(exc)},
            }}
            yield {"type": "output", "data": {"text": ""}}
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

                yield {"type": "event", "data": {
                    "event_type": f"{phase}_tool_start",
                    "data": {"tool_name": tool_name, "args": tool_args},
                }}

                try:
                    result_text = await ctx.graph.dispatch_tool(
                        tool_name, tool_args,
                    )
                except Exception as exc:
                    log.warning("%s tool %s failed: %s", phase, tool_name, exc)
                    result_text = f"[Tool error] {type(exc).__name__}: {exc}"

                yield {"type": "event", "data": {
                    "event_type": f"{phase}_tool_end",
                    "data": {
                        "tool_name": tool_name,
                        "result_preview": result_text[:2000],
                    },
                }}

                followup.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result_text,
                })

            payload = dict(payload, messages=followup)
            acc = Acc(ctx.graph.dispatch(payload, stream_mode="silent"))
            try:
                async for _ in acc:
                    pass
            except Exception as exc:
                log.warning("%s follow-up failed: %s", phase, exc)
                break

        yield {"type": "event", "data": {
            "event_type": f"{phase}_end",
            "data": {"status": "done"},
        }}
        yield {"type": "output", "data": {"text": acc.text}}


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
