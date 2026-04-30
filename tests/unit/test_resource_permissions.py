"""Tests for resource permission enforcement.

Covers two enforcement layers:

1. **ToolRegistry.mcp_definitions** — tools with declared resources that
   are not a subset of ``allowed_resources`` must be excluded from the
   schema sent to the LLM.

2. **TaskGraph._resolve_tools / dispatch_tool** — the orchestrator must
   populate ``_allowed_tools`` from the filtered set and reject any
   tool call whose qualified name is not in that set.
"""

from __future__ import annotations

import pytest

from acai.orchestrator.tools import ToolRegistry, tool, _parse_scope


# ─── Helpers ────────────────────────────────────────────────────────

def _build_registry() -> ToolRegistry:
    """Build a small registry with known resource annotations."""
    reg = ToolRegistry()

    @tool(permissions=("read",), resources=("agents:read",))
    def read_agent(name: str) -> str:
        """Read an agent definition."""
        return name

    @tool(permissions=("write",), resources=("agents:create",))
    def create_agent(name: str) -> str:
        """Create a new agent."""
        return name

    @tool(permissions=("write",), resources=("agents:update",))
    def update_agent(name: str) -> str:
        """Update an agent."""
        return name

    @tool(permissions=("read",), resources=("files:read",))
    def read_file(path: str) -> str:
        """Read a file."""
        return path

    @tool(permissions=("write",), resources=("files:write",), sandbox=True)
    def write_file(path: str, content: str) -> str:
        """Write a file."""
        return path

    @tool(permissions=("write",), resources=("files:delete",), sandbox=True)
    def delete_file(path: str) -> str:
        """Delete a file."""
        return path

    @tool(permissions=("read",))
    def list_tools() -> str:
        """List tools (no resource annotation)."""
        return "ok"

    @tool(permissions=("execute",), resources=("shell:execute",), sandbox=True)
    def run_command(cmd: str) -> str:
        """Execute a shell command."""
        return cmd

    reg.register(read_agent, "workflow")
    reg.register(create_agent, "workflow")
    reg.register(update_agent, "workflow")
    reg.register(read_file, "filesystem")
    reg.register(write_file, "filesystem")
    reg.register(delete_file, "filesystem")
    reg.register(list_tools, "meta")
    reg.register(run_command, "shell")

    return reg


def _tool_names(defs: list[dict]) -> set[str]:
    return {d["function"]["name"] for d in defs}


# =====================================================================
# Layer 1: ToolRegistry.mcp_definitions filtering
# =====================================================================


