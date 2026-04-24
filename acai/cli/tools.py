"""List all discovered tools.

Usage::

    acai tools
"""

from __future__ import annotations

from dataclasses import dataclass

from argklass.command import Command

from acai.cli import CommonArguments


@dataclass
class ToolsArguments(CommonArguments):
    pass


class Tools(Command):
    """List all available tools grouped by namespace."""

    name = "tools"

    Arguments = ToolsArguments

    @staticmethod
    def execute(args) -> int:
        from acai.orchestrator.tools import discover_tools

        registry = discover_tools()
        from acai.tools.meta import _configure as configure_meta_tools

        configure_meta_tools(registry)

        import os
        from acai.orchestrator.skill_store import SkillStore
        from acai.tools.skills import _configure as configure_skills

        workspace = os.environ.get("ACAI_WORKSPACE", "workspace")
        skill_store = SkillStore(os.path.join(workspace, "skills"))
        skill_store.register_all(registry)
        configure_skills(skill_store)

        for ns in registry.namespaces():
            print(ns)
            for td in registry.tools_in(ns):
                first_line = td.description.split("\n", 1)[0] if td.description else ""
                print(f"  {td.name:24s} {first_line}")
            print()

        return 0


COMMANDS = Tools
