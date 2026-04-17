"""DynamicGraph — execute a workflow defined by a JSON spec.

The spec uses **execution pins** (``exec_*``) to control traversal
order and **data pins** (``data_*``) to route typed JSON values
between nodes.

Node types are loaded from the :mod:`assai.tasks.nodes` registry.
See that module for the built-in set and instructions for adding
custom node types.
"""

from __future__ import annotations

import json
import logging
import traceback as _tb
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, AsyncIterator

from assai.tasks.graph import TaskGraph
from assai.tasks import nodes as node_registry
from assai.tasks.nodes import NodeContext

log = logging.getLogger(__name__)


@dataclass
class _ForEachFrame:
    """Call-stack frame for a ForEach node iteration."""
    node_id: str
    items: list
    index: int = 0


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
    """Execute a workflow by following execution pins and resolving
    data pins.

    Node behaviour is delegated to :class:`~assai.tasks.nodes.NodeType`
    instances looked up from the node registry.  Streaming nodes (like
    ``agent``) yield SSE events in real time while the graph accumulates
    their final output for downstream data pins.
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
        final_reasoning = ""
        current_id: str | None = start["id"]
        max_steps = len(spec.nodes) * 4 + 20
        foreach_stack: list[_ForEachFrame] = []

        def _edge_event(edge: dict) -> dict:
            return {
                "event_type": "edge_traversed",
                "data": {
                    "edge_id": edge.get("id", ""),
                    "source": edge["source"],
                    "target": edge["target"],
                    "source_handle": edge.get("sourceHandle", ""),
                },
            }

        for _step in range(max_steps):
            if current_id is None:
                # Dead end — check ForEach call stack
                if foreach_stack:
                    frame = foreach_stack[-1]
                    frame.index += 1
                    if frame.index < len(frame.items):
                        outputs[frame.node_id] = {
                            "item": frame.items[frame.index],
                            "index": frame.index,
                        }
                        nexts = spec.exec_edges(frame.node_id, "exec_body")
                        if nexts:
                            yield _edge_event(nexts[0])
                        current_id = nexts[0]["target"] if nexts else None
                        if current_id is None:
                            foreach_stack.pop()
                        continue
                    else:
                        foreach_stack.pop()
                        nexts = spec.exec_edges(frame.node_id, "exec_then")
                        if nexts:
                            yield _edge_event(nexts[0])
                        current_id = nexts[0]["target"] if nexts else None
                        continue
                break

            node = spec.node(current_id)
            ntype = node.get("type", "")
            data = node.get("data") or {}

            # -- resolve data-pin inputs from upstream outputs -----------
            resolved: dict[str, Any] = {}
            for edge in spec.data_inputs(current_id):
                src_id = edge["source"]
                src_handle = edge.get("sourceHandle", "")
                tgt_handle = edge.get("targetHandle", "")
                pin_name = (tgt_handle.removeprefix("data_")
                            if tgt_handle.startswith("data_") else tgt_handle)
                src_pin = (src_handle.removeprefix("data_")
                           if src_handle.startswith("data_") else src_handle)
                if src_id in outputs and src_pin in outputs[src_id]:
                    resolved[pin_name] = outputs[src_id][src_pin]

            # -- ForEach: handled by the executor, not the node ----------
            if ntype == "for_each":
                array = resolved.get("array", [])
                if not isinstance(array, list):
                    array = list(array) if array else []

                node_label = data.get("label", current_id)
                yield {
                    "event_type": "node_start",
                    "data": {"node_id": current_id, "type": ntype,
                             "label": node_label},
                }

                if not array:
                    outputs[current_id] = {"item": None, "index": 0}
                    self.audit.record(
                        "node.exec", phase="node",
                        node_id=current_id, node_type=ntype,
                        label=node_label, duration_ms=0,
                    )
                    yield {"event_type": "node_end", "data": {
                        "node_id": current_id, "type": ntype,
                        "output_preview": "(empty array)",
                    }}
                    nexts = spec.exec_edges(current_id, "exec_then")
                    if nexts:
                        yield _edge_event(nexts[0])
                    current_id = nexts[0]["target"] if nexts else None
                else:
                    outputs[current_id] = {
                        "item": array[0], "index": 0,
                    }
                    foreach_stack.append(_ForEachFrame(
                        node_id=current_id, items=array, index=0,
                    ))
                    self.audit.record(
                        "node.exec", phase="node",
                        node_id=current_id, node_type=ntype,
                        label=node_label, items=len(array), duration_ms=0,
                    )
                    yield {"event_type": "node_end", "data": {
                        "node_id": current_id, "type": ntype,
                        "output_preview": f"{len(array)} items",
                    }}
                    nexts = spec.exec_edges(current_id, "exec_body")
                    if nexts:
                        yield _edge_event(nexts[0])
                    current_id = nexts[0]["target"] if nexts else None
                continue

            # -- look up node type from registry -------------------------
            node_type = node_registry.get(ntype)
            if node_type is None:
                yield self._error_event(
                    f"Unknown node type '{ntype}' on node '{current_id}'",
                )
                return

            node_label = data.get("label", current_id)
            yield {
                "event_type": "node_start",
                "data": {"node_id": current_id, "type": ntype,
                         "label": node_label},
            }

            ctx = NodeContext(
                graph=self,
                node_id=current_id,
                data=data,
                inputs=resolved,
                work=work,
            )

            # -- execute (iterate the async generator) --------------------
            node_out: dict[str, Any] = {}
            try:
                async with self.audit.aspan(
                    "node", phase="node",
                    node_id=current_id, node_type=ntype, label=node_label,
                ):
                    async for event in node_type.execute(ctx):
                        etype = event.get("type", "")
                        edata = event.get("data", {})
                        if etype == "output":
                            node_out.update(edata)
                        elif etype == "event":
                            evt = edata.get("event_type", "")
                            if evt == "token":
                                final_text += (edata.get("data", {})
                                               .get("token", ""))
                            elif evt == "reasoning":
                                final_reasoning += (edata.get("data", {})
                                                    .get("token", ""))
                            yield edata
            except Exception as exc:
                log.exception("node %s failed", current_id)
                yield self._error_event(
                    f"Node '{current_id}' ({ntype}) failed: {exc}",
                    _tb.format_exc(),
                )
                return

            outputs[current_id] = node_out

            # -- build preview for node_end event ------------------------
            preview = ""
            for v in node_out.values():
                if isinstance(v, str):
                    preview = v[:200]
                    break
                if isinstance(v, (dict, list, int, float, bool)):
                    try:
                        preview = json.dumps(v, ensure_ascii=False)[:200]
                    except (TypeError, ValueError):
                        continue
                    break

            node_end_data: dict[str, Any] = {
                "node_id": current_id, "type": ntype,
                "output_preview": preview,
            }

            if ntype == "output":
                node_end_data["final_output"] = final_text

            yield {"event_type": "node_end", "data": node_end_data}

            # -- output node → stop execution --------------------------------
            if ntype == "output":
                break

            # -- follow exec edge to the next node -----------------------
            if ntype == "condition":
                cond_result = node_out.get("_condition", True)
                handle = "exec_true" if cond_result else "exec_false"
                nexts = spec.exec_edges(current_id, handle)
            else:
                nexts = spec.exec_edges(current_id, "exec_out")

            if nexts:
                yield _edge_event(nexts[0])
            current_id = nexts[0]["target"] if nexts else None

        # -- persist final response to conversation ----------------------
        if final_text and self.conversation:
            msg: dict = {"role": "assistant", "content": final_text}
            if final_reasoning:
                msg["reasoning"] = final_reasoning
            self.chat.append(self.conversation, msg)

        yield {
            "event_type": "workflow_end",
            "data": {"workflow_id": spec.id, "output": final_text[:500]},
        }
        yield self._done_event()
