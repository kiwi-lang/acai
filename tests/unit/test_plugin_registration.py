"""Tests for the plugin system: scaffold, discover, and register."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import types
import tempfile

import pytest

from acai.cli.scaffold import scaffold_plugin, _underscored
from acai.orchestrator.tools import ToolRegistry, discover_tools, _load_plugins


# ---------------------------------------------------------------------------
# scaffold_plugin
# ---------------------------------------------------------------------------

class TestScaffoldPlugin:
    """Verify the scaffolder produces a valid plugin package."""

    def test_creates_project_directory(self, tmp_path):
        path = scaffold_plugin("my-tools", dest=str(tmp_path))
        assert os.path.isdir(path)
        assert path.endswith("acai-plugin-my-tools")

    def test_pyproject_toml_rendered(self, tmp_path):
        path = scaffold_plugin("my-tools", dest=str(tmp_path))
        toml = os.path.join(path, "pyproject.toml")
        assert os.path.isfile(toml)
        with open(toml) as f:
            content = f.read()
        assert "acai-plugin-my-tools" in content
        assert "{{name}}" not in content

    def test_init_has_register(self, tmp_path):
        path = scaffold_plugin("my-tools", dest=str(tmp_path))
        init = os.path.join(path, "acai", "plugins", "my_tools", "__init__.py")
        assert os.path.isfile(init)
        with open(init) as f:
            content = f.read()
        assert "def register(" in content
        assert "{{" not in content

    def test_tools_module_created(self, tmp_path):
        path = scaffold_plugin("my-tools", dest=str(tmp_path))
        tools = os.path.join(path, "acai", "plugins", "my_tools", "tools.py")
        assert os.path.isfile(tools)
        with open(tools) as f:
            content = f.read()
        assert "@tool" in content
        assert "def hello" in content

    def test_agent_definition_created(self, tmp_path):
        path = scaffold_plugin("my-tools", dest=str(tmp_path))
        defn = os.path.join(
            path, "acai", "plugins", "my_tools",
            "agents", "example", "definition.json",
        )
        assert os.path.isfile(defn)
        with open(defn) as f:
            data = json.load(f)
        assert data["name"] == "my_tools-example"

    def test_agent_template_created(self, tmp_path):
        path = scaffold_plugin("my-tools", dest=str(tmp_path))
        tpl = os.path.join(
            path, "acai", "plugins", "my_tools",
            "agents", "example", "system.j2",
        )
        assert os.path.isfile(tpl)

    def test_readme_rendered(self, tmp_path):
        path = scaffold_plugin("my-tools", dest=str(tmp_path))
        readme = os.path.join(path, "README.md")
        assert os.path.isfile(readme)
        with open(readme) as f:
            content = f.read()
        assert "my-tools" in content

    def test_raises_on_duplicate(self, tmp_path):
        scaffold_plugin("dupe", dest=str(tmp_path))
        with pytest.raises(FileExistsError):
            scaffold_plugin("dupe", dest=str(tmp_path))

    def test_namespace_init_exists(self, tmp_path):
        path = scaffold_plugin("foo", dest=str(tmp_path))
        ns_init = os.path.join(path, "acai", "plugins", "__init__.py")
        assert os.path.isfile(ns_init)
        with open(ns_init) as f:
            content = f.read()
        assert "extend_path" in content


class TestUnderscored:
    def test_basic(self):
        assert _underscored("my-tools") == "my_tools"

    def test_dots(self):
        assert _underscored("foo.bar") == "foo_bar"

    def test_spaces(self):
        assert _underscored("hello world") == "hello_world"

    def test_already_underscored(self):
        assert _underscored("already_ok") == "already_ok"


# ---------------------------------------------------------------------------
# Plugin discovery & registration
# ---------------------------------------------------------------------------

def _make_plugin_package(tmp_path, plugin_name="test_plug"):
    """Create a minimal plugin under tmp_path that discover_tools can find."""
    pkg_root = os.path.join(tmp_path, "acai", "plugins", plugin_name)
    os.makedirs(pkg_root, exist_ok=True)

    # namespace __init__.py files
    acai_init = os.path.join(tmp_path, "acai", "__init__.py")
    with open(acai_init, "w") as f:
        f.write("")

    plugins_init = os.path.join(tmp_path, "acai", "plugins", "__init__.py")
    with open(plugins_init, "w") as f:
        f.write("from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n")

    # Plugin __init__ with register()
    agents_dir = os.path.join(pkg_root, "agents")
    os.makedirs(agents_dir, exist_ok=True)
    agent_dir = os.path.join(agents_dir, "plug-agent")
    os.makedirs(agent_dir, exist_ok=True)
    with open(os.path.join(agent_dir, "definition.json"), "w") as f:
        json.dump({"name": "plug-agent", "description": "from plugin"}, f)
    with open(os.path.join(agent_dir, "system.j2"), "w") as f:
        f.write("You are a test plugin agent.\n")

    with open(os.path.join(pkg_root, "__init__.py"), "w") as f:
        f.write(
            "import os\n"
            "_HERE = os.path.dirname(__file__)\n"
            "def register(registry, config=None):\n"
            "    return {'agents_dir': os.path.join(_HERE, 'agents')}\n"
        )

    # Plugin tools module
    with open(os.path.join(pkg_root, "tools.py"), "w") as f:
        f.write(
            "from acai.orchestrator.tools import tool\n\n"
            "@tool(permissions=('read',))\n"
            "def plugin_greet(name: str = 'world') -> str:\n"
            '    """Greet someone."""\n'
            "    return f'hello {name}'\n"
        )

    return str(tmp_path)


def _patch_plugins_path(extra_path):
    """Context manager that temporarily adds *extra_path* to acai.plugins.__path__."""
    import acai.plugins as plugins_mod
    saved_path = list(plugins_mod.__path__)
    plugins_mod.__path__ = [extra_path] + saved_path
    return plugins_mod, saved_path


def _cleanup_plugin_modules(prefix, extra_sys_path, saved_path):
    """Remove cached plugin modules and restore acai.plugins.__path__."""
    for key in list(sys.modules):
        if key.startswith(prefix):
            del sys.modules[key]
    if extra_sys_path in sys.path:
        sys.path.remove(extra_sys_path)
    import acai.plugins as plugins_mod
    plugins_mod.__path__ = saved_path


class TestPluginDiscovery:
    """Verify that _load_plugins finds plugin tools and calls register()."""

    def test_load_plugins_discovers_tools(self, tmp_path):
        pkg_path = _make_plugin_package(tmp_path)
        sys.path.insert(0, pkg_path)
        plugins_mod, saved_path = _patch_plugins_path(
            os.path.join(pkg_path, "acai", "plugins"),
        )
        try:
            for key in list(sys.modules):
                if key.startswith("acai.plugins.test_plug"):
                    del sys.modules[key]

            registry = ToolRegistry()
            n = _load_plugins(plugins_mod, registry)

            assert n > 0
            assert any("plugin_greet" in name for name in registry._tools)
            assert len(registry.plugin_resources) == 1
            res = registry.plugin_resources[0]
            assert "agents_dir" in res
            assert os.path.isdir(res["agents_dir"])
        finally:
            _cleanup_plugin_modules("acai.plugins.test_plug", pkg_path, saved_path)

    def test_register_not_called_when_absent(self, tmp_path):
        """Plugins without register() should still have their tools discovered."""
        pkg_root = os.path.join(tmp_path, "acai", "plugins", "no_register")
        os.makedirs(pkg_root, exist_ok=True)

        with open(os.path.join(tmp_path, "acai", "__init__.py"), "w") as f:
            f.write("")
        with open(os.path.join(tmp_path, "acai", "plugins", "__init__.py"), "w") as f:
            f.write("from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n")
        with open(os.path.join(pkg_root, "__init__.py"), "w") as f:
            f.write("# no register function\n")
        with open(os.path.join(pkg_root, "tools.py"), "w") as f:
            f.write(
                "from acai.orchestrator.tools import tool\n\n"
                "@tool(permissions=('read',))\n"
                "def no_reg_tool() -> str:\n"
                '    """A tool."""\n'
                "    return 'ok'\n"
            )

        sys.path.insert(0, str(tmp_path))
        plugins_mod, saved_path = _patch_plugins_path(
            os.path.join(str(tmp_path), "acai", "plugins"),
        )
        try:
            for key in list(sys.modules):
                if key.startswith("acai.plugins.no_register"):
                    del sys.modules[key]

            registry = ToolRegistry()
            _load_plugins(plugins_mod, registry)

            assert any("no_reg_tool" in name for name in registry._tools)
            assert len(registry.plugin_resources) == 0
        finally:
            _cleanup_plugin_modules("acai.plugins.no_register", str(tmp_path), saved_path)


# ---------------------------------------------------------------------------
# AgentStore multi-dir
# ---------------------------------------------------------------------------

class TestAgentStoreMultiDir:
    """Verify AgentStore picks up plugin agent directories."""

    def test_add_builtin_dir(self, tmp_path):
        from acai.orchestrator.agent_store import AgentStore

        ws = str(tmp_path / "workspace")
        bi = str(tmp_path / "builtin")
        os.makedirs(bi)
        store = AgentStore(ws, builtin_dir=bi)

        plugin_dir = str(tmp_path / "plugin_agents")
        agent_dir = os.path.join(plugin_dir, "from-plugin")
        os.makedirs(agent_dir)
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            json.dump({"name": "from-plugin", "description": "test"}, f)

        store.add_builtin_dir(plugin_dir)
        assert plugin_dir in store._builtin_dirs

        agents = store.list()
        names = [a.name for a in agents]
        assert "from-plugin" in names

    def test_get_from_plugin_dir(self, tmp_path):
        from acai.orchestrator.agent_store import AgentStore

        ws = str(tmp_path / "workspace")
        bi = str(tmp_path / "builtin")
        os.makedirs(bi)
        store = AgentStore(ws, builtin_dir=bi)

        plugin_dir = str(tmp_path / "plugin_agents")
        agent_dir = os.path.join(plugin_dir, "plug-a")
        os.makedirs(agent_dir)
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            json.dump({"name": "plug-a", "description": "plugin agent"}, f)

        store.add_builtin_dir(plugin_dir)
        agent = store.get("plug-a")
        assert agent is not None
        assert agent.name == "plug-a"
        assert agent.builtin is True

    def test_workspace_shadows_plugin(self, tmp_path):
        from acai.orchestrator.agent_store import AgentStore

        ws = str(tmp_path / "workspace")
        bi = str(tmp_path / "builtin")
        os.makedirs(bi)
        store = AgentStore(ws, builtin_dir=bi)

        plugin_dir = str(tmp_path / "plugin_agents")
        agent_dir = os.path.join(plugin_dir, "shadow-me")
        os.makedirs(agent_dir)
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            json.dump({"name": "shadow-me", "description": "from plugin"}, f)
        store.add_builtin_dir(plugin_dir)

        # Create workspace override
        ws_agent_dir = os.path.join(ws, "shadow-me")
        os.makedirs(ws_agent_dir)
        with open(os.path.join(ws_agent_dir, "definition.json"), "w") as f:
            json.dump({"name": "shadow-me", "description": "from workspace"}, f)

        agent = store.get("shadow-me")
        assert agent is not None
        assert agent.description == "from workspace"
        assert agent.builtin is False

    def test_is_builtin_from_plugin_dir(self, tmp_path):
        from acai.orchestrator.agent_store import AgentStore

        ws = str(tmp_path / "workspace")
        bi = str(tmp_path / "builtin")
        os.makedirs(bi)
        store = AgentStore(ws, builtin_dir=bi)

        plugin_dir = str(tmp_path / "plugin_agents")
        agent_dir = os.path.join(plugin_dir, "bi-check")
        os.makedirs(agent_dir)
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            json.dump({"name": "bi-check", "description": "test"}, f)

        store.add_builtin_dir(plugin_dir)
        assert store._is_builtin("bi-check") is True
        assert store._is_builtin("nonexistent") is False

    def test_add_builtin_dir_ignores_nonexistent(self, tmp_path):
        from acai.orchestrator.agent_store import AgentStore

        ws = str(tmp_path / "workspace")
        bi = str(tmp_path / "builtin")
        os.makedirs(bi)
        store = AgentStore(ws, builtin_dir=bi)

        store.add_builtin_dir("/nonexistent/path")
        assert len(store._builtin_dirs) == 1

    def test_add_builtin_dir_no_duplicates(self, tmp_path):
        from acai.orchestrator.agent_store import AgentStore

        ws = str(tmp_path / "workspace")
        bi = str(tmp_path / "builtin")
        os.makedirs(bi)
        store = AgentStore(ws, builtin_dir=bi)

        plugin_dir = str(tmp_path / "plugin_agents")
        os.makedirs(plugin_dir)
        store.add_builtin_dir(plugin_dir)
        store.add_builtin_dir(plugin_dir)
        assert store._builtin_dirs.count(plugin_dir) == 1

    def test_builtin_dir_property(self, tmp_path):
        from acai.orchestrator.agent_store import AgentStore

        ws = str(tmp_path / "workspace")
        bi = str(tmp_path / "builtin")
        os.makedirs(bi)
        store = AgentStore(ws, builtin_dir=bi)

        assert store.builtin_dir == bi

    def test_scoped_adds_and_removes(self, tmp_path):
        from acai.orchestrator.agent_store import AgentStore

        ws = str(tmp_path / "workspace")
        bi = str(tmp_path / "builtin")
        os.makedirs(bi)
        store = AgentStore(ws, builtin_dir=bi)

        wf_agents = str(tmp_path / "wf_agents")
        agent_dir = os.path.join(wf_agents, "wf-only")
        os.makedirs(agent_dir)
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            json.dump({"name": "wf-only", "description": "workflow scoped"}, f)

        assert store.get("wf-only") is None

        with store.scoped(wf_agents):
            agent = store.get("wf-only")
            assert agent is not None
            assert agent.name == "wf-only"
            names = [a.name for a in store.list()]
            assert "wf-only" in names

        assert store.get("wf-only") is None
        assert "wf-only" not in [a.name for a in store.list()]
        assert wf_agents not in store._builtin_dirs

    def test_scoped_cleans_up_on_exception(self, tmp_path):
        from acai.orchestrator.agent_store import AgentStore

        ws = str(tmp_path / "workspace")
        bi = str(tmp_path / "builtin")
        os.makedirs(bi)
        store = AgentStore(ws, builtin_dir=bi)

        wf_agents = str(tmp_path / "wf_agents")
        os.makedirs(wf_agents)

        try:
            with store.scoped(wf_agents):
                assert wf_agents in store._builtin_dirs
                raise RuntimeError("simulated error")
        except RuntimeError:
            pass

        assert wf_agents not in store._builtin_dirs


# ---------------------------------------------------------------------------
# End-to-end: scaffold → discover → AgentStore
# ---------------------------------------------------------------------------

class TestEndToEnd:
    """Scaffold a plugin from the template and verify it registers properly."""

    def test_scaffolded_plugin_is_discoverable(self, tmp_path):
        path = scaffold_plugin("e2e-test", dest=str(tmp_path))
        plugin_src = os.path.join(path, "acai", "plugins", "e2e_test")
        assert os.path.isdir(plugin_src)

        sys.path.insert(0, path)
        plugins_mod, saved_path = _patch_plugins_path(
            os.path.join(path, "acai", "plugins"),
        )
        try:
            for key in list(sys.modules):
                if key.startswith("acai.plugins.e2e_test"):
                    del sys.modules[key]

            mod = importlib.import_module("acai.plugins.e2e_test")
            assert hasattr(mod, "register")
            assert callable(mod.register)

            registry = ToolRegistry()
            result = mod.register(registry)
            assert isinstance(result, dict)
            assert "agents_dir" in result

            from acai.orchestrator.agent_store import AgentStore
            ws = str(tmp_path / "ws_agents")
            store = AgentStore(ws, builtin_dir=str(tmp_path / "empty_bi"))
            os.makedirs(str(tmp_path / "empty_bi"), exist_ok=True)
            store.add_builtin_dir(result["agents_dir"])
            agents = store.list()
            names = [a.name for a in agents]
            assert "e2e_test-example" in names
        finally:
            _cleanup_plugin_modules("acai.plugins.e2e_test", path, saved_path)

    def test_scaffolded_plugin_tools_discovered(self, tmp_path):
        path = scaffold_plugin("tools-test", dest=str(tmp_path))

        sys.path.insert(0, path)
        plugins_mod, saved_path = _patch_plugins_path(
            os.path.join(path, "acai", "plugins"),
        )
        try:
            for key in list(sys.modules):
                if key.startswith("acai.plugins.tools_test"):
                    del sys.modules[key]

            registry = ToolRegistry()
            _load_plugins(plugins_mod, registry)

            tool_names = list(registry._tools.keys())
            assert any("hello" in n for n in tool_names), f"Expected hello tool, got {tool_names}"
        finally:
            _cleanup_plugin_modules("acai.plugins.tools_test", path, saved_path)
