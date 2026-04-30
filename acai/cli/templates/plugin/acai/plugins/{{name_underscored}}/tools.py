"""Example tools for the {{name}} plugin."""

from __future__ import annotations

from acai.orchestrator.tools import tool


@tool(permissions=("read",))
def hello(who: str = "world") -> str:
    """Say hello.

    Args:
        who: Name to greet.
    """
    return f"Hello, {who}! (from {{name}} plugin)"
