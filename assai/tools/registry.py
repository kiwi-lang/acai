"""Tool registry with ``@tool`` decorator, MCP schema generation, and Flask blueprint factory.

Tools are Python functions decorated with ``@tool("namespace")``.  The
registry generates MCP-compatible JSON definitions from type annotations
and docstrings, can expose them via a Flask blueprint, and lets callers
invoke them by qualified name (``namespace.function_name``).

Example::

    from assai.tools.registry import ToolRegistry

    registry = ToolRegistry()

    @registry.tool("filesystem")
    def read_file(path: str) -> str:
        \"\"\"Read the contents of a file.

        Args:
            path: The file path to read.
        \"\"\"
        with open(path) as f:
            return f.read()

    # Generate MCP definitions for a subset of namespaces
    defs = registry.mcp_definitions(namespaces=["filesystem"])

    # Create a Flask blueprint exposing all tools
    bp = registry.blueprint()
"""

from __future__ import annotations

import inspect
import json
import re
import textwrap
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Optional,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from flask import Blueprint, jsonify, request as flask_request


# ---------------------------------------------------------------------------
# Type → JSON Schema mapping
# ---------------------------------------------------------------------------

_PRIMITIVE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _type_to_schema(tp: Any) -> dict:
    """Convert a Python type annotation to a JSON Schema fragment."""
    if tp in _PRIMITIVE_MAP:
        return {"type": _PRIMITIVE_MAP[tp]}

    origin = get_origin(tp)
    args = get_args(tp)

    # Optional[X] → nullable schema for X
    if origin is Union and len(args) == 2 and type(None) in args:
        inner = args[0] if args[1] is type(None) else args[1]
        return _type_to_schema(inner)

    if origin is list:
        items = _type_to_schema(args[0]) if args else {}
        return {"type": "array", "items": items}

    if origin is dict:
        return {"type": "object"}

    return {"type": "string"}


# ---------------------------------------------------------------------------
# Docstring → parameter descriptions
# ---------------------------------------------------------------------------

_ARG_RE = re.compile(r"^\s{4,}(\w+)\s*(?:\(.+?\))?\s*:\s*(.+)", re.MULTILINE)


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """Return ``(description, {param_name: param_description})``."""
    if not doc:
        return "", {}

    doc = textwrap.dedent(doc).strip()
    sections = re.split(r"\n\s*(?:Args|Arguments|Parameters)\s*:\s*\n", doc, maxsplit=1)
    description = sections[0].strip()

    param_descs: dict[str, str] = {}
    if len(sections) > 1:
        for m in _ARG_RE.finditer(sections[1]):
            param_descs[m.group(1)] = m.group(2).strip()

    return description, param_descs


# ---------------------------------------------------------------------------
# ToolDef
# ---------------------------------------------------------------------------

