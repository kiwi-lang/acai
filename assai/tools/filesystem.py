"""Filesystem tools — read, write, list, and manage files and directories."""

from __future__ import annotations

import json
import os

from assai.orchestrator.tools import tool


@tool(permissions=("read",))
def read_file(
    path: str,
    encoding: str = "utf-8",
    line_start: int = 0,
    line_limit: int = 0,
) -> str:
    """Read the contents of a file, optionally a slice of lines (1-based).

    Args:
        path: Path to the file.
        encoding: Text encoding to use.
        line_start: First line to include (1-based). Use 0 to read the whole file.
        line_limit: Maximum number of lines to return after line_start. Use 0 for no limit (only applies when line_start > 0).
    """
    try:
        with open(path, encoding=encoding) as f:
            if line_start <= 0:
                return f.read()
            lines = f.readlines()
            start = max(line_start - 1, 0)
            end = len(lines) if line_limit <= 0 else start + line_limit
            chunk = lines[start:end]
            text = "".join(chunk)
            return text
    except OSError as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",))
def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Edit a file by exact string replacement (like a minimal patch).

    Args:
        path: Path to the file.
        old_string: Text to find.
        new_string: Replacement text.
        replace_all: If true, replace every occurrence; otherwise exactly one.
    """
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        if old_string not in content:
            return json.dumps({"error": "old_string not found", "path": path})
        if replace_all:
            new_content = content.replace(old_string, new_string)
            n = content.count(old_string)
        else:
            idx = content.index(old_string)
            new_content = (
                content[:idx] + new_string + content[idx + len(old_string):]
            )
            n = 1
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return json.dumps({"ok": True, "path": path, "replacements": n})
    except OSError as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",))
def write_file(path: str, content: str, encoding: str = "utf-8") -> str:
    """Write content to a file, creating parent directories as needed.

    Args:
        path: Destination file path.
        content: Text content to write.
        encoding: Text encoding to use.
    """
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding=encoding) as f:
            f.write(content)
        return json.dumps({"written": path})
    except OSError as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",))
def list_directory(path: str = ".", recursive: bool = False) -> str:
    """List the entries in a directory.

    Args:
        path: Directory path to list.
        recursive: If true, list all files recursively.
    """
    try:
        if recursive:
            entries = []
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".venv", "node_modules")]
                rel_root = os.path.relpath(root, path)
                for f in files:
                    rel = os.path.join(rel_root, f) if rel_root != "." else f
                    entries.append(rel)
            return json.dumps(sorted(entries))
        entries = sorted(os.listdir(path))
        result = []
        for e in entries:
            fp = os.path.join(path, e)
            result.append({"name": e, "type": "dir" if os.path.isdir(fp) else "file"})
        return json.dumps(result)
    except OSError as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",))
def make_directory(path: str) -> str:
    """Create a directory (and parents) if it does not exist.

    Args:
        path: Directory path to create.
    """
    try:
        os.makedirs(path, exist_ok=True)
        return json.dumps({"created": path})
    except OSError as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",))
def delete_file(path: str) -> str:
    """Delete a file.

    Args:
        path: Path to the file to delete.
    """
    try:
        os.remove(path)
        return json.dumps({"deleted": path})
    except OSError as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",))
def file_info(path: str) -> str:
    """Return size and modification time of a file.

    Args:
        path: Path to the file.
    """
    try:
        stat = os.stat(path)
        return json.dumps({
            "path": path,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "is_dir": os.path.isdir(path),
        })
    except OSError as exc:
        return json.dumps({"error": str(exc)})
