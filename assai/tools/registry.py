"""Backward-compatibility shim — canonical location is :mod:`assai.core.tools`."""

from assai.core.tools import ToolDef, ToolRegistry, discover_tools, tool  # noqa: F401

__all__ = ["ToolDef", "ToolRegistry", "discover_tools", "tool"]