class TestMcpDefinitionsResourceFiltering:
    """Verify that mcp_definitions respects allowed_resources."""

    def test_no_filter_returns_all(self):
        reg = _build_registry()
        defs = reg.mcp_definitions()
        assert len(defs) == 8

    def test_allowed_resources_none_returns_all(self):
        reg = _build_registry()
        defs = reg.mcp_definitions(allowed_resources=None)
        assert len(defs) == 8

    def test_tools_without_resources_always_pass(self):
        """Tools with no declared resources pass even with a restrictive allowed set."""
        reg = _build_registry()
        defs = reg.mcp_definitions(allowed_resources=set())
        names = _tool_names(defs)
        assert "meta.list_tools" in names

    def test_exact_subset_passes(self):
        reg = _build_registry()
        defs = reg.mcp_definitions(
            allowed_resources={"agents:read", "agents:create"},
        )
        names = _tool_names(defs)
        assert "workflow.read_agent" in names
        assert "workflow.create_agent" in names

    def test_superset_passes(self):
        """Having more permissions than required is fine."""
        reg = _build_registry()
        defs = reg.mcp_definitions(
            allowed_resources={"agents:read", "agents:create", "agents:update", "extra:perm"},
        )
        names = _tool_names(defs)
        assert "workflow.read_agent" in names
        assert "workflow.create_agent" in names
        assert "workflow.update_agent" in names

    def test_missing_resource_excludes_tool(self):
        """A tool is excluded when its resources are not a subset of allowed."""
        reg = _build_registry()
        defs = reg.mcp_definitions(
            allowed_resources={"agents:read"},
        )
        names = _tool_names(defs)
        assert "workflow.read_agent" in names
        assert "workflow.create_agent" not in names
        assert "workflow.update_agent" not in names

    def test_empty_allowed_resources_excludes_all_annotated(self):
        """An empty allowed_resources set blocks every tool that declares resources."""
        reg = _build_registry()
        defs = reg.mcp_definitions(allowed_resources=set())
        names = _tool_names(defs)
        assert names == {"meta.list_tools"}

    def test_cross_namespace_filtering(self):
        """Resources from different namespaces are correctly handled."""
        reg = _build_registry()
        defs = reg.mcp_definitions(
            allowed_resources={"agents:read", "files:read"},
        )
        names = _tool_names(defs)
        assert "workflow.read_agent" in names
        assert "filesystem.read_file" in names
        assert "workflow.create_agent" not in names
        assert "filesystem.write_file" not in names
        assert "filesystem.delete_file" not in names
        assert "meta.list_tools" in names

    def test_namespace_plus_resource_filter(self):
        """Namespace and resource filters are applied together."""
        reg = _build_registry()
        defs = reg.mcp_definitions(
            namespaces=["workflow"],
            allowed_resources={"agents:read"},
        )
        names = _tool_names(defs)
        assert names == {"workflow.read_agent"}

    def test_permission_plus_resource_filter(self):
        """Global permission and resource filters are applied together."""
        reg = _build_registry()
        defs = reg.mcp_definitions(
            allowed_permissions={"read"},
            allowed_resources={"agents:read", "files:read"},
        )
        names = _tool_names(defs)
        assert "workflow.read_agent" in names
        assert "filesystem.read_file" in names
        assert "meta.list_tools" in names
        # write-permission tools excluded by allowed_permissions
        assert "workflow.create_agent" not in names
        assert "filesystem.write_file" not in names

    def test_resource_field_included_in_output(self):
        """Each MCP definition includes the resources list."""
        reg = _build_registry()
        defs = reg.mcp_definitions()
        by_name = {d["function"]["name"]: d for d in defs}

        assert by_name["workflow.read_agent"]["function"]["resources"] == ["agents:read"]
        assert by_name["workflow.create_agent"]["function"]["resources"] == ["agents:create"]
        assert by_name["meta.list_tools"]["function"]["resources"] == []


# =====================================================================
# Layer 2: TaskGraph._resolve_tools / dispatch_tool enforcement
# =====================================================================


