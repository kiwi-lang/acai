"""Node type registry for DynamicGraph workflows.

Each node type is a subclass of :class:`NodeType`.  Register custom
nodes with the :func:`register` decorator or call it manually.

Built-in types
--------------
start, agent, tool, append, condition, output

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
            Pin.data("data_input", "input", Colors.green, "left"),
            Pin.data("data_output", "output", Colors.green, "right"),
        ]

        async def execute(self, ctx: NodeContext) -> dict[str, Any]:
            value = ctx.inputs.get("input", "")
            return {"output": do_something(value)}
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, TYPE_CHECKING

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
    side: str   # "left" | "right"
    kind: str   # "exec" | "data"

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "color": self.color,
                "side": self.side, "kind": self.kind}

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
    def data(id: str, label: str, color: str, side: str) -> Pin:
        return Pin(id, label, color, side, "data")


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
    * ``streaming``   — if *True* the executor calls :meth:`execute_stream`.
    """

    type: str = ""
    label: str = ""
    accent: str = "#888"
    description: str = ""
    pins: list[Pin] = []
    streaming: bool = False

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        """Execute the node.  Return ``{pin_name: value}``."""
        return {}

    async def execute_stream(
        self, ctx: NodeContext,
    ) -> AsyncIterator[dict]:
        """For streaming nodes: yield SSE event dicts, then yield the
        output ``{pin_name: value}`` dict as the final item (it will
        NOT have an ``event_type`` key)."""
        yield await self.execute(ctx)  # default: non-streaming fallback

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
        Pin.data("data_conversation", "conversation", Colors.blue, "right"),
        Pin.data("data_message", "message", Colors.amber, "right"),
    ]

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        msg_text = (ctx.work.get("message", "")
                    or ctx.data.get("preview_message", ""))
        message: dict = {"role": "user", "content": msg_text}

        conv_raw = (ctx.work.get("conversation_preview", "")
                    or ctx.data.get("preview_conversation", ""))
        conversation: list[dict] = []

        if conv_raw:
            if isinstance(conv_raw, list):
                conversation = conv_raw
            elif isinstance(conv_raw, str):
                conv_raw = conv_raw.strip()
                if conv_raw.startswith("["):
                    try:
                        conversation = json.loads(conv_raw)
                    except json.JSONDecodeError:
                        conversation = [{"role": "user", "content": conv_raw}]
                elif conv_raw:
                    conversation = [{"role": "user", "content": conv_raw}]

        if not conversation and ctx.graph.conversation:
            conversation = list(ctx.graph.chat.read(ctx.graph.conversation))

        return {"message": message, "conversation": conversation}


@register
class AgentNode(NodeType):
    """LLM agent call — outputs a token stream.

    The agent does NOT send tokens to the client.  Its only data
    output is ``stream``: a list of token-event dicts collected
    during execution.  Wire the stream into an :class:`AccumulateNode`
    to forward tokens to the user and produce a usable response.
    """

    type = "agent"
    label = "Agent"
    accent = Colors.blue
    description = "LLM agent call"
    streaming = True
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_agent", "agent", Colors.cyan, "left"),
        Pin.data("data_context", "context", Colors.blue, "left"),
        Pin.data("data_stream", "stream", Colors.green, "right"),
    ]

    async def execute_stream(  # noqa: C901
        self, ctx: NodeContext,
    ) -> AsyncIterator[dict]:
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

        payload = ctx.graph.prepare(agent_name, agent_work)

        if isinstance(context, list) and context:
            payload["messages"] = (
                list(context) + payload.get("messages", [])[-1:]
            )

        stream_events: list[dict] = []

        async def _collect(a: Acc) -> None:
            async for event in a:
                etype = event.get("event_type", "")
                edata = event.get("data", {})
                if etype in ("token", "reasoning"):
                    stream_events.append({
                        "token": edata.get("token", ""),
                        "mode": etype,
                    })

        acc = Acc(ctx.graph.dispatch(payload))
        await _collect(acc)

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

                yield {
                    "event_type": "tool_start",
                    "data": {"node_id": ctx.node_id,
                             "tool_name": tool_name, "args": tool_args},
                }

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

                yield {
                    "event_type": "tool_end",
                    "data": {"node_id": ctx.node_id,
                             "tool_name": tool_name,
                             "result_preview": result_text[:200]},
                }

            followup_payload = dict(payload)
            followup_payload["messages"] = followup
            payload = followup_payload

            acc = Acc(ctx.graph.dispatch(followup_payload))
            await _collect(acc)

        yield {"stream": stream_events}


