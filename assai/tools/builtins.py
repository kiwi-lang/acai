"""Built-in tools for the worker — filesystem operations and shell execution.

Each function is registered via ``@registry.tool(namespace)`` so the worker
discovers them automatically through the shared :data:`registry` instance.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

from assai.tools.registry import ToolRegistry

registry = ToolRegistry()


# ---------------------------------------------------------------------------
# shell
# ---------------------------------------------------------------------------

@registry.tool("shell", gpu=False)
def run(command: str, cwd: Optional[str] = None, timeout: int = 300) -> str:
    """Execute a shell command and return its output.

    Args:
        command: The shell command to execute.
        cwd: Working directory for the command.
        timeout: Maximum seconds before the command is killed.
    """
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        return json.dumps({
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "timeout", "timeout": timeout})


# ---------------------------------------------------------------------------
# filesystem
# ---------------------------------------------------------------------------

@registry.tool("filesystem")
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


@registry.tool("filesystem")
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


@registry.tool("filesystem")
def list_directory(path: str = ".") -> str:
    """List the entries in a directory.

    Args:
        path: Directory path to list.
    """
    try:
        entries = sorted(os.listdir(path))
        return json.dumps(entries)
    except OSError as exc:
        return json.dumps({"error": str(exc)})


@registry.tool("filesystem")
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


@registry.tool("filesystem")
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


@registry.tool("filesystem")
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
