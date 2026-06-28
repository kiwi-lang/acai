"""Tests for acai.orchestrator.tools — ToolRegistry, discovery, and schema generation."""

from __future__ import annotations

import importlib
import json
import types
from typing import Optional, Union
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from acai.orchestrator.tools import (
    _parse_docstring,
    _type_to_schema,
    _parse_scope,
    _ns_matches,
    _build_tool_def,
    _module_namespace,
    _discover_in_package,
    _load_plugins,
    discover_tools,
    tool,
    ToolDef,
    ToolRegistry,
    VALID_PERMISSIONS,
    VALID_SCOPES,
)


class TestParseScope:

    def test_project_scope(self):
        assert _parse_scope("project:workflow_id") == ("project", "workflow_id")

    def test_global_scope(self):
        assert _parse_scope("global") == ("global", "")

    def test_empty_string(self):
        assert _parse_scope("") == ("global", "")

    def test_invalid_level_defaults_global(self):
        assert _parse_scope("unknown:key") == ("global", "")


class TestTypeToSchema:

    def test_str(self):
        assert _type_to_schema(str) == {"type": "string"}

    def test_int(self):
        assert _type_to_schema(int) == {"type": "integer"}

    def test_float(self):
        assert _type_to_schema(float) == {"type": "number"}

    def test_bool(self):
        assert _type_to_schema(bool) == {"type": "boolean"}

    def test_list_of_str(self):
        from typing import List
        result = _type_to_schema(List[str])
        assert result == {"type": "array", "items": {"type": "string"}}

    def test_dict(self):
        from typing import Dict
        result = _type_to_schema(Dict[str, str])
        assert result == {"type": "object"}

    def test_optional_str(self):
        from typing import Optional
        result = _type_to_schema(Optional[str])
        assert result == {"type": "string"}

    def test_unknown_type_defaults_to_string(self):
        class Custom:
            pass
        result = _type_to_schema(Custom)
        assert result == {"type": "string"}


class TestParseDocstring:

    def test_simple_doc(self):
        doc = "Do something useful."
        desc, params = _parse_docstring(doc)
        assert desc == "Do something useful."
        assert params == {}

    def test_with_args(self):
        doc = """Do something.

    Args:
        path: The file path.
        content: The body text.
    """
        desc, params = _parse_docstring(doc)
        assert desc == "Do something."
        assert params["path"] == "The file path."
        assert params["content"] == "The body text."

    def test_none_docstring(self):
        desc, params = _parse_docstring(None)
        assert desc == ""
        assert params == {}

    def test_empty_docstring(self):
        desc, params = _parse_docstring("")
        assert desc == ""
        assert params == {}


class TestNsMatches:

    def test_exact_match(self):
        assert _ns_matches("skills", ["skills"]) is True

    def test_child_match(self):
        assert _ns_matches("skills.data", ["skills"]) is True

    def test_no_match(self):
        assert _ns_matches("filesystem", ["skills"]) is False

    def test_partial_name_not_matched(self):
        assert _ns_matches("skillset", ["skills"]) is False


class TestToolDecorator:

    def test_attaches_meta(self):
        @tool(permissions=("read", "write"), gpu=True, sandbox=True)
        def my_func(x: str) -> str:
            return x

        assert my_func._tool_meta["gpu"] is True
        assert my_func._tool_meta["permissions"] == ("read", "write")
        assert my_func._tool_meta["sandbox"] is True

    def test_filters_invalid_permissions(self):
        @tool(permissions=("read", "invalid_perm", "execute"))
        def my_func() -> str:
            return ""

        assert my_func._tool_meta["permissions"] == ("read", "execute")

    def test_default_permissions(self):
        @tool()
        def my_func() -> str:
            return ""

        assert my_func._tool_meta["permissions"] == ("read",)


