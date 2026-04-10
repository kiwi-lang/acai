"""UI tools — lets the LLM push visual feedback to the user's browser.

The orchestrator URL must be set via :func:`configure` before any tool
is invoked.  The worker does this during initialisation.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import requests as http

from assai.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

registry = ToolRegistry()

_orchestrator_url: str = ""


def configure(orchestrator_url: str) -> None:
    """Set the orchestrator base URL so tools can reach it."""
    global _orchestrator_url
    _orchestrator_url = orchestrator_url.rstrip("/")


@registry.tool("ui")
def toast(
    message: str,
    title: Optional[str] = None,
    status: str = "info",
    duration: int = 5000,
) -> str:
    """Display a toast notification in the user's browser.

    Args:
        message: The notification body text.
        title: Optional heading shown above the message.
        status: Severity level — one of "info", "success", "warning", "error".
        duration: How long the toast stays visible (milliseconds).
    """
    if status not in ("info", "success", "warning", "error"):
        status = "info"

    if not _orchestrator_url:
        log.warning("ui.toast called but orchestrator URL not configured")
        return json.dumps({"error": "orchestrator URL not configured"})

    payload = {
        "message": message,
        "title": title or "",
        "status": status,
        "duration": duration,
    }

    try:
        resp = http.post(
            f"{_orchestrator_url}/toast",
            json=payload,
            timeout=5,
        )
        if resp.status_code == 200:
            return json.dumps({"ok": True})
        return json.dumps({"error": f"HTTP {resp.status_code}"})
    except Exception as exc:
        log.exception("ui.toast failed to reach orchestrator")
        return json.dumps({"error": str(exc)})
