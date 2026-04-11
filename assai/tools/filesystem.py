"""Filesystem tools — read, write, list, and manage files and directories."""

from __future__ import annotations

import json
import os


def read_file(path: str, encoding: str = "utf-8") -> str:
    """Read the contents of a file.

    Args:
        path: Path to the file.
        encoding: Text encoding to use.
    """
    try:
        with open(path, encoding=encoding) as f:
            return f.read()
    except OSError as exc:
        return json.dumps({"error": str(exc)})


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