class TestBuildToolDef:

    def test_basic_function(self):
        def read_file(path: str, encoding: str = "utf-8") -> str:
            """Read a file.

            Args:
                path: File path to read.
                encoding: Text encoding.
            """
            return ""

        td = _build_tool_def(read_file, "filesystem")
        assert td.qualified_name == "filesystem_read_file"
        assert td.namespace == "filesystem"
        assert td.name == "read_file"
        assert "path" in td.required
        assert "encoding" not in td.required
        assert td.parameters["path"]["type"] == "string"
        assert td.parameters["encoding"]["description"] == "Text encoding."

    def test_decorated_function(self):
        @tool(permissions=("write",), sandbox=True, name="custom_name")
        def do_thing(value: int) -> str:
            """Does a thing."""
            return ""

        td = _build_tool_def(do_thing, "myns")
        assert td.qualified_name == "myns_custom_name"
        assert td.sandbox is True
        assert td.permissions == ("write",)


class TestToolRegistry:

    @pytest.fixture
    def registry(self):
        return ToolRegistry()

    def test_register_and_get(self, registry):
        def hello(name: str) -> str:
            """Say hello."""
            return f"Hello {name}"

        td = registry.register(hello, "greet")
        assert td.qualified_name == "greet_hello"
        assert registry.get("greet_hello") is td

    def test_namespaces(self, registry):
        def a() -> str:
            return ""
        def b() -> str:
            return ""

        registry.register(a, "ns1")
        registry.register(b, "ns2")
        assert sorted(registry.namespaces()) == ["ns1", "ns2"]

    def test_tools_in_namespace(self, registry):
        def f1() -> str:
            return ""
        def f2() -> str:
            return ""

        registry.register(f1, "ns")
        registry.register(f2, "ns")
        tools = registry.tools_in("ns")
        assert len(tools) == 2

    def test_call_tool(self, registry):
        def add(a: int, b: int) -> str:
            return str(a + b)

        registry.register(add, "math")
        result = registry.call("math_add", {"a": 3, "b": 4})
        assert result == "7"

    def test_call_unknown_raises(self, registry):
        with pytest.raises(KeyError, match="unknown tool"):
            registry.call("nonexistent_tool", {})

    def test_is_sandboxed(self, registry):
        @tool(sandbox=True)
        def dangerous() -> str:
            return ""

        @tool(sandbox=False)
        def safe() -> str:
            return ""

        registry.register(dangerous, "ns")
        registry.register(safe, "ns")
        assert registry.is_sandboxed("ns_dangerous") is True
        assert registry.is_sandboxed("ns_safe") is False
        assert registry.is_sandboxed("nonexistent") is False

    def test_mcp_definitions(self, registry):
        @tool(permissions=("read",), resources=("files:read",))
        def read(path: str) -> str:
            """Read a file.

            Args:
                path: The path.
            """
            return ""

        registry.register(read, "fs")
        defs = registry.mcp_definitions()
        assert len(defs) == 1
        d = defs[0]
        assert d["type"] == "function"
        assert d["function"]["name"] == "fs_read"
        assert "path" in d["function"]["parameters"]["properties"]
        assert d["function"]["permissions"] == ["read"]
        assert d["function"]["resources"] == ["files:read"]

    def test_mcp_definitions_namespace_filter(self, registry):
        def a() -> str:
            return ""
        def b() -> str:
            return ""

        registry.register(a, "fs")
        registry.register(b, "shell")
        defs = registry.mcp_definitions(namespaces=["fs"])
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "fs_a"

    def test_mcp_definitions_permission_filter(self, registry):
        @tool(permissions=("read",))
        def reader() -> str:
            return ""

        @tool(permissions=("execute",))
        def runner() -> str:
            return ""

        registry.register(reader, "ns")
        registry.register(runner, "ns")
        defs = registry.mcp_definitions(allowed_permissions={"read"})
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "ns_reader"

    def test_mcp_definitions_resource_filter(self, registry):
        @tool(resources=("files:read",))
        def a() -> str:
            return ""

        @tool(resources=("files:write",))
        def b() -> str:
            return ""

        registry.register(a, "ns")
        registry.register(b, "ns")
        defs = registry.mcp_definitions(allowed_resources={"files:read"})
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "ns_a"

    def test_resource_permissions(self, registry):
        @tool(resources=("files:read", "files:write"))
        def rw() -> str:
            return ""

        @tool(resources=("shell:execute",))
        def ex() -> str:
            return ""

        registry.register(rw, "a")
        registry.register(ex, "b")
        perms = registry.resource_permissions()
        assert "files:read" in perms
        assert "files:write" in perms
        assert "shell:execute" in perms

    def test_register_module(self, registry):
        import acai.tools.filesystem as fs_mod
        count = registry.register_module(fs_mod, namespace="filesystem")
        assert count >= 5
        assert registry.get("filesystem_read_file") is not None
        assert registry.get("filesystem_write_file") is not None

    def test_get_returns_none_for_missing(self, registry):
        assert registry.get("does_not_exist") is None

    def test_tools_in_empty_namespace(self, registry):
        assert registry.tools_in("nonexistent_namespace") == []

    def test_all_tools(self, registry):
        def a() -> str:
            return ""
        def b() -> str:
            return ""
        registry.register(a, "ns1")
        registry.register(b, "ns2")
        assert len(registry.all_tools()) == 2

    def test_duplicate_registration_no_duplicate_in_namespace(self, registry):
        def f() -> str:
            return ""
        registry.register(f, "ns")
        registry.register(f, "ns")
        assert registry._namespaces["ns"].count("ns_f") == 1

    def test_resource_permissions_with_namespace(self, registry):
        @tool(resources=("files:read",))
        def a() -> str:
            return ""
        @tool(resources=("shell:execute",))
        def b() -> str:
            return ""
        registry.register(a, "fs")
        registry.register(b, "shell")
        perms = registry.resource_permissions(namespace="fs")
        assert perms == ["files:read"]
        assert "shell:execute" not in perms

    def test_resource_permissions_empty_namespace(self, registry):
        perms = registry.resource_permissions(namespace="empty")
        assert perms == []

    def test_merge_registries(self, registry):
        other = ToolRegistry()
        def f1() -> str:
            return "one"
        def f2() -> str:
            return "two"
        registry.register(f1, "ns_a")
        other.register(f2, "ns_b")
        registry.merge(other)
        assert registry.get("ns_a_f1") is not None
        assert registry.get("ns_b_f2") is not None
        assert "ns_b" in registry.namespaces()

    def test_merge_no_duplicate_namespace_entries(self, registry):
        other = ToolRegistry()
        def f() -> str:
            return ""
        registry.register(f, "ns")
        other.register(f, "ns")
        registry.merge(other)
        assert registry._namespaces["ns"].count("ns_f") == 1

    def test_mcp_definitions_no_resources_passes_resource_filter(self, registry):
        @tool(permissions=("read",))
        def no_res() -> str:
            """No resources declared."""
            return ""
        registry.register(no_res, "ns")
        defs = registry.mcp_definitions(allowed_resources={"files:read"})
        assert len(defs) == 1

    def test_mcp_definitions_scope_in_output(self, registry):
        @tool(scope="project:wf_id")
        def scoped() -> str:
            """A scoped tool."""
            return ""
        registry.register(scoped, "ns")
        defs = registry.mcp_definitions()
        assert defs[0]["function"]["scope"] == "project:wf_id"

    def test_blueprint_returns_router(self, registry):
        def f() -> str:
            return ""
        registry.register(f, "ns")
        rt = registry.blueprint()
        from fastapi import APIRouter
        assert isinstance(rt, APIRouter)


