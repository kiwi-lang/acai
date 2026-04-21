"""Make tools — discover and run Makefile recipes.

Exposes project Makefiles as structured workflows so agents can
list available targets and invoke them without hand-crafting shell
commands.
"""

from __future__ import annotations

import json
import os
import subprocess

from acai.orchestrator.tools import tool


@tool(permissions=("read",))
def list_targets(cwd: str = ".", makefile: str = "Makefile") -> str:
    """List all targets defined in a Makefile with their commands.

    Args:
        cwd: Directory containing the Makefile.
        makefile: Makefile name (default ``Makefile``).
    """
    path = os.path.join(os.path.abspath(cwd), makefile)
    if not os.path.isfile(path):
        return json.dumps({"error": f"no {makefile} found in {os.path.abspath(cwd)}"})

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        return json.dumps({"error": str(exc)})

    targets = _parse_targets(content)
    return json.dumps({"makefile": path, "targets": targets, "count": len(targets)})


@tool(permissions=("execute",))
def run_target(
    target: str,
    cwd: str = ".",
    makefile: str = "Makefile",
    variables: str = "",
    timeout: int = 600,
) -> str:
    """Run a Makefile target.

    Args:
        target: The make target to run (e.g. ``tests``, ``install``).
        cwd: Directory containing the Makefile.
        makefile: Makefile name (default ``Makefile``).
        variables: Space-separated make variable overrides (e.g. ``FILE=foo.py CC=gcc``).
        timeout: Maximum seconds before the command is killed.
    """
    abs_cwd = os.path.abspath(cwd)
    path = os.path.join(abs_cwd, makefile)
    if not os.path.isfile(path):
        return json.dumps({"error": f"no {makefile} found in {abs_cwd}"})

    cmd = ["make", "-f", makefile, target]
    if variables:
        cmd.extend(variables.split())

    try:
        proc = subprocess.run(
            cmd,
            cwd=abs_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout
        stderr = proc.stderr
        if len(stdout) > 8000:
            stdout = stdout[:4000] + "\n... (truncated) ...\n" + stdout[-4000:]
        if len(stderr) > 4000:
            stderr = stderr[:2000] + "\n... (truncated) ...\n" + stderr[-2000:]

        return json.dumps({
            "target": target,
            "command": cmd,
            "returncode": proc.returncode,
            "success": proc.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"timed out after {timeout}s", "target": target})
    except OSError as exc:
        return json.dumps({"error": str(exc)})


def _parse_targets(content: str) -> list[dict]:
    """Extract targets, their dependencies, and recipe lines from Makefile text."""
    targets = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("#") or line.startswith("\t") or not line.strip():
            i += 1
            continue

        if ":" in line and not line.startswith("."):
            colon_idx = line.index(":")
            # Skip variable assignments like VAR := value
            if colon_idx + 1 < len(line) and line[colon_idx + 1] == "=":
                i += 1
                continue

            name = line[:colon_idx].strip()
            # Skip targets with variable expansions like $(...)
            if "$(" in name or "${" in name:
                i += 1
                continue

            deps = line[colon_idx + 1:].strip()

            # Collect comment block above as description
            desc_lines = []
            j = i - 1
            while j >= 0 and lines[j].startswith("#"):
                desc_lines.insert(0, lines[j].lstrip("# ").strip())
                j -= 1
            description = " ".join(desc_lines).strip()

            # Collect recipe lines
            recipe = []
            i += 1
            while i < len(lines) and lines[i].startswith("\t"):
                recipe.append(lines[i][1:])  # strip leading tab
                i += 1

            targets.append({
                "name": name,
                "deps": deps,
                "description": description,
                "recipe": recipe,
            })
            continue

        i += 1

    return targets
