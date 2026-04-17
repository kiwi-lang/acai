"""Shell tool — execute arbitrary commands."""

from __future__ import annotations

import json
import subprocess
from typing import Optional

from assai.orchestrator.tools import tool


@tool(permissions=("execute",))
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