class TestToolDefProperties:

    def test_scope_level_project(self):
        td = _build_tool_def(lambda: None, "ns")
        td.scope = "project:workflow_id"
        assert td.scope_level == "project"

    def test_scope_key_project(self):
        td = _build_tool_def(lambda: None, "ns")
        td.scope = "project:workflow_id"
        assert td.scope_key == "workflow_id"

    def test_scope_level_global(self):
        td = _build_tool_def(lambda: None, "ns")
        td.scope = "global"
        assert td.scope_level == "global"

    def test_scope_key_empty_for_global(self):
        td = _build_tool_def(lambda: None, "ns")
        td.scope = "global"
        assert td.scope_key == ""

    def test_scope_level_defaults_global_empty(self):
        td = _build_tool_def(lambda: None, "ns")
        td.scope = ""
        assert td.scope_level == "global"
        assert td.scope_key == ""


class TestBuildToolDefEdgeCases:

    def test_skips_self_parameter(self):
        def method(self, x: str) -> str:
            """Do something."""
            return x
        td = _build_tool_def(method, "ns")
        assert "self" not in td.parameters
        assert "x" in td.parameters

    def test_skips_cls_parameter(self):
        def classmethod_like(cls, x: int) -> int:
            """Cls method."""
            return x
        td = _build_tool_def(classmethod_like, "ns")
        assert "cls" not in td.parameters
        assert "x" in td.parameters

    def test_no_type_hint_defaults_to_string(self):
        def untyped(x):
            """No hints."""
            return x
        td = _build_tool_def(untyped, "ns")
        assert td.parameters["x"]["type"] == "string"

    def test_no_docstring(self):
        def no_doc(a: int) -> int:
            return a
        td = _build_tool_def(no_doc, "ns")
        assert td.description == ""
        assert "description" not in td.parameters["a"]

    def test_optional_param_not_required(self):
        def opt_fn(a: str, b: Optional[str] = None) -> str:
            """Test optional."""
            return a
        td = _build_tool_def(opt_fn, "ns")
        assert "a" in td.required
        assert "b" not in td.required

    def test_meta_name_overrides_function_name(self):
        @tool(name="custom")
        def original_name() -> str:
            return ""
        td = _build_tool_def(original_name, "ns")
        assert td.name == "custom"
        assert td.qualified_name == "ns_custom"

    def test_function_without_decorator_has_default_meta(self):
        def plain_fn(x: str) -> str:
            return x
        td = _build_tool_def(plain_fn, "ns")
        assert td.gpu is False
        assert td.permissions == ("read",)
        assert td.resources == ()
        assert td.sandbox is False
        assert td.scope == ""


