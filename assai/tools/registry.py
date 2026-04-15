"""Backward-compatibility shim — canonical location is :mod:`assai.orchestrator.tools`."""

from assai.orchestrator.tools import ToolDef, ToolRegistry, discover_tools, tool  # noqa: F401

__all__ = ["ToolDef", "ToolRegistry", "discover_tools", "tool"]