class TestTaskGraphResourceEnforcement:
    """Verify that TaskGraph blocks tools excluded by resource permissions."""

    def _make_graph(self, registry):
        """Instantiate a minimal TaskGraph wired to the given registry."""
        from acai.orchestrator.agent_store import AgentStore
        from acai.orchestrator.chat import ChatStore
        from acai.orchestrator.config import AcaiConfig
        from acai.orchestrator.stream import StreamTracker
        from acai.tasks.graph import TaskGraph
        import tempfile, os

        tmp = tempfile.mkdtemp()
        ws_agents = os.path.join(tmp, "agents")
        os.makedirs(ws_agents, exist_ok=True)

        store = AgentStore(ws_agents)
        chat = ChatStore(tmp)
        config = AcaiConfig(workspace=tmp)
        tracker = StreamTracker()

        class FakeWorker:
            url = "http://localhost:0/worker"

        graph = TaskGraph(
            worker=FakeWorker(),
            agent_store=store,
            chat=chat,
            config=config,
            tracker=tracker,
            projects=None,
            tool_registry=registry,
        )
        return graph

    def test_resolve_tools_with_full_resources(self):
        """When agent has all resource permissions, all annotated tools are available."""
        from acai.orchestrator.agent_store import AgentDef

        reg = _build_registry()
        agent = AgentDef(
            name="full-access",
            tools=["workflow", "filesystem", "meta", "shell"],
            tool_permissions=["read", "write", "execute"],
            resource_permissions=[
                "agents:read", "agents:create", "agents:update",
                "files:read", "files:write", "files:delete",
                "shell:execute",
            ],
        )

        graph = self._make_graph(reg)
        tool_defs, desc = graph._resolve_tools(agent)

        assert tool_defs is not None
        names = _tool_names(tool_defs)
        assert len(names) == 8
        assert "workflow.read_agent" in names
        assert "filesystem.write_file" in names

    def test_resolve_tools_restricts_by_resources(self):
        """Agent with partial resource permissions sees only allowed tools."""
        from acai.orchestrator.agent_store import AgentDef

        reg = _build_registry()
        agent = AgentDef(
            name="read-only-agent",
            tools=["workflow", "filesystem", "meta"],
            tool_permissions=["read", "write"],
            resource_permissions=["agents:read", "files:read"],
        )

        graph = self._make_graph(reg)
        tool_defs, desc = graph._resolve_tools(agent)

        names = _tool_names(tool_defs)
        assert "workflow.read_agent" in names
        assert "filesystem.read_file" in names
        assert "meta.list_tools" in names
        assert "workflow.create_agent" not in names
        assert "workflow.update_agent" not in names
        assert "filesystem.write_file" not in names
        assert "filesystem.delete_file" not in names

    def test_resolve_tools_populates_allowed_tools(self):
        """_allowed_tools is set to the filtered tool names."""
        from acai.orchestrator.agent_store import AgentDef

        reg = _build_registry()
        agent = AgentDef(
            name="restricted",
            tools=["workflow"],
            tool_permissions=["read"],
            resource_permissions=["agents:read"],
        )

        graph = self._make_graph(reg)
        graph._resolve_tools(agent)

        assert graph._allowed_tools == {"workflow.read_agent"}

    @pytest.mark.asyncio
    async def test_dispatch_tool_blocks_disallowed(self):
        """dispatch_tool returns an error for tools not in _allowed_tools."""
        from acai.orchestrator.agent_store import AgentDef

        reg = _build_registry()
        agent = AgentDef(
            name="restricted",
            tools=["workflow", "filesystem"],
            tool_permissions=["read", "write"],
            resource_permissions=["agents:read", "files:read"],
        )

        graph = self._make_graph(reg)
        graph._resolve_tools(agent)

        result = await graph.dispatch_tool("workflow.create_agent", {"name": "evil"})
        assert "not permitted" in result
        assert "workflow.create_agent" in result

    @pytest.mark.asyncio
    async def test_dispatch_tool_blocks_unknown_tool(self):
        """dispatch_tool blocks a completely unknown tool name."""
        from acai.orchestrator.agent_store import AgentDef

        reg = _build_registry()
        agent = AgentDef(
            name="restricted",
            tools=["workflow"],
            tool_permissions=["read"],
            resource_permissions=["agents:read"],
        )

        graph = self._make_graph(reg)
        graph._resolve_tools(agent)

        result = await graph.dispatch_tool("admin.destroy_everything", {})
        assert "not permitted" in result

    def test_empty_resource_permissions_means_no_filter(self):
        """An empty resource_permissions list is treated as 'no filter' (backward compat).

        In _resolve_tools, ``[] → falsy → allowed_res = None``, so no
        resource filtering occurs.  This ensures legacy agents that
        predate the resource permission system keep working.
        """
        from acai.orchestrator.agent_store import AgentDef

        reg = _build_registry()
        agent = AgentDef(
            name="no-resources",
            tools=["workflow", "filesystem", "meta"],
            tool_permissions=["read", "write"],
            resource_permissions=[],
        )

        graph = self._make_graph(reg)
        tool_defs, _ = graph._resolve_tools(agent)

        assert tool_defs is not None
        names = _tool_names(tool_defs)
        assert "workflow.read_agent" in names
        assert "workflow.create_agent" in names
        assert "meta.list_tools" in names

    def test_no_resource_permissions_field_means_no_filter(self):
        """When resource_permissions is not set (None-ish), no resource filtering occurs."""
        from acai.orchestrator.agent_store import AgentDef

        reg = _build_registry()
        agent = AgentDef(
            name="legacy-agent",
            tools=["workflow", "filesystem", "meta", "shell"],
            tool_permissions=["read", "write", "execute"],
        )
        # AgentDef defaults resource_permissions to []
        # In _resolve_tools, empty list → allowed_res = None → no filtering
        assert agent.resource_permissions == []

        graph = self._make_graph(reg)
        tool_defs, _ = graph._resolve_tools(agent)

        names = _tool_names(tool_defs)
        assert len(names) == 8, "all tools should be available when resource_permissions is empty (no filter)"


