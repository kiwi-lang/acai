"""Tool registry, MCP schema generation, Flask blueprint, and auto-discovery.

Tools are plain public functions inside ``assai.tools.*`` or
``assai.plugins.*`` modules.  The module file name becomes the
namespace (e.g. ``assai/tools/filesystem.py`` → namespace ``filesystem``).

An optional ``@tool`` decorator can annotate constraints::

    from assai.orchestrator.tools import tool

    @tool(gpu=True)
    def heavy_inference(prompt: str) -> str:
        ...

Functions without the decorator are still discovered — the decorator
is only needed for extra metadata.

:func:`discover_tools` imports every submodule of ``assai.tools`` and
``assai.plugins``, introspects public functions, and builds a single
:class:`ToolRegistry`.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
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

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# @tool decorator (lightweight, no registry)
# ---------------------------------------------------------------------------

def tool(*, gpu: bool = False, name: str | None = None):
    """Mark a function with tool constraints.

    This does **not** register the function anywhere — discovery does
    that automatically.  Use this only when you need to override
    defaults (e.g. ``gpu=True``).
    """
    def decorator(fn: Callable) -> Callable:
        fn._tool_meta = {"gpu": gpu, "name": name}
        return fn
    return decorator


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


def _build_tool_def(fn: Callable, namespace: str) -> ToolDef:
    """Build a :class:`ToolDef` from a plain function."""
    meta = getattr(fn, "_tool_meta", {})
    tool_name = meta.get("name") or fn.__name__
    gpu = meta.get("gpu", False)
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

    return ToolDef(
        namespace=namespace,
        name=tool_name,
        qualified_name=qualified,
        description=doc_desc,
        parameters=properties,
        required=required,
        fn=fn,
        gpu=gpu,
    )


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Central registry for discovered tool functions."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}       # qualified_name → ToolDef
        self._namespaces: dict[str, list[str]] = {} # namespace → [qualified_name, …]

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, fn: Callable, namespace: str) -> ToolDef:
        """Register a single function under *namespace*."""
        td = _build_tool_def(fn, namespace)
        self._tools[td.qualified_name] = td
        self._namespaces.setdefault(namespace, [])
        if td.qualified_name not in self._namespaces[namespace]:
            self._namespaces[namespace].append(td.qualified_name)
        fn._tool_def = td
        return td

    def register_module(self, mod, namespace: str | None = None) -> int:
        """Register all public functions defined in *mod*.

        Returns the number of tools registered.  The *namespace*
        defaults to the module name relative to its tool root
        (e.g. ``assai.tools.git`` → ``git``,
        ``assai.plugins.myplugin.extra`` → ``myplugin.extra``).
        """
        if namespace is None:
            namespace = _module_namespace(mod.__name__)

        count = 0
        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            if name.startswith("_"):
                continue
            if getattr(obj, "__module__", None) != mod.__name__:
                continue
            self.register(obj, namespace)
            count += 1
        return count

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
    # FastAPI router
    # ------------------------------------------------------------------

    def router(
        self,
        namespaces: list[str] | None = None,
        url_prefix: str = "/tools",
    ) -> APIRouter:
        """Create a router that exposes each tool as a POST endpoint.

        ``POST <url_prefix>/call`` with JSON body::

            {"tool": "namespace.function_name", "args": { … }}

        ``GET <url_prefix>/list`` returns the MCP definitions.
        """
        rt = APIRouter(prefix=url_prefix, tags=["tools"])
        registry = self

        @rt.get("/list")
        def list_tools(request: Request):
            ns_filter = request.query_params.getlist("namespace") if hasattr(request.query_params, "getlist") else None
            if not ns_filter:
                ns_filter = None
            effective = namespaces if ns_filter is None else ns_filter
            return registry.mcp_definitions(effective)

        @rt.post("/call")
        async def call_tool(request: Request):
            from assai.orchestrator.context import WorkerContext, OrchestratorClient, set_context, reset_context

            try:
                body = await request.json()
            except Exception:
                body = {}
            tool_name = body.get("tool", "")
            args = body.get("args", {})
            ctx_data = body.get("context")

            td = registry.get(tool_name)
            if td is None:
                return JSONResponse({"error": f"unknown tool: {tool_name}"}, status_code=404)
            if namespaces is not None and td.namespace not in namespaces:
                return JSONResponse({"error": f"tool not exposed: {tool_name}"}, status_code=403)

            ctx = None
            if ctx_data and isinstance(ctx_data, dict):
                orch_url = ctx_data.pop("orchestrator_url", "")
                client = OrchestratorClient(orch_url) if orch_url else None
                ctx = WorkerContext(client=client, **ctx_data)

            import asyncio

            def _run_tool():
                token = None
                if ctx is not None:
                    token = set_context(ctx)
                try:
                    return td.fn(**args)
                finally:
                    if token is not None:
                        reset_context(token)

            def _sse(event: str, data: dict) -> str:
                return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            async def _generate():
                try:
                    result = await asyncio.to_thread(_run_tool)
                    yield _sse("result", {"tool": tool_name, "result": result})
                except Exception as exc:
                    yield _sse("error", {"tool": tool_name, "error": str(exc)})
                    return
                yield _sse("done", {})

            return StreamingResponse(_generate(), media_type="text/event-stream")

        return rt

    def blueprint(self, *args, **kwargs):
        """Alias for backward compat — returns an APIRouter."""
        return self.router(*args, **kwargs)

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


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

_TOOL_ROOTS = ("assai.tools.", "assai.plugins.")


def _module_namespace(module_name: str) -> str:
    """Derive a namespace from a fully-qualified module name.

    Strips the known root prefix so that:
    - ``assai.tools.git``             → ``git``
    - ``assai.plugins.myplugin``      → ``myplugin``
    - ``assai.plugins.myplugin.extra`` → ``myplugin.extra``
    """
    for root in _TOOL_ROOTS:
        if module_name.startswith(root):
            return module_name[len(root):]
    return module_name.rsplit(".", 1)[-1]


_SKIP_MODULES = {
    "assai.tools.registry",
}


def _discover_in_package(package, registry: ToolRegistry) -> int:
    """Import submodules of *package* and register public functions."""
    count = 0
    try:
        path = package.__path__
        prefix = package.__name__ + "."
    except AttributeError:
        return 0

    for finder, module_name, is_pkg in pkgutil.iter_modules(path, prefix):
        if module_name in _SKIP_MODULES:
            continue
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            log.debug("skipping %s (import failed)", module_name, exc_info=True)
            continue

        added = registry.register_module(mod)
        if added:
            log.info("discovered %d tools from %s", added, module_name)
        count += added

    return count


def _load_plugins(module, registry):
    n_plugins = 0
    for finder, plugin_name, is_pkg in pkgutil.iter_modules(module.__path__, module.__name__ + "."):
        try:
            plugin_mod = importlib.import_module(plugin_name)
        except Exception:
            log.debug("skipping plugin %s (import failed)", plugin_name, exc_info=True)
            continue

        if is_pkg:
            added = registry.register_module(plugin_mod)
            if added:
                log.info("discovered %d tools from %s", added, plugin_name)
            n_plugins += added
            n_plugins += _discover_in_package(plugin_mod, registry)
        else:
            added = registry.register_module(plugin_mod)
            if added:
                log.info("discovered %d tools from %s", added, plugin_name)
            n_plugins += added
    return n_plugins

def discover_tools(*packages) -> ToolRegistry:
    """Auto-discover tools from built-in modules and plugins.

    Scans:
    1. ``assai.tools.*`` -- built-in tool modules
    2. ``assai.plugins.*`` -- third-party namespace package (pip-installable)

    Every public function (not starting with ``_``) defined in a
    scanned module becomes a tool.  The module's file name is the
    namespace.
    """
    registry = ToolRegistry()

    import assai.tools as tools_pkg
    n_builtin = _discover_in_package(tools_pkg, registry)

    for pkg in packages:
        _discover_in_package(pkg, registry)

    n_plugins = 0
    try:
        import assai.plugins as plugins_pkg

        n_plugins = _load_plugins(plugins_pkg, registry)
    except ImportError:
        pass

    log.info(
        "tool discovery complete: %d built-in + %d plugin tools (%d namespaces)",
        n_builtin, n_plugins, len(registry.namespaces()),
    )
    return registry
