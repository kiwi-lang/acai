"""Per-request worker context using ``contextvars``.

The worker sets a :class:`WorkerContext` before dispatching each tool
call.  Tools access it via :func:`current_context` to get job metadata
(project, conversation, task_id, …) and an :class:`OrchestratorClient`
for scheduling new tasks, sending toasts, etc.

This replaces the old pattern of per-module ``_orchestrator_url`` globals.
"""

from __future__ import annotations

import contextvars
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import requests as http

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Orchestrator client
# ------------------------------------------------------------------

class OrchestratorClient:
    """Thin HTTP client for the orchestrator API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _post(self, path: str, payload: dict, timeout: int = 15) -> dict:
        resp = http.post(f"{self.base_url}{path}", json=payload, timeout=timeout)
        return resp.json()

    def _get(self, path: str, params: dict | None = None, timeout: int = 15) -> Any:
        resp = http.get(f"{self.base_url}{path}", params=params, timeout=timeout)
        return resp.json()

    def _put(self, path: str, payload: dict, timeout: int = 15) -> dict:
        resp = http.put(f"{self.base_url}{path}", json=payload, timeout=timeout)
        return resp.json()

    def _patch(self, path: str, payload: dict, timeout: int = 15) -> dict:
        resp = http.patch(f"{self.base_url}{path}", json=payload, timeout=timeout)
        return resp.json()

    def post(self, path: str, payload: dict, timeout: int = 15) -> dict:
        return self._post(path, payload, timeout)

    def post_sse(self, path: str, payload: dict, timeout: int = 120) -> str:
        """POST and consume an SSE stream, returning the accumulated text."""
        resp = http.post(
            f"{self.base_url}{path}", json=payload,
            stream=True, timeout=timeout,
        )
        parts: list[str] = []
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data: "):
                continue
            try:
                evt = json.loads(raw_line[6:])
            except Exception:
                continue
            token = evt.get("token") or evt.get("content") or ""
            if token:
                parts.append(token)
            msg = evt.get("message")
            if isinstance(msg, str) and msg:
                parts.append(msg)
        resp.close()
        return "".join(parts)

    def put(self, path: str, payload: dict, timeout: int = 15) -> dict:
        return self._put(path, payload, timeout)

    def get(self, path: str, params: dict | None = None, timeout: int = 15) -> Any:
        return self._get(path, params, timeout)

    def patch(self, path: str, payload: dict, timeout: int = 15) -> dict:
        return self._patch(path, payload, timeout)


# ------------------------------------------------------------------
# Worker context
# ------------------------------------------------------------------

@dataclass
class WorkerContext:
    """Per-job context available to tools via :func:`current_context`."""

    task_id: str = ""
    kind: str = ""
    project: str = ""
    conversation: str = ""
    agent: str = ""
    client: OrchestratorClient | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_work(cls, work: dict,
                  client: OrchestratorClient | None = None) -> WorkerContext:
        _known = {"task_id", "kind", "project_name", "project",
                  "conversation", "agent", "orchestrator_url"}
        extra = {k: v for k, v in work.items() if k not in _known}
        return cls(
            task_id=work.get("task_id", ""),
            kind=work.get("kind", ""),
            project=work.get("project_name", "") or work.get("project", ""),
            conversation=work.get("conversation", ""),
            agent=work.get("agent", ""),
            client=client,
            extra=extra,
        )


# ------------------------------------------------------------------
# ContextVar plumbing
# ------------------------------------------------------------------

_worker_ctx: contextvars.ContextVar[WorkerContext | None] = contextvars.ContextVar(
    "worker_ctx", default=None,
)


def current_context() -> WorkerContext | None:
    """Return the active :class:`WorkerContext`, or ``None`` outside a job."""
    return _worker_ctx.get()


def current_client() -> OrchestratorClient | None:
    """Shortcut: return the orchestrator client from the active context."""
    ctx = _worker_ctx.get()
    return ctx.client if ctx else None


def set_context(ctx: WorkerContext) -> contextvars.Token:
    """Install *ctx* as the active worker context.  Returns a reset token."""
    return _worker_ctx.set(ctx)


def reset_context(token: contextvars.Token) -> None:
    """Restore the previous context using the token from :func:`set_context`."""
    _worker_ctx.reset(token)
