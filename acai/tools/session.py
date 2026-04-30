"""Session-scoped tools — local checklist state (not orchestrator tasks)."""

from __future__ import annotations

import json
import os

from acai.orchestrator.tools import tool

_DEFAULT_NAME = ".acai-session-todos.json"


@tool(permissions=("write",), resources=("session:write",))
def todo_write(cwd: str, todos_json: str, filename: str = "") -> str:
    """Replace the session todo list stored as JSON in the workspace.

    Pass ``todos_json`` as a JSON array of objects, e.g.
    ``[{"content": "...", "status": "pending"}, ...]``.

    Args:
        cwd: Workspace directory where the todo file is written.
        todos_json: JSON array string of todo items.
        filename: Optional file name (default ``.acai-session-todos.json``).
    """
    name = filename.strip() or _DEFAULT_NAME
    path = os.path.join(os.path.abspath(cwd), name)
    try:
        todos = json.loads(todos_json)
        if not isinstance(todos, list):
            return json.dumps({"error": "todos_json must be a JSON array"})
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(todos, f, indent=2, ensure_ascii=False)
        return json.dumps({"ok": True, "path": path, "count": len(todos)})
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid JSON: {exc}"})
    except OSError as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",), resources=("session:read",))
def todo_read(cwd: str, filename: str = "") -> str:
    """Read the session todo list JSON file if it exists.

    Args:
        cwd: Workspace directory.
        filename: Optional file name (default ``.acai-session-todos.json``).
    """
    name = filename.strip() or _DEFAULT_NAME
    path = os.path.join(os.path.abspath(cwd), name)
    if not os.path.isfile(path):
        return json.dumps({"path": path, "todos": [], "exists": False})
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return json.dumps({"path": path, "todos": data, "exists": True})
    except OSError as exc:
        return json.dumps({"error": str(exc)})
