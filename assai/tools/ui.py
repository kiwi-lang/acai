"""UI tools — lets the LLM push visual feedback to the user's browser.

The orchestrator client is obtained from the worker context
(see :mod:`assai.orchestrator.context`).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from assai.orchestrator.context import current_client

log = logging.getLogger(__name__)


def toast(
    message: str,
    title: Optional[str] = None,
    status: str = "info",
    duration: int = 5000,
) -> str:
    """Display a toast notification in the user's browser.
    Only use it when the user need to grab the user's attention.
    Use Sparingly.

    Args:
        message: The notification body text.
        title: Optional heading shown above the message.
        status: Severity level — one of "info", "success", "warning", "error".
        duration: How long the toast stays visible (milliseconds).
    """
    if status not in ("info", "success", "warning", "error"):
        status = "info"

    client = current_client()
    if client is None:
        log.warning("ui.toast called but no orchestrator client in context")
        return json.dumps({"error": "orchestrator client not available"})

    try:
        result = client.post("/toast", {
            "message": message,
            "title": title or "",
            "status": status,
            "duration": duration,
        }, timeout=5)
        return json.dumps(result)
    except Exception as exc:
        log.exception("ui.toast failed")
        return json.dumps({"error": str(exc)})