@dataclass
class ToolDef:
    namespace: str
    name: str
    qualified_name: str
    description: str
    parameters: dict        # JSON Schema ``properties``
    required: list[str]
    fn: Callable
    gpu: bool = False


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Central registry for ``@tool``-decorated functions."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}       # qualified_name → ToolDef
        self._namespaces: dict[str, list[str]] = {} # namespace → [qualified_name, …]

    # ------------------------------------------------------------------
    # Decorator
    # ------------------------------------------------------------------

    def tool(self, namespace: str, *, gpu: bool = False, name: str | None = None):
        """Register a function as a tool under *namespace*.

        Parameters
        ----------
        namespace:
            Logical grouping (e.g. ``"filesystem"``, ``"shell"``).
        gpu:
            If ``True`` the worker must free GPU before running this tool.
        name:
            Override the tool name (defaults to ``fn.__name__``).
        """

        def decorator(fn: Callable) -> Callable:
            tool_name = name or fn.__name__
            qualified = f"{namespace}.{tool_name}"

            hints = get_type_hints(fn)
            sig = inspect.signature(fn)
            doc_desc, param_descs = _parse_docstring(fn.__doc__)

            properties: dict[str, Any] = {}
            required: list[str] = []

            for pname, param in sig.parameters.items():
                if pname in ("self", "cls"):
                    continue

                tp = hints.get(pname, str)

                origin = get_origin(tp)
                args = get_args(tp)
                is_optional = (
                    origin is Union
                    and len(args) == 2
                    and type(None) in args
                )

                schema = _type_to_schema(tp)
                if pname in param_descs:
                    schema["description"] = param_descs[pname]

                properties[pname] = schema

                if param.default is inspect.Parameter.empty and not is_optional:
                    required.append(pname)

            td = ToolDef(
                namespace=namespace,
                name=tool_name,
                qualified_name=qualified,
                description=doc_desc,
                parameters=properties,
                required=required,
                fn=fn,
                gpu=gpu,
            )

            self._tools[qualified] = td
            self._namespaces.setdefault(namespace, []).append(qualified)

            fn._tool_def = td
            return fn

        return decorator

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, qualified_name: str) -> ToolDef | None:
        return self._tools.get(qualified_name)

    def namespaces(self) -> list[str]:
        return sorted(self._namespaces.keys())

    def tools_in(self, namespace: str) -> list[ToolDef]:
        return [self._tools[qn] for qn in self._namespaces.get(namespace, [])]

    def all_tools(self) -> list[ToolDef]:
        return list(self._tools.values())

    # ------------------------------------------------------------------
    # Call
    # ------------------------------------------------------------------

    def call(self, qualified_name: str, args: dict[str, Any]) -> Any:
        """Invoke a registered tool by its qualified name."""
        td = self._tools.get(qualified_name)
        if td is None:
            raise KeyError(f"unknown tool: {qualified_name}")
        return td.fn(**args)

    # ------------------------------------------------------------------
    # MCP definition generation
    # ------------------------------------------------------------------

    def mcp_definitions(self, namespaces: list[str] | None = None) -> list[dict]:
        """Return MCP-compatible tool definitions.

        If *namespaces* is ``None`` all tools are included; otherwise only
        tools whose namespace appears in the list.
        """
        defs: list[dict] = []
        for td in self._tools.values():
            if namespaces is not None and td.namespace not in namespaces:
                continue
            defs.append({
                "type": "function",
                "function": {
                    "name": td.qualified_name,
                    "description": td.description,
                    "parameters": {
                        "type": "object",
                        "properties": td.parameters,
                        "required": td.required,
                    },
                },
            })
        return defs

    # ------------------------------------------------------------------
    # Flask blueprint
    # ------------------------------------------------------------------

    def blueprint(
        self,
        namespaces: list[str] | None = None,
        url_prefix: str = "/tools",
    ) -> Blueprint:
        """Create a Flask blueprint that exposes each tool as a POST endpoint.

        ``POST <url_prefix>/call`` with JSON body::

            {"tool": "namespace.function_name", "args": { … }}

        ``GET <url_prefix>/list`` returns the MCP definitions.
        """
        bp = Blueprint("tools", __name__, url_prefix=url_prefix)
        registry = self

        @bp.route("/list", methods=["GET"])
        def list_tools():
            ns_filter = flask_request.args.getlist("namespace") or None
            effective = namespaces if ns_filter is None else ns_filter
            return jsonify(registry.mcp_definitions(effective))

        @bp.route("/call", methods=["POST"])
        def call_tool():
            body = flask_request.get_json(silent=True) or {}
            tool_name = body.get("tool", "")
            args = body.get("args", {})

            td = registry.get(tool_name)
            if td is None:
                return jsonify({"error": f"unknown tool: {tool_name}"}), 404
            if namespaces is not None and td.namespace not in namespaces:
                return jsonify({"error": f"tool not exposed: {tool_name}"}), 403

            try:
                result = td.fn(**args)
                return jsonify({"result": result})
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        return bp

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge(self, other: ToolRegistry) -> None:
        """Import all tools from *other* into this registry."""
        for qn, td in other._tools.items():
            self._tools[qn] = td
            self._namespaces.setdefault(td.namespace, [])
            if qn not in self._namespaces[td.namespace]:
                self._namespaces[td.namespace].append(qn)
