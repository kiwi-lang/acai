"""Meta tools — inspect the tool registry (namespaces, search, descriptions)."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Optional

from acai.orchestrator.tools import tool

if TYPE_CHECKING:
    from acai.orchestrator.tools import ToolRegistry

_registry: Optional["ToolRegistry"] = None


def _configure(registry: "ToolRegistry") -> None:
    """Bind the active :class:`ToolRegistry` (called by the worker / server)."""
    global _registry
    _registry = registry


def _get_registry() -> "ToolRegistry":
    global _registry
    if _registry is not None:
        return _registry
    from acai.orchestrator.tools import discover_tools

    return discover_tools()


@tool(permissions=("read",))
def list_namespaces() -> str:
    """List all tool namespace strings (for picking a subset to enable)."""
    reg = _get_registry()
    return json.dumps({"namespaces": reg.namespaces()})


@tool(permissions=("read",))
def list_tools(namespace: str = "") -> str:
    """List tools, optionally restricted to one namespace.

    Args:
        namespace: If non-empty, only tools in this namespace.
    """
    reg = _get_registry()
    if namespace:
        tools = reg.tools_in(namespace)
    else:
        tools = reg.all_tools()
    out = [
        {
            "qualified_name": t.qualified_name,
            "namespace": t.namespace,
            "description": t.description[:500] if t.description else "",
            "permissions": list(t.permissions),
        }
        for t in tools
    ]
    return json.dumps({"tools": out, "count": len(out)})


@tool(permissions=("read",))
def search_tools(query: str, max_results: int = 12) -> str:
    """Find tools whose name or description matches a keyword (tool discovery).

    Args:
        query: Substring or regex; if it starts with ``regex:``, the rest is a pattern.
        max_results: Maximum tools to return.
    """
    reg = _get_registry()
    q = query.strip()
    use_regex = q.lower().startswith("regex:")
    pattern = q[6:].strip() if use_regex else re.escape(q)
    try:
        rx = re.compile(pattern, re.IGNORECASE if not use_regex else 0)
    except re.error as exc:
        return json.dumps({"error": f"invalid regex: {exc}"})

    hits = []
    for td in reg.all_tools():
        hay = f"{td.qualified_name}\n{td.description or ''}"
        if rx.search(hay):
            hits.append({
                "qualified_name": td.qualified_name,
                "namespace": td.namespace,
                "description": (td.description or "")[:400],
            })
        if len(hits) >= max_results:
            break

    return json.dumps({"query": query, "tools": hits, "count": len(hits)})