@register
class ForwardNode(NodeType):
    """Forward a token stream to the user.

    Sends each token to the chat UI as SSE events.  The ``mode``
    field controls display: ``"token"`` (default) for a regular
    reply bubble, ``"reasoning"`` for a collapsible thinking bubble.

    Wire the agent's ``stream`` pin to both this node and an
    :class:`AccumulateNode` in parallel — Forward handles display,
    Accumulate handles data.
    """

    type = "forward"
    label = "Forward"
    accent = Colors.purple
    description = "Stream to user"
    streaming = True
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_stream", "stream", Colors.green, "left"),
    ]

    async def execute_stream(
        self, ctx: NodeContext,
    ) -> AsyncIterator[dict]:
        events = ctx.inputs.get("stream", [])
        if not isinstance(events, list):
            events = []
        mode = ctx.data.get("mode", "token")

        for ev in events:
            token = ev.get("token", "") if isinstance(ev, dict) else ""
            if token:
                yield {
                    "event_type": "agent_token",
                    "data": {"node_id": ctx.node_id,
                             "token": token,
                             "stream_mode": mode},
                }

        yield {}


@register
class AccumulateNode(NodeType):
    """Accumulate a token stream into a response message.

    Collects all tokens from the stream and outputs a single
    ``{"role": "assistant", "content": "..."}`` message.
    Does not send anything to the client — pair with
    :class:`ForwardNode` if you also want to display the stream.
    """

    type = "accumulate"
    label = "Accumulate"
    accent = Colors.green
    description = "Stream to response"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_stream", "stream", Colors.green, "left"),
        Pin.data("data_response", "response", Colors.amber, "right"),
    ]

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        events = ctx.inputs.get("stream", [])
        if not isinstance(events, list):
            events = []
        text = ""
        for ev in events:
            token = ev.get("token", "") if isinstance(ev, dict) else ""
            text += token
        return {"response": {"role": "assistant", "content": text}}


@register
class ToolNode(NodeType):
    type = "tool"
    label = "Tool"
    accent = Colors.amber
    description = "Single tool call"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_tool", "tool", Colors.cyan, "left"),
        Pin.data("data_input", "input", Colors.green, "left"),
        Pin.data("data_result", "result", Colors.green, "right"),
    ]

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        tool_name = str(
            ctx.inputs.get("tool", "") or ctx.data.get("tool", ""),
        )
        if not tool_name:
            return {"result": "[Error] Tool node has no tool name"}

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

        return {"result": result}


@register
class AppendNode(NodeType):
    type = "append"
    label = "Append"
    accent = Colors.purple
    description = "Append item to array"
    pins = [
        Pin.exec_in(),
        Pin.exec_out(),
        Pin.data("data_a", "array", Colors.blue, "left"),
        Pin.data("data_b", "item", Colors.amber, "left"),
        Pin.data("data_result", "result", Colors.blue, "right"),
    ]

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
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
        return {"result": result}


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
        Pin.data("data_value", "value", Colors.green, "left"),
    ]

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
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
        return {"_condition": result, "value": value}


@register
class OutputNode(NodeType):
    type = "output"
    label = "Output"
    accent = Colors.cyan
    description = "Final response"
    pins = [
        Pin.exec_in(),
        Pin.data("data_response", "response", Colors.amber, "left"),
    ]

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        return {}
