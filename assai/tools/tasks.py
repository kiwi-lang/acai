"""Task management tools — lets agents create and manipulate tasks.

Job metadata (project, conversation, …) and the orchestrator client
are available via :func:`assai.orchestrator.context.current_context`.
"""

from __future__ import annotations

import json
import logging
import requests as http
from dataclasses import dataclass

from assai.orchestrator.context import current_context, current_client
from assai.orchestrator.tools import tool

@dataclass
class Task:
    title: str
    context: str
    subtasks: list[Task]



log = logging.getLogger(__name__)


def _require_client():
    client = current_client()
    if client is None:
        raise RuntimeError("no orchestrator client in worker context")
    return client


@tool(permissions=("write",))
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
        project: Project name (defaults to the current job's project).
        priority: Higher values are picked up first.
        agent: Agent to assign (e.g. "coder"). Leave empty for default.
        kind: Task kind — "task" for general, "work" for implementation work.
    """
    try:
        client = _require_client()
        ctx = current_context()
        if not project and ctx is not None:
            project = ctx.project
        result = client.post("/tasks", {
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


@tool(permissions=("write",))
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
        client = _require_client()
        result = client.patch(f"/tasks/{task_id}", fields)
        return json.dumps(result)
    except Exception as exc:
        log.exception("tasks.update failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",))
def list_tasks(
    project: str = "",
    status: str = "",
) -> str:
    """List tasks, optionally filtered by project and/or status.

    Args:
        project: Filter by project name (empty for all projects).
        status: Filter by status (empty for all statuses).
    """
    try:
        client = _require_client()
        params: dict[str, str] = {}
        if project:
            params["project"] = project
        if status:
            params["status"] = status
        result = client.get("/tasks", params)
        return json.dumps(result)
    except Exception as exc:
        log.exception("tasks.list_tasks failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",))
def get(task_id: str) -> str:
    """Get full details of a specific task.

    Args:
        task_id: The task ID to retrieve.
    """
    try:
        client = _require_client()
        result = client.get(f"/tasks/{task_id}")
        return json.dumps(result)
    except Exception as exc:
        log.exception("tasks.get failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",))
def mark_ready(task_id: str) -> str:
    """Mark a task as ready to be picked up by a worker.

    Use this when the task specification is complete and the task
    should be queued for implementation.

    Args:
        task_id: The task ID to mark as ready.
    """
    try:
        client = _require_client()
        result = client.patch(f"/tasks/{task_id}", {"status": "ready"})
        return json.dumps(result)
    except Exception as exc:
        log.exception("tasks.mark_ready failed")
        return json.dumps({"error": str(exc)})
