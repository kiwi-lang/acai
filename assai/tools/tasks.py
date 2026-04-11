"""Task management tools — lets agents create and manipulate tasks.

The orchestrator URL must be set via :func:`configure` before any tool
is invoked.  The worker does this during initialisation.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import requests as http

log = logging.getLogger(__name__)

_orchestrator_url: str = ""


def _configure(orchestrator_url: str) -> None:
    """Set the orchestrator base URL so tools can reach it."""
    global _orchestrator_url
    _orchestrator_url = orchestrator_url.rstrip("/")


def _post(path: str, payload: dict) -> dict:
    resp = http.post(f"{_orchestrator_url}{path}", json=payload, timeout=15)
    return resp.json()


def _get(path: str, params: dict | None = None) -> dict | list:
    resp = http.get(f"{_orchestrator_url}{path}", params=params, timeout=15)
    return resp.json()


def _patch(path: str, payload: dict) -> dict:
    resp = http.patch(f"{_orchestrator_url}{path}", json=payload, timeout=15)
    return resp.json()


def create(
    title: str,
    description: str = "",
    project: str = "",
    priority: int = 0,
    agent: str = "",
    kind: str = "task",
) -> str:
    """Create a new task in the work queue.

    Args:
        title: Short summary of the task.
        description: Detailed description with acceptance criteria, context, etc.
        project: Project name this task belongs to.
        priority: Higher values are picked up first.
        agent: Agent to assign (e.g. "coder"). Leave empty for default.
        kind: Task kind — "task" for general, "work" for implementation work.
    """
    if not _orchestrator_url:
        return json.dumps({"error": "orchestrator URL not configured"})

    try:
        result = _post("/tasks", {
            "title": title,
            "description": description,
            "project": project,
            "priority": priority,
            "agent": agent,
            "kind": kind,
        })
        return json.dumps(result)
    except Exception as exc:
        log.exception("tasks.create failed")
        return json.dumps({"error": str(exc)})


def update(
    task_id: str,
    title: str = "",
    description: str = "",
    status: str = "",
    priority: int = -1,
    agent: str = "",
) -> str:
    """Update fields on an existing task.

    Args:
        task_id: The task ID to update.
        title: New title (leave empty to keep current).
        description: New description (leave empty to keep current).
        status: New status — "pending", "ready", "in_progress", "completed", "failed".
        priority: New priority (-1 to keep current).
        agent: New agent assignment (leave empty to keep current).
    """
    if not _orchestrator_url:
        return json.dumps({"error": "orchestrator URL not configured"})

    fields: dict = {}
    if title:
        fields["title"] = title
    if description:
        fields["description"] = description
    if status:
        fields["status"] = status
    if priority >= 0:
        fields["priority"] = priority
    if agent:
        fields["agent"] = agent

    if not fields:
        return json.dumps({"error": "no fields to update"})

    try:
        result = _patch(f"/tasks/{task_id}", fields)
        return json.dumps(result)
    except Exception as exc:
        log.exception("tasks.update failed")
        return json.dumps({"error": str(exc)})


def list_tasks(
    project: str = "",
    status: str = "",
) -> str:
    """List tasks, optionally filtered by project and/or status.

    Args:
        project: Filter by project name (empty for all projects).
        status: Filter by status (empty for all statuses).
    """
    if not _orchestrator_url:
        return json.dumps({"error": "orchestrator URL not configured"})

    try:
        params: dict = {}
        if project:
            params["project"] = project
        if status:
            params["status"] = status
        result = _get("/tasks", params)
        return json.dumps(result)
    except Exception as exc:
        log.exception("tasks.list_tasks failed")
        return json.dumps({"error": str(exc)})


def get(task_id: str) -> str:
    """Get full details of a specific task.

    Args:
        task_id: The task ID to retrieve.
    """
    if not _orchestrator_url:
        return json.dumps({"error": "orchestrator URL not configured"})

    try:
        result = _get(f"/tasks/{task_id}")
        return json.dumps(result)
    except Exception as exc:
        log.exception("tasks.get failed")
        return json.dumps({"error": str(exc)})


def mark_ready(task_id: str) -> str:
    """Mark a task as ready to be picked up by a worker.

    Use this when the task specification is complete and the task
    should be queued for implementation.

    Args:
        task_id: The task ID to mark as ready.
    """
    if not _orchestrator_url:
        return json.dumps({"error": "orchestrator URL not configured"})

    try:
        result = _patch(f"/tasks/{task_id}", {"status": "ready"})
        return json.dumps(result)
    except Exception as exc:
        log.exception("tasks.mark_ready failed")
        return json.dumps({"error": str(exc)})