# =====================================================================
# Resource permission discovery (registry.resource_permissions)
# =====================================================================


class TestResourcePermissionDiscovery:
    """Verify that resource_permissions() introspection works correctly."""

    def test_all_resources(self):
        reg = _build_registry()
        perms = reg.resource_permissions()
        assert "agents:read" in perms
        assert "agents:create" in perms
        assert "files:read" in perms
        assert "files:write" in perms
        assert "files:delete" in perms
        assert "shell:execute" in perms

    def test_namespace_scoped(self):
        reg = _build_registry()
        perms = reg.resource_permissions("workflow")
        assert perms == ["agents:create", "agents:read", "agents:update"]

    def test_namespace_without_resources(self):
        reg = _build_registry()
        perms = reg.resource_permissions("meta")
        assert perms == []


# =====================================================================
# Scope enforcement (agent scope + tool scope)
# =====================================================================


def _build_scoped_registry() -> ToolRegistry:
    """Registry with scope-annotated workflow tools and an unscoped tool."""
    reg = ToolRegistry()

    @tool(permissions=("write",), resources=("workflows:update",), scope="project:workflow_id")
    def update_workflow(workflow_id: str, spec: str) -> str:
        """Update a workflow."""
        return spec

    @tool(permissions=("read",), resources=("workflows:validate",), scope="project:workflow_id")
    def get_diagnostics(workflow_id: str) -> str:
        """Validate a workflow."""
        return workflow_id

    @tool(permissions=("read",), resources=("agents:read",), scope="project:workflow_id")
    def read_agent(workflow_id: str, agent_name: str) -> str:
        """Read an agent from a workflow."""
        return agent_name

    @tool(permissions=("read",))
    def list_tools() -> str:
        """List tools (global, unscoped)."""
        return "ok"

    @tool(permissions=("read",), resources=("files:read",))
    def read_file(path: str) -> str:
        """Read a file (unscoped)."""
        return path

    reg.register(update_workflow, "workflow")
    reg.register(get_diagnostics, "workflow")
    reg.register(read_agent, "workflow")
    reg.register(list_tools, "meta")
    reg.register(read_file, "filesystem")

    return reg


