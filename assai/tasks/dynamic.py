"""DynamicGraph — execute a workflow defined by a JSON spec with dual-pin edges.

The spec uses **execution pins** (``exec_*``) to control traversal order
and **data pins** (``data_*``) to route typed JSON values between nodes.

Data types
----------
* **conversation** (blue)  — ``list[dict]``, array of message objects.
* **message** (amber)      — ``dict``, single message ``{"role": …, "content": …}``.
* **string** (green)       — plain text.
* **reference** (cyan)     — agent/tool name string.

Node types
----------
* **start**     — entry point; outputs ``data_conversation`` (list) and ``data_message`` (dict).
* **agent**     — LLM agent call; inputs ``data_agent`` (str), ``data_context`` (list);
                  outputs ``data_response`` (dict — message object).
* **tool**      — single tool call; inputs ``data_tool`` (str), ``data_input`` (str);
                  outputs ``data_result`` (str).
* **append**    — append item to array; inputs ``data_a`` (list), ``data_b`` (dict);
                  outputs ``data_result`` (list).
* **condition** — branch; input ``data_value`` (any); exec outs ``exec_true`` / ``exec_false``.
* **output**    — terminal; input ``data_response`` (dict — message object or str).

Edge types
----------
* ``"exec"``  — execution flow (handle ids: ``exec_in``, ``exec_out``, ``exec_true``, ``exec_false``).
* ``"data"``  — data flow (handle ids: ``data_<name>``).
"""

from __future__ import annotations

import json
import logging
import re
import traceback as _tb
from collections import defaultdict
from typing import Any, AsyncIterator

from assai.tasks.graph import Acc, TaskGraph

log = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def _substitute(template: str, variables: dict[str, str]) -> str:
    """Replace ``{{name}}`` placeholders with values from *variables*."""
    def _repl(m: re.Match) -> str:
        return variables.get(m.group(1), m.group(0))
    return _VAR_RE.sub(_repl, template)


# ------------------------------------------------------------------
# WorkflowSpec
# ------------------------------------------------------------------

class WorkflowSpec:
    """Parsed and validated workflow specification."""

    __slots__ = ("id", "name", "description", "nodes", "edges",
                 "_node_map", "_adj", "_in_edges")

    def __init__(self, raw: dict):
        self.id: str = raw.get("id", "")
        self.name: str = raw.get("name", self.id)
        self.description: str = raw.get("description", "")

        nodes = raw.get("nodes") or []
        edges = raw.get("edges") or []

        if not nodes:
            raise ValueError("Workflow spec must contain at least one node")

        self.nodes: list[dict] = nodes
        self.edges: list[dict] = edges
        self._node_map: dict[str, dict] = {n["id"]: n for n in nodes}
        self._adj: dict[str, list[dict]] = defaultdict(list)
        self._in_edges: dict[str, list[dict]] = defaultdict(list)

        for edge in edges:
            self._adj[edge["source"]].append(edge)
            self._in_edges[edge["target"]].append(edge)

    def node(self, node_id: str) -> dict:
        return self._node_map[node_id]

    def outgoing(self, node_id: str, handle: str | None = None) -> list[dict]:
        edges = self._adj.get(node_id, [])
        if handle is not None:
            edges = [e for e in edges if e.get("sourceHandle") == handle]
        return edges

    def incoming(self, node_id: str, handle: str | None = None) -> list[dict]:
        edges = self._in_edges.get(node_id, [])
        if handle is not None:
            edges = [e for e in edges if e.get("targetHandle") == handle]
        return edges

    def exec_edges(self, node_id: str, handle: str = "exec_out") -> list[dict]:
        return [e for e in self._adj.get(node_id, [])
                if e.get("type") == "exec" and e.get("sourceHandle") == handle]

    def data_inputs(self, node_id: str) -> list[dict]:
        return [e for e in self._in_edges.get(node_id, [])
                if e.get("type") == "data"]

    def find_start(self) -> dict | None:
        for n in self.nodes:
            if n.get("type") == "start":
                return n
        return None

    @classmethod
    def from_json(cls, text: str) -> WorkflowSpec:
        return cls(json.loads(text))


# ------------------------------------------------------------------
# DynamicGraph
# ------------------------------------------------------------------

