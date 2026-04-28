"""Workflow graph type checker.

Walks every node and edge in a workflow spec and reports diagnostics:

* **type_mismatch** — a data edge connects pins with incompatible types.
* **missing_input** — a required (non-optional) input pin has no edge
  and no inline default value.
* **unknown_node_type** — a node's ``type`` is not in the registry.
* **dangling_edge** — an edge references a node id that doesn't exist.
* **missing_exec** — a node that requires exec_in has none connected.
* **orphan_node** — a non-start node has no exec path reaching it.

Each diagnostic is a dict with at least ``severity``, ``code``,
``node_id``, and ``message``.  Edge-related diagnostics also carry
``edge_id``, ``source_node``, ``target_node``, ``source_pin``,
``target_pin``, ``source_type``, ``target_type``.
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any

from acai.tasks.nodes import get as get_node_type

log = logging.getLogger(__name__)

# ── pin-type maps for dynamic nodes ──────────────────────────────

_JSON_TO_PIN: dict[str, str] = {
    "string": "string", "integer": "int", "number": "float",
    "boolean": "bool", "object": "json", "array": "json",
}

_FIELD_TO_PIN: dict[str, str] = {
    "str": "string", "string": "string",
    "int": "int", "integer": "int",
    "float": "float", "number": "float",
    "bool": "bool", "boolean": "bool",
}

# ── helpers ──────────────────────────────────────────────────────


def _pin_name(handle_id: str) -> str:
    return handle_id.removeprefix("data_") if handle_id.startswith("data_") else handle_id


def _resolve_pin_type(
    node_type_name: str,
    handle_id: str,
    node: dict,
    nodes_by_id: dict[str, dict],
    edges: list[dict],
    tool_defs: list[dict] | None = None,
) -> str:
    """Return the pin type for *handle_id* on *node*.

    Checks the static registry first, then falls back to dynamic
    resolution for ``skill_call`` and ``read_reply`` nodes.
    """
    nt = get_node_type(node_type_name)
    if nt is not None:
        pin = next((p for p in nt.pins if p.id == handle_id), None)
        if pin is not None:
            return pin.pin_type

    pin_name = _pin_name(handle_id)
    data = node.get("data") or {}

    if node_type_name == "skill_call" and tool_defs:
        tool_name = data.get("tool", "")
        if tool_name:
            for td in tool_defs:
                if td.get("function", {}).get("name") == tool_name:
                    props = (td["function"]
                             .get("parameters", {})
                             .get("properties", {}))
                    schema = props.get(pin_name)
                    if schema:
                        return _JSON_TO_PIN.get(schema.get("type", ""), "string")

    if node_type_name == "read_reply":
        for edge in edges:
            if edge.get("target") != node.get("id"):
                continue
            src = nodes_by_id.get(edge.get("source", ""))
            if src and src.get("type") == "reply_type":
                try:
                    fields = _json.loads(
                        src.get("data", {}).get("fields", "[]"))
                except (ValueError, TypeError):
                    fields = []
                for f in fields:
                    if f.get("name") == pin_name:
                        return _FIELD_TO_PIN.get(f.get("type", ""), "string")

    return "any"


def _pin_label(
    node_type_name: str, handle_id: str,
) -> str:
    nt = get_node_type(node_type_name)
    if nt is not None:
        pin = next((p for p in nt.pins if p.id == handle_id), None)
        if pin is not None:
            return pin.label or _pin_name(handle_id)
    return _pin_name(handle_id)


def _node_label(node: dict) -> str:
    return (node.get("data") or {}).get("label", node.get("id", "?"))


def _compatible(src_type: str, tgt_type: str) -> bool:
    if src_type == "any" or tgt_type == "any":
        return True
    return src_type == tgt_type


# ── main entry point ─────────────────────────────────────────────

def typecheck(
    spec: dict,
    *,
    tool_defs: list[dict] | None = None,
) -> list[dict]:
    """Run all checks on *spec* and return a list of diagnostics.

    Parameters
    ----------
    spec:
        The workflow JSON (``{nodes, edges}``).
    tool_defs:
        Optional MCP tool definitions (as returned by the
        ``/workflows/tool-definitions`` endpoint).  When provided,
        ``skill_call`` dynamic pins get full type resolution instead
        of falling back to ``any``.
    """
    nodes_list: list[dict] = spec.get("nodes", [])
    edges_list: list[dict] = spec.get("edges", [])
    nodes_by_id: dict[str, dict] = {n["id"]: n for n in nodes_list}

    diags: list[dict] = []

    # ── 1. unknown node types ────────────────────────────────────
    for node in nodes_list:
        ntype = node.get("type", "")
        if not get_node_type(ntype):
            diags.append({
                "severity": "error",
                "code": "unknown_node_type",
                "node_id": node["id"],
                "message": (
                    f"{_node_label(node)}: unknown node type '{ntype}'"
                ),
            })

    # ── 2. dangling edges ────────────────────────────────────────
    for edge in edges_list:
        eid = edge.get("id", "")
        src_id = edge.get("source", "")
        tgt_id = edge.get("target", "")
        if src_id not in nodes_by_id or tgt_id not in nodes_by_id:
            diags.append({
                "severity": "error",
                "code": "dangling_edge",
                "edge_id": eid,
                "node_id": src_id if src_id not in nodes_by_id else tgt_id,
                "message": (
                    f"Edge '{eid}' references missing node "
                    f"'{src_id if src_id not in nodes_by_id else tgt_id}'"
                ),
            })

    # ── 3. type mismatches on data edges ─────────────────────────
    for edge in edges_list:
        if edge.get("type") != "data":
            continue
        eid = edge.get("id", "")
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

        src_pin_type = _resolve_pin_type(
            src_type_name, src_handle, src_node,
            nodes_by_id, edges_list, tool_defs)
        tgt_pin_type = _resolve_pin_type(
            tgt_type_name, tgt_handle, tgt_node,
            nodes_by_id, edges_list, tool_defs)

        if not _compatible(src_pin_type, tgt_pin_type):
            diags.append({
                "severity": "error",
                "code": "type_mismatch",
                "edge_id": eid,
                "node_id": tgt_id,
                "source_node": src_id,
                "target_node": tgt_id,
                "source_pin": _pin_label(src_type_name, src_handle),
                "target_pin": _pin_label(tgt_type_name, tgt_handle),
                "source_type": src_pin_type,
                "target_type": tgt_pin_type,
                "message": (
                    f"{_node_label(src_node)}.{_pin_label(src_type_name, src_handle)} "
                    f"({src_pin_type}) \u2192 "
                    f"{_node_label(tgt_node)}.{_pin_label(tgt_type_name, tgt_handle)} "
                    f"({tgt_pin_type}): incompatible types"
                ),
            })

    # ── 4. required inputs not connected and no inline default ───
    connected_inputs: set[tuple[str, str]] = set()
    for edge in edges_list:
        if edge.get("type") == "data":
            connected_inputs.add(
                (edge.get("target", ""), edge.get("targetHandle", "")))

    for node in nodes_list:
        ntype = node.get("type", "")
        nt = get_node_type(ntype)
        if nt is None:
            continue
        data = node.get("data") or {}
        for pin in nt.pins:
            if pin.kind != "data" or pin.side != "left" or pin.optional:
                continue
            if (node["id"], pin.id) in connected_inputs:
                continue
            pin_key = _pin_name(pin.id)
            if pin_key in data and data[pin_key]:
                continue
            diags.append({
                "severity": "error",
                "code": "missing_input",
                "node_id": node["id"],
                "target_node": node["id"],
                "target_pin": pin.label or pin.id,
                "target_type": pin.pin_type,
                "message": (
                    f"{_node_label(node)}.{pin.label}: "
                    f"required input is not connected"
                ),
            })

    # ── 5. exec connectivity ─────────────────────────────────────
    exec_sources: dict[str, set[str]] = {}
    exec_targets: dict[str, set[str]] = {}
    for edge in edges_list:
        src_h = edge.get("sourceHandle", "")
        if not src_h.startswith("exec_"):
            continue
        src_id = edge.get("source", "")
        tgt_id = edge.get("target", "")
        exec_targets.setdefault(tgt_id, set()).add(src_id)
        exec_sources.setdefault(src_id, set()).add(tgt_id)

    for node in nodes_list:
        ntype = node.get("type", "")
        nt = get_node_type(ntype)
        if nt is None or ntype == "start":
            continue
        has_exec_in = any(
            p.kind == "exec" and p.side == "left" for p in nt.pins)
        if has_exec_in and node["id"] not in exec_targets:
            diags.append({
                "severity": "warning",
                "code": "missing_exec",
                "node_id": node["id"],
                "message": (
                    f"{_node_label(node)}: no exec input connected — "
                    f"node will never execute"
                ),
            })

    # ── 6. orphan detection (BFS from start nodes) ───────────────
    start_ids = {
        n["id"] for n in nodes_list if n.get("type") == "start"
    }
    reachable: set[str] = set(start_ids)
    frontier = list(start_ids)
    while frontier:
        nid = frontier.pop()
        for tgt in exec_sources.get(nid, ()):
            if tgt not in reachable:
                reachable.add(tgt)
                frontier.append(tgt)

    for node in nodes_list:
        if node["id"] in reachable:
            continue
        ntype = node.get("type", "")
        nt = get_node_type(ntype)
        if nt is None:
            continue
        has_exec_in = any(
            p.kind == "exec" and p.side == "left" for p in nt.pins)
        if not has_exec_in:
            continue
        diags.append({
            "severity": "warning",
            "code": "orphan_node",
            "node_id": node["id"],
            "message": (
                f"{_node_label(node)}: unreachable from any Start node"
            ),
        })

    return diags