class TestScopeEnforcement:
    """Verify that agent scope + tool scope restricts tool arguments."""

    def _make_graph(self, registry):
        """Instantiate a minimal TaskGraph for scope testing."""
        from acai.orchestrator.agent_store import AgentStore
        from acai.orchestrator.chat import ChatStore
        from acai.orchestrator.config import AcaiConfig
        from acai.orchestrator.stream import StreamTracker
        from acai.tasks.graph import TaskGraph
        import tempfile, os

        tmp = tempfile.mkdtemp()
        ws_agents = os.path.join(tmp, "agents")
        os.makedirs(ws_agents, exist_ok=True)

        store = AgentStore(ws_agents)
        chat = ChatStore(tmp)
        config = AcaiConfig(workspace=tmp)
        tracker = StreamTracker()

        class FakeWorker:
            url = "http://localhost:0/worker"

        graph = TaskGraph(
            worker=FakeWorker(),
            agent_store=store,
            chat=chat,
            config=config,
            tracker=tracker,
            projects=None,
            tool_registry=registry,
        )
        return graph

    # --- scope=global agent: no restrictions ---

    @pytest.mark.asyncio
    async def test_global_scope_allows_any_workflow_id(self):
        """An agent with scope=global can target any workflow_id."""
        from acai.orchestrator.agent_store import AgentDef

        reg = _build_scoped_registry()
        agent = AgentDef(
            name="global-agent",
            tools=["workflow", "meta", "filesystem"],
            tool_permissions=["read", "write"],
            scope="global",
        )

        graph = self._make_graph(reg)
        graph._resolve_tools(agent)
        graph._agent_scope = "global"
        graph._scope_context = {"workflow_id": "my-workflow"}

        result = graph._check_scope("workflow.update_workflow", {"workflow_id": "other-workflow", "spec": "{}"})
        assert result == ""

    # --- scope=project agent: validates scope key ---

    @pytest.mark.asyncio
    async def test_project_scope_blocks_mismatched_workflow_id(self):
        """A project-scoped agent is blocked when workflow_id doesn't match."""
        from acai.orchestrator.agent_store import AgentDef

        reg = _build_scoped_registry()
        agent = AgentDef(
            name="project-agent",
            tools=["workflow", "meta"],
            tool_permissions=["read", "write"],
            scope="project",
        )

        graph = self._make_graph(reg)
        graph._resolve_tools(agent)
        graph._agent_scope = "project"
        graph._scope_context = {"workflow_id": "my-workflow"}

        result = graph._check_scope("workflow.update_workflow", {"workflow_id": "other-workflow", "spec": "{}"})
        assert "Scope error" in result
        assert "other-workflow" in result

    @pytest.mark.asyncio
    async def test_project_scope_allows_matching_workflow_id(self):
        """A project-scoped agent can proceed when workflow_id matches."""
        from acai.orchestrator.agent_store import AgentDef

        reg = _build_scoped_registry()
        agent = AgentDef(
            name="project-agent",
            tools=["workflow", "meta"],
            tool_permissions=["read", "write"],
            scope="project",
        )

        graph = self._make_graph(reg)
        graph._resolve_tools(agent)
        graph._agent_scope = "project"
        graph._scope_context = {"workflow_id": "my-workflow"}

        result = graph._check_scope("workflow.update_workflow", {"workflow_id": "my-workflow", "spec": "{}"})
        assert result == ""

    # --- no scope context: graceful fallback ---

    def test_no_scope_context_allows_all(self):
        """When there is no scope context, no enforcement happens."""
        reg = _build_scoped_registry()
        graph = self._make_graph(reg)
        graph._agent_scope = "project"
        graph._scope_context = {}

        result = graph._check_scope("workflow.update_workflow", {"workflow_id": "anything", "spec": "{}"})
        assert result == ""

    def test_no_scope_context_key_allows_all(self):
        """When scope context doesn't contain the tool's scope key, no enforcement."""
        reg = _build_scoped_registry()
        graph = self._make_graph(reg)
        graph._agent_scope = "project"
        graph._scope_context = {"project": "some-project"}

        result = graph._check_scope("workflow.update_workflow", {"workflow_id": "anything", "spec": "{}"})
        assert result == ""

    # --- unscoped tools: unaffected ---

    def test_unscoped_tool_unaffected(self):
        """A tool with no scope is never blocked."""
        reg = _build_scoped_registry()
        graph = self._make_graph(reg)
        graph._agent_scope = "project"
        graph._scope_context = {"workflow_id": "my-workflow"}

        result = graph._check_scope("meta.list_tools", {})
        assert result == ""

    def test_tool_with_resources_but_unscoped(self):
        """A tool with resources but no scope is never blocked."""
        reg = _build_scoped_registry()
        graph = self._make_graph(reg)
        graph._agent_scope = "project"
        graph._scope_context = {"workflow_id": "my-workflow"}

        result = graph._check_scope("filesystem.read_file", {"path": "/etc/passwd"})
        assert result == ""

    # --- dispatch_tool integration ---

    @pytest.mark.asyncio
    async def test_dispatch_tool_blocked_by_scope(self):
        """dispatch_tool returns a scope error before reaching the worker."""
        from acai.orchestrator.agent_store import AgentDef

        reg = _build_scoped_registry()
        agent = AgentDef(
            name="project-agent",
            tools=["workflow", "meta", "filesystem"],
            tool_permissions=["read", "write"],
            scope="project",
        )

        graph = self._make_graph(reg)
        graph._resolve_tools(agent)
        graph._agent_scope = "project"
        graph._scope_context = {"workflow_id": "my-workflow"}

        result = await graph.dispatch_tool(
            "workflow.update_workflow",
            {"workflow_id": "evil-workflow", "spec": "{}"},
        )
        assert "Scope error" in result
        assert "evil-workflow" in result

    # --- build_scope_context ---

    def test_build_scope_context_from_workflow_dir(self):
        """Workflow ID is extracted from workflow_dir basename."""
        reg = _build_scoped_registry()
        graph = self._make_graph(reg)

        ctx = graph._build_scope_context(
            {"workflow_dir": "/path/to/workflows/my-workflow"},
            {},
        )
        assert ctx["workflow_id"] == "my-workflow"

    def test_build_scope_context_from_extra_context(self):
        """workflow_id in extra_context overrides workflow_dir."""
        reg = _build_scoped_registry()
        graph = self._make_graph(reg)

        ctx = graph._build_scope_context(
            {"workflow_dir": "/path/to/workflows/old-workflow"},
            {"extra_context": {"workflow_id": "override-workflow"}},
        )
        assert ctx["workflow_id"] == "override-workflow"

    def test_build_scope_context_from_work_workflow_id(self):
        """Direct workflow_id in work dict takes highest priority."""
        reg = _build_scoped_registry()
        graph = self._make_graph(reg)

        ctx = graph._build_scope_context(
            {"workflow_dir": "/path/to/old", "workflow_id": "explicit-id"},
            {},
        )
        assert ctx["workflow_id"] == "explicit-id"

    def test_build_scope_context_includes_project(self):
        """Project name is captured when present."""
        reg = _build_scoped_registry()
        graph = self._make_graph(reg)

        ctx = graph._build_scope_context(
            {"project": "my-project"},
            {},
        )
        assert ctx["project"] == "my-project"
        assert "workflow_id" not in ctx

    def test_build_scope_context_empty_when_no_context(self):
        """Empty work produces empty scope context."""
        reg = _build_scoped_registry()
        graph = self._make_graph(reg)

        ctx = graph._build_scope_context({}, {})
        assert ctx == {}