class TestToolDecoratorEdgeCases:

    def test_all_invalid_permissions_falls_back_to_read(self):
        @tool(permissions=("nope", "nah", "invalid"))
        def f() -> str:
            return ""
        assert f._tool_meta["permissions"] == ("read",)

    def test_empty_permissions_falls_back_to_read(self):
        @tool(permissions=())
        def f() -> str:
            return ""
        assert f._tool_meta["permissions"] == ("read",)

    def test_resources_filters_invalid_format(self):
        @tool(resources=("files:read", "no_colon", "shell:exec"))
        def f() -> str:
            return ""
        assert f._tool_meta["resources"] == ("files:read", "shell:exec")

    def test_resources_empty_when_all_invalid(self):
        @tool(resources=("invalid", "also_bad"))
        def f() -> str:
            return ""
        assert f._tool_meta["resources"] == ()


class TestParseScopeEdgeCases:

    def test_global_with_key(self):
        assert _parse_scope("global:mykey") == ("global", "mykey")

    def test_project_bare(self):
        assert _parse_scope("project") == ("project", "")

    def test_invalid_no_colon_not_in_valid(self):
        assert _parse_scope("randomvalue") == ("global", "")

    def test_colon_with_invalid_level(self):
        assert _parse_scope("badlevel:key") == ("global", "")


class TestTypeToSchemaEdgeCases:

    def test_bare_list_no_args(self):
        result = _type_to_schema(list)
        assert result == {"type": "string"}

    def test_union_none_first(self):
        result = _type_to_schema(Union[None, int])
        assert result == {"type": "integer"}

    def test_optional_int(self):
        result = _type_to_schema(Optional[int])
        assert result == {"type": "integer"}

    def test_list_of_int(self):
        from typing import List
        result = _type_to_schema(List[int])
        assert result == {"type": "array", "items": {"type": "integer"}}


class TestModuleNamespace:

    def test_acai_tools_prefix(self):
        assert _module_namespace("acai.tools.git") == "git"

    def test_acai_plugins_prefix(self):
        assert _module_namespace("acai.plugins.myplugin") == "myplugin"

    def test_acai_plugins_nested(self):
        assert _module_namespace("acai.plugins.myplugin.extra") == "myplugin.extra"

    def test_unknown_prefix_uses_last_segment(self):
        assert _module_namespace("some.other.module") == "module"

    def test_single_name(self):
        assert _module_namespace("standalone") == "standalone"