class DynamicGraph(TaskGraph):
    """Execute a workflow by following execution pins and resolving data pins.

    The executor walks exec edges from the start node.  At each node it
    resolves data inputs by tracing data edges backward to source nodes'
    stored outputs, executes the node, stores per-handle outputs, then
    follows the exec_out edge to the next node.

    Agent nodes stream their tokens through the SSE channel so the UI
    can display them in real time.  Each agent emits ``agent_token``
    events (with ``node_id`` and ``stream_mode`` to distinguish thinker
    reasoning from replier text).
    """

    async def run(self, work: dict) -> AsyncIterator[dict]:  # noqa: C901
        raw = work.get("workflow_spec") or {}
        if not raw and work.get("workflow_spec_json"):
            try:
                raw = json.loads(work["workflow_spec_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                yield self._error_event(f"Invalid workflow spec JSON: {exc}")
                return

        if not raw:
            yield self._error_event("No workflow_spec provided")
            return

        try:
            spec = WorkflowSpec(raw)
        except (KeyError, ValueError) as exc:
            yield self._error_event(f"Invalid workflow spec: {exc}")
            return

        start = spec.find_start()
        if start is None:
            yield self._error_event("Workflow has no start node")
            return

        yield {
            "event_type": "workflow_start",
            "data": {"workflow_id": spec.id, "name": spec.name,
                     "node_count": len(spec.nodes)},
        }

        outputs: dict[str, dict[str, Any]] = {}
        final_text = ""
        current_id: str | None = start["id"]
        max_steps = len(spec.nodes) * 2 + 10

        for _step in range(max_steps):
            if current_id is None:
                break

            node = spec.node(current_id)
            ntype = node.get("type", "")
            data = node.get("data") or {}

            resolved: dict[str, Any] = {}
            for edge in spec.data_inputs(current_id):
                src_id = edge["source"]
                src_handle = edge.get("sourceHandle", "")
                tgt_handle = edge.get("targetHandle", "")
                pin_name = tgt_handle.removeprefix("data_") if tgt_handle.startswith("data_") else tgt_handle
                src_pin = src_handle.removeprefix("data_") if src_handle.startswith("data_") else src_handle
                if src_id in outputs and src_pin in outputs[src_id]:
                    resolved[pin_name] = outputs[src_id][src_pin]

            yield {
                "event_type": "node_start",
                "data": {"node_id": current_id, "type": ntype,
                         "label": data.get("label", current_id)},
            }

            try:
                if ntype == "agent":
                    node_out = {}
                    async for item in self._exec_agent_stream(
                        current_id, data, resolved, work,
                    ):
                        if isinstance(item, dict) and "event_type" in item:
                            yield item
                        else:
                            node_out = item
                else:
                    node_out = await self._exec_node(ntype, data, resolved, work)
            except Exception as exc:
                log.exception("node %s failed", current_id)
                yield self._error_event(
                    f"Node '{current_id}' ({ntype}) failed: {exc}",
                    _tb.format_exc(),
                )
                return

            outputs[current_id] = node_out

            preview = ""
            for v in node_out.values():
                if v:
                    preview = (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))[:200]
                    break

            node_end_data: dict[str, Any] = {
                "node_id": current_id, "type": ntype,
                "output_preview": preview,
            }

            if ntype == "output":
                resp = resolved.get("response", "")
                if isinstance(resp, dict):
                    node_end_data["final_output"] = resp.get("content", "") or json.dumps(resp, ensure_ascii=False)
                elif isinstance(resp, str):
                    node_end_data["final_output"] = resp
                else:
                    node_end_data["final_output"] = json.dumps(resp, ensure_ascii=False)

            yield {
                "event_type": "node_end",
                "data": node_end_data,
            }

            if ntype == "output":
                resp = resolved.get("response", "")
                if isinstance(resp, dict):
                    final_text = resp.get("content", "") or json.dumps(resp, ensure_ascii=False)
                elif isinstance(resp, str):
                    final_text = resp
                else:
                    final_text = json.dumps(resp, ensure_ascii=False)
                break

            if ntype == "condition":
                cond_result = node_out.get("_condition", True)
                handle = "exec_true" if cond_result else "exec_false"
                nexts = spec.exec_edges(current_id, handle)
            else:
                nexts = spec.exec_edges(current_id, "exec_out")

            current_id = nexts[0]["target"] if nexts else None

        if final_text and self.conversation:
            self.chat.append(self.conversation, {
                "role": "assistant", "content": final_text,
            })

        yield {
            "event_type": "workflow_end",
            "data": {"workflow_id": spec.id, "output": final_text[:500]},
        }
        yield self._done_event()

    # ------------------------------------------------------------------
    # Node executors — each returns {handle_name: value}
    # Values are typed JSON: lists, dicts, or strings.
    # ------------------------------------------------------------------

    async def _exec_node(
        self, ntype: str, data: dict,
        inputs: dict[str, Any], work: dict,
    ) -> dict[str, Any]:
        if ntype == "start":
            return self._exec_start(data, work)
        elif ntype == "tool":
            return await self._exec_tool(data, inputs)
        elif ntype == "append":
            return self._exec_append(inputs)
        elif ntype == "condition":
            return self._exec_condition(data, inputs)
        elif ntype == "output":
            return {}
        else:
            log.warning("Unknown node type %r — passing through", ntype)
            return {}

    # -- start ----------------------------------------------------------

    def _exec_start(self, data: dict, work: dict) -> dict[str, Any]:
        msg_text = work.get("message", "") or data.get("preview_message", "")
        message: dict = {"role": "user", "content": msg_text}

        conv_raw = work.get("conversation_preview", "") or data.get("preview_conversation", "")
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

        if not conversation and self.conversation:
            conversation = list(self.chat.read(self.conversation))

        return {"message": message, "conversation": conversation}

    # -- agent (streaming) -----------------------------------------------

    async def _exec_agent_stream(
        self, node_id: str, data: dict, inputs: dict[str, Any], work: dict,
    ) -> AsyncIterator[dict[str, Any] | dict]:
        """Run an agent node, yielding SSE events for each token/reasoning chunk.

        The ``stream_mode`` field in node data controls whether tokens are
        emitted as ``"token"`` (default) or ``"reasoning"`` events.  This
        allows the think-then-reply pattern where the thinker streams as
        reasoning and the replier streams as tokens.

        Yields SSE event dicts during streaming.  The final yield is the
        node output dict ``{"response": {...}}``.
        """
        agent_name = inputs.get("agent", "") or data.get("agent", "default")
        context = inputs.get("context", [])
        stream_mode = data.get("stream_mode", "token")

        agent_work = dict(work)

        if isinstance(context, list):
            context_str = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}"
                for m in context if isinstance(m, dict)
            ) if context else ""
        else:
            context_str = str(context)

        agent_work["message"] = context_str

        if data.get("prompt_template"):
            variables = {"context": context_str, "input": context_str,
                         "message": work.get("message", "")}
            agent_work["message"] = _substitute(data["prompt_template"], variables)

        payload = self.prepare(agent_name, agent_work)

        if isinstance(context, list) and context:
            payload["messages"] = list(context) + payload.get("messages", [])[-1:]

        acc = Acc(self.dispatch(payload))
        async for event in acc:
            etype = event.get("event_type", "")
            edata = event.get("data", {})

            if etype == "token":
                yield {
                    "event_type": "agent_token",
                    "data": {
                        "node_id": node_id,
                        "token": edata.get("token", ""),
                        "stream_mode": stream_mode,
                    },
                }
            elif etype == "reasoning":
                yield {
                    "event_type": "agent_token",
                    "data": {
                        "node_id": node_id,
                        "token": edata.get("token", ""),
                        "stream_mode": "reasoning",
                    },
                }
            elif etype == "error":
                yield event

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
                    "data": {"node_id": node_id, "tool_name": tool_name,
                             "args": tool_args},
                }

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

                yield {
                    "event_type": "tool_end",
                    "data": {"node_id": node_id, "tool_name": tool_name,
                             "result_preview": result_text[:200]},
                }

            followup_payload = dict(payload)
            followup_payload["messages"] = followup
            payload = followup_payload

            acc = Acc(self.dispatch(followup_payload))
            async for event in acc:
                etype = event.get("event_type", "")
                edata = event.get("data", {})
                if etype == "token":
                    yield {
                        "event_type": "agent_token",
                        "data": {
                            "node_id": node_id,
                            "token": edata.get("token", ""),
                            "stream_mode": stream_mode,
                        },
                    }
                elif etype == "reasoning":
                    yield {
                        "event_type": "agent_token",
                        "data": {
                            "node_id": node_id,
                            "token": edata.get("token", ""),
                            "stream_mode": "reasoning",
                        },
                    }
                elif etype == "error":
                    yield event

        yield {"response": {"role": "assistant", "content": acc.text}}

    # -- tool -----------------------------------------------------------

    async def _exec_tool(self, data: dict, inputs: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(inputs.get("tool", "") or data.get("tool", ""))
        if not tool_name:
            return {"result": "[Error] Tool node has no tool name"}

        raw_args = data.get("args") or {}
        node_input = str(inputs.get("input", ""))
        variables = {"input": node_input}
        args: dict[str, Any] = {}
        for k, v in raw_args.items():
            args[k] = _substitute(str(v), variables) if isinstance(v, str) else v

        try:
            result = await self.dispatch_tool(tool_name, args)
        except Exception as exc:
            log.exception("tool node error: %s", tool_name)
            result = f"[Tool error] {type(exc).__name__}: {exc}"

        return {"result": result}

    # -- append ---------------------------------------------------------

    def _exec_append(self, inputs: dict[str, Any]) -> dict[str, Any]:
        a = inputs.get("a", [])
        b = inputs.get("b", {})
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

    # -- condition ------------------------------------------------------

    def _exec_condition(self, data: dict, inputs: dict[str, Any]) -> dict[str, Any]:
        value = inputs.get("value", "")
        if isinstance(value, (list, dict)):
            input_for_eval = json.dumps(value, ensure_ascii=False)
        else:
            input_for_eval = str(value)
        expression = data.get("expression", "True")
        try:
            result = bool(eval(expression, {"__builtins__": {}},  # noqa: S307
                               {"input": input_for_eval, "value": value, "len": len}))
        except Exception:
            result = True
        return {"_condition": result, "value": value}