# =====================================================================
# _parse_scope utility
# =====================================================================


class TestParseScope:
    """Verify the ``level:key`` scope string parser."""

    def test_project_with_key(self):
        assert _parse_scope("project:workflow_id") == ("project", "workflow_id")

    def test_global_with_key(self):
        assert _parse_scope("global:some_key") == ("global", "some_key")

    def test_global_bare(self):
        assert _parse_scope("global") == ("global", "")

    def test_project_bare(self):
        assert _parse_scope("project") == ("project", "")

    def test_empty_string(self):
        assert _parse_scope("") == ("global", "")

    def test_invalid_level(self):
        assert _parse_scope("invalid:key") == ("global", "")

    def test_scope_on_tooldef(self):
        """ToolDef.scope_level and scope_key parse from the scope string."""
        reg = ToolRegistry()

        @tool(permissions=("read",), scope="project:workflow_id")
        def scoped_tool(workflow_id: str) -> str:
            """A scoped tool."""
            return workflow_id

        @tool(permissions=("read",))
        def unscoped_tool() -> str:
            """An unscoped tool."""
            return "ok"

        reg.register(scoped_tool, "test")
        reg.register(unscoped_tool, "test")

        td = reg.get("test.scoped_tool")
        assert td.scope == "project:workflow_id"
        assert td.scope_level == "project"
        assert td.scope_key == "workflow_id"

        td2 = reg.get("test.unscoped_tool")
        assert td2.scope == ""
        assert td2.scope_level == "global"
        assert td2.scope_key == ""