class TestNsMatchesEdgeCases:

    def test_empty_allowed_list(self):
        assert _ns_matches("anything", []) is False

    def test_multiple_allowed(self):
        assert _ns_matches("fs", ["shell", "fs"]) is True

    def test_deeply_nested_child(self):
        assert _ns_matches("skills.data.sub.deep", ["skills"]) is True

    def test_prefix_overlap_no_dot(self):
        assert _ns_matches("skillset", ["skills"]) is False


class TestDiscoverInPackage:

    def test_package_without_path_returns_zero(self):
        mod = types.ModuleType("no_path_pkg")
        assert _discover_in_package(mod, ToolRegistry()) == 0

    def test_skips_modules_in_skip_list(self):
        fake_pkg = types.ModuleType("acai.tools")
        fake_pkg.__path__ = []
        fake_pkg.__name__ = "acai.tools"
        registry = ToolRegistry()
        with patch("acai.orchestrator.tools.pkgutil.iter_modules") as mock_iter:
            mock_iter.return_value = [
                (None, "acai.tools.registry", False),
                (None, "acai.tools.good", False),
            ]
            good_mod = types.ModuleType("acai.tools.good")
            def public_fn() -> str:
                return ""
            public_fn.__module__ = "acai.tools.good"
            good_mod.public_fn = public_fn
            with patch("acai.orchestrator.tools.importlib.import_module") as mock_import:
                mock_import.return_value = good_mod
                count = _discover_in_package(fake_pkg, registry)
        assert count == 1
        assert registry.get("good_public_fn") is not None

    def test_import_failure_is_logged_and_skipped(self):
        fake_pkg = types.ModuleType("acai.tools")
        fake_pkg.__path__ = []
        fake_pkg.__name__ = "acai.tools"
        registry = ToolRegistry()
        with patch("acai.orchestrator.tools.pkgutil.iter_modules") as mock_iter:
            mock_iter.return_value = [
                (None, "acai.tools.broken", False),
            ]
            with patch("acai.orchestrator.tools.importlib.import_module", side_effect=ImportError("boom")):
                count = _discover_in_package(fake_pkg, registry)
        assert count == 0


class TestLoadPlugins:

    def test_plugin_module_registration(self):
        container = types.ModuleType("acai.plugins")
        container.__path__ = []
        container.__name__ = "acai.plugins"

        plugin = types.ModuleType("acai.plugins.myplugin")
        plugin.__name__ = "acai.plugins.myplugin"
        def helper() -> str:
            return "hi"
        helper.__module__ = "acai.plugins.myplugin"
        plugin.helper = helper

        registry = ToolRegistry()
        with patch("acai.orchestrator.tools.pkgutil.iter_modules") as mock_iter:
            mock_iter.return_value = [(None, "acai.plugins.myplugin", False)]
            with patch("acai.orchestrator.tools.importlib.import_module", return_value=plugin):
                count = _load_plugins(container, registry)
        assert count == 1
        assert registry.get("myplugin_helper") is not None

    def test_plugin_package_with_submodules(self):
        container = types.ModuleType("acai.plugins")
        container.__path__ = []
        container.__name__ = "acai.plugins"

        plugin_pkg = types.ModuleType("acai.plugins.mypkg")
        plugin_pkg.__path__ = []
        plugin_pkg.__name__ = "acai.plugins.mypkg"
        def pkg_fn() -> str:
            return ""
        pkg_fn.__module__ = "acai.plugins.mypkg"
        plugin_pkg.pkg_fn = pkg_fn

        registry = ToolRegistry()
        with patch("acai.orchestrator.tools.pkgutil.iter_modules") as mock_iter:
            mock_iter.side_effect = [
                [(None, "acai.plugins.mypkg", True)],
                [],
            ]
            with patch("acai.orchestrator.tools.importlib.import_module", return_value=plugin_pkg):
                count = _load_plugins(container, registry)
        assert count >= 1

    def test_plugin_register_hook_returning_dict(self):
        container = types.ModuleType("acai.plugins")
        container.__path__ = []
        container.__name__ = "acai.plugins"

        plugin = types.ModuleType("acai.plugins.withres")
        plugin.__name__ = "acai.plugins.withres"
        plugin.register = MagicMock(return_value={"gpu_pool": "resource_config"})

        registry = ToolRegistry()
        with patch("acai.orchestrator.tools.pkgutil.iter_modules") as mock_iter:
            mock_iter.return_value = [(None, "acai.plugins.withres", False)]
            with patch("acai.orchestrator.tools.importlib.import_module", return_value=plugin):
                _load_plugins(container, registry)
        assert len(registry.plugin_resources) == 1
        assert registry.plugin_resources[0] == {"gpu_pool": "resource_config"}

    def test_plugin_register_hook_exception_is_caught(self):
        container = types.ModuleType("acai.plugins")
        container.__path__ = []
        container.__name__ = "acai.plugins"

        plugin = types.ModuleType("acai.plugins.bad")
        plugin.__name__ = "acai.plugins.bad"
        plugin.register = MagicMock(side_effect=RuntimeError("register exploded"))

        registry = ToolRegistry()
        with patch("acai.orchestrator.tools.pkgutil.iter_modules") as mock_iter:
            mock_iter.return_value = [(None, "acai.plugins.bad", False)]
            with patch("acai.orchestrator.tools.importlib.import_module", return_value=plugin):
                count = _load_plugins(container, registry)
        assert count == 0
        assert len(registry.plugin_resources) == 0

    def test_plugin_import_failure_is_skipped(self):
        container = types.ModuleType("acai.plugins")
        container.__path__ = []
        container.__name__ = "acai.plugins"

        registry = ToolRegistry()
        with patch("acai.orchestrator.tools.pkgutil.iter_modules") as mock_iter:
            mock_iter.return_value = [(None, "acai.plugins.broken", False)]
            with patch("acai.orchestrator.tools.importlib.import_module", side_effect=ImportError("nope")):
                count = _load_plugins(container, registry)
        assert count == 0


class TestDiscoverTools:

    def test_discover_returns_populated_registry(self):
        registry = discover_tools()
        assert len(registry.all_tools()) > 0
        assert len(registry.namespaces()) > 0

    def test_discover_with_extra_package(self):
        extra_pkg = types.ModuleType("extra_tools")
        extra_pkg.__path__ = []
        extra_pkg.__name__ = "extra_tools"
        with patch("acai.orchestrator.tools._discover_in_package") as mock_disc:
            mock_disc.return_value = 0
            registry = discover_tools(extra_pkg)
        assert mock_disc.call_count >= 2


class TestRegisterModuleEdgeCases:

    def test_skips_private_functions(self):
        mod = types.ModuleType("acai.tools.testmod")
        mod.__name__ = "acai.tools.testmod"
        def _private() -> str:
            return ""
        _private.__module__ = "acai.tools.testmod"
        def public() -> str:
            return ""
        public.__module__ = "acai.tools.testmod"
        mod._private = _private
        mod.public = public

        registry = ToolRegistry()
        count = registry.register_module(mod, namespace="testmod")
        assert count == 1
        assert registry.get("testmod__private") is None
        assert registry.get("testmod_public") is not None

    def test_skips_imported_functions(self):
        mod = types.ModuleType("acai.tools.mymod")
        mod.__name__ = "acai.tools.mymod"
        def local_fn() -> str:
            return ""
        local_fn.__module__ = "acai.tools.mymod"
        def foreign_fn() -> str:
            return ""
        foreign_fn.__module__ = "some.other.module"
        mod.local_fn = local_fn
        mod.foreign_fn = foreign_fn

        registry = ToolRegistry()
        count = registry.register_module(mod, namespace="mymod")
        assert count == 1
        assert registry.get("mymod_foreign_fn") is None

    def test_default_namespace_from_module_name(self):
        mod = types.ModuleType("acai.tools.git")
        mod.__name__ = "acai.tools.git"
        def status() -> str:
            return ""
        status.__module__ = "acai.tools.git"
        mod.status = status

        registry = ToolRegistry()
        count = registry.register_module(mod)
        assert count == 1
        assert registry.get("git_status") is not None


class TestRouterEndpoints:
    """Test the FastAPI router created by ToolRegistry.router()."""

    @pytest.fixture
    def app_and_registry(self):
        from fastapi import FastAPI
        registry = ToolRegistry()

        def greet(name: str) -> str:
            """Say hello."""
            return f"Hello {name}"

        def fail_tool(msg: str) -> str:
            """Always fails."""
            raise ValueError(msg)

        registry.register(greet, "test")
        registry.register(fail_tool, "test")

        app = FastAPI()
        app.include_router(registry.router())
        return app, registry

    @pytest.fixture
    def client(self, app_and_registry):
        from starlette.testclient import TestClient
        app, _ = app_and_registry
        return TestClient(app)

    def test_list_tools(self, client):
        resp = client.get("/tools/list")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        names = [d["function"]["name"] for d in data]
        assert "test_greet" in names

    def test_call_unknown_tool_returns_404(self, client):
        resp = client.post("/tools/call", json={"tool": "does_not_exist", "args": {}})
        assert resp.status_code == 404
        body = _parse_sse_response(resp.text)
        assert body is None or "unknown tool" in resp.text

    def test_call_tool_not_in_namespace_returns_403(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        registry = ToolRegistry()
        def secret() -> str:
            return "hidden"
        registry.register(secret, "internal")
        app = FastAPI()
        app.include_router(registry.router(namespaces=["public"]))
        client = TestClient(app)

        resp = client.post("/tools/call", json={"tool": "internal_secret", "args": {}})
        assert resp.status_code == 403
        assert "not exposed" in resp.text

    def test_call_tool_success_sse(self, client):
        resp = client.post("/tools/call", json={"tool": "test_greet", "args": {"name": "World"}})
        assert resp.status_code == 200
        events = _parse_all_sse_events(resp.text)
        result_events = [e for e in events if e["event"] == "result"]
        assert len(result_events) == 1
        assert result_events[0]["data"]["result"] == "Hello World"
        done_events = [e for e in events if e["event"] == "done"]
        assert len(done_events) == 1

    def test_call_tool_exception_returns_sse_error(self, client):
        resp = client.post("/tools/call", json={
            "tool": "test_fail_tool",
            "args": {"msg": "something broke"},
        })
        assert resp.status_code == 200
        events = _parse_all_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert "something broke" in error_events[0]["data"]["error"]

    def test_call_tool_malformed_json_body(self, client):
        resp = client.post(
            "/tools/call",
            content=b"not valid json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 404
        assert "unknown tool" in resp.text

    def test_call_tool_empty_body(self, client):
        resp = client.post(
            "/tools/call",
            content=b"",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 404

    def test_call_tool_with_context(self, client):
        resp = client.post("/tools/call", json={
            "tool": "test_greet",
            "args": {"name": "Ctx"},
            "context": {"task_id": "t1", "kind": "test", "orchestrator_url": ""},
        })
        assert resp.status_code == 200
        events = _parse_all_sse_events(resp.text)
        result_events = [e for e in events if e["event"] == "result"]
        assert result_events[0]["data"]["result"] == "Hello Ctx"

    def test_call_tool_with_context_and_orchestrator_url(self, client):
        resp = client.post("/tools/call", json={
            "tool": "test_greet",
            "args": {"name": "Orch"},
            "context": {
                "task_id": "t2",
                "orchestrator_url": "http://localhost:9999",
            },
        })
        assert resp.status_code == 200
        events = _parse_all_sse_events(resp.text)
        result_events = [e for e in events if e["event"] == "result"]
        assert result_events[0]["data"]["result"] == "Hello Orch"

    def test_list_tools_with_namespace_filter(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        registry = ToolRegistry()
        def a() -> str:
            return ""
        def b() -> str:
            return ""
        registry.register(a, "ns1")
        registry.register(b, "ns2")
        app = FastAPI()
        app.include_router(registry.router(namespaces=["ns1"]))
        client = TestClient(app)

        resp = client.get("/tools/list")
        data = resp.json()
        names = [d["function"]["name"] for d in data]
        assert "ns1_a" in names
        assert "ns2_b" not in names

    def test_sandbox_proxy_intercepts_call(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        registry = ToolRegistry()

        @tool(sandbox=True)
        def sandboxed_fn(x: str) -> str:
            """Runs in sandbox."""
            return x

        registry.register(sandboxed_fn, "ns")

        proxy = MagicMock()
        proxy.should_proxy.return_value = True
        mock_sse = "event: result\ndata: {\"result\": \"proxied\"}\n\nevent: done\ndata: {}\n\n"
        from starlette.responses import StreamingResponse

        async def fake_proxy_call(tool_name, args, ctx_data):
            async def gen():
                yield mock_sse
            return StreamingResponse(gen(), media_type="text/event-stream")

        proxy.proxy_call = fake_proxy_call

        app = FastAPI()
        app.include_router(registry.router(sandbox_proxy=proxy))
        client = TestClient(app)

        resp = client.post("/tools/call", json={
            "tool": "ns_sandboxed_fn",
            "args": {"x": "test"},
        })
        assert resp.status_code == 200
        proxy.should_proxy.assert_called_once()

    def test_sandbox_proxy_failure_returns_sse_error(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        registry = ToolRegistry()

        @tool(sandbox=True)
        def sandboxed_fn(x: str) -> str:
            return x

        registry.register(sandboxed_fn, "ns")

        proxy = MagicMock()
        proxy.should_proxy.return_value = True
        proxy.proxy_call = AsyncMock(side_effect=ConnectionError("sandbox unreachable"))

        app = FastAPI()
        app.include_router(registry.router(sandbox_proxy=proxy))
        client = TestClient(app)

        resp = client.post("/tools/call", json={
            "tool": "ns_sandboxed_fn",
            "args": {"x": "test"},
        })
        assert resp.status_code == 200
        events = _parse_all_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert "sandbox unreachable" in error_events[0]["data"]["error"]
        done_events = [e for e in events if e["event"] == "done"]
        assert len(done_events) == 1

    def test_context_none_does_not_set_context(self, client):
        resp = client.post("/tools/call", json={
            "tool": "test_greet",
            "args": {"name": "NoCtx"},
            "context": None,
        })
        assert resp.status_code == 200
        events = _parse_all_sse_events(resp.text)
        result_events = [e for e in events if e["event"] == "result"]
        assert result_events[0]["data"]["result"] == "Hello NoCtx"

    def test_context_non_dict_ignored(self, client):
        resp = client.post("/tools/call", json={
            "tool": "test_greet",
            "args": {"name": "NotDict"},
            "context": "string_context",
        })
        assert resp.status_code == 200
        events = _parse_all_sse_events(resp.text)
        result_events = [e for e in events if e["event"] == "result"]
        assert result_events[0]["data"]["result"] == "Hello NotDict"


# ---------------------------------------------------------------------------
# SSE parsing helpers
# ---------------------------------------------------------------------------

def _parse_all_sse_events(text: str) -> list[dict]:
    """Parse SSE text into a list of {event, data} dicts."""
    events = []
    current_event = None
    current_data = None
    for line in text.strip().split("\n"):
        if line.startswith("event: "):
            current_event = line[len("event: "):]
        elif line.startswith("data: "):
            current_data = line[len("data: "):]
        elif line == "":
            if current_event is not None and current_data is not None:
                try:
                    events.append({"event": current_event, "data": json.loads(current_data)})
                except json.JSONDecodeError:
                    events.append({"event": current_event, "data": current_data})
            current_event = None
            current_data = None
    if current_event is not None and current_data is not None:
        try:
            events.append({"event": current_event, "data": json.loads(current_data)})
        except json.JSONDecodeError:
            events.append({"event": current_event, "data": current_data})
    return events


def _parse_sse_response(text: str) -> dict | None:
    """Parse the first data payload from SSE text."""
    events = _parse_all_sse_events(text)
    return events[0]["data"] if events else None
