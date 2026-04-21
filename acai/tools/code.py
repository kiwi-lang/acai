"""Code tools — search, test, lint, typecheck, and build.

Project-scoped development tools that operate within a worktree.
The worktree path is communicated to the agent via its system prompt
so it can pass it as the ``cwd`` argument.
"""

from __future__ import annotations

import json
import os
import subprocess

from acai.orchestrator.tools import tool


@tool(permissions=("read",))
def search(pattern: str, cwd: str = ".", file_glob: str = "", max_results: int = 50) -> str:
    """Search for a text pattern in project files using grep.

    Args:
        pattern: Regular expression to search for.
        cwd: Project root / worktree directory.
        file_glob: Optional file glob filter (e.g. "*.py").
        max_results: Maximum number of matches to return.
    """
    cmd = ["grep", "-rn", "--color=never"]
    if file_glob:
        cmd += ["--include", file_glob]
    cmd += [pattern, "."]

    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        lines = proc.stdout.strip().splitlines()[:max_results]
        return json.dumps({
            "matches": lines,
            "count": len(lines),
            "truncated": len(proc.stdout.strip().splitlines()) > max_results,
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "search timed out"})
    except OSError as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("execute",), sandbox=True)
def run_tests(cwd: str = ".", command: str = "", timeout: int = 300) -> str:
    """Run the project test suite.

    If no command is specified, auto-detects the test framework
    (pytest for Python, npm test for JS/TS).

    Args:
        cwd: Project root / worktree directory.
        command: Explicit test command to run. Leave empty for auto-detect.
        timeout: Maximum seconds before the test run is killed.
    """
    if not command:
        if os.path.isfile(os.path.join(cwd, "pyproject.toml")) or os.path.isfile(os.path.join(cwd, "setup.py")):
            command = "python -m pytest -x --tb=short -q"
        elif os.path.isfile(os.path.join(cwd, "package.json")):
            command = "npm test"
        elif os.path.isfile(os.path.join(cwd, "Makefile")):
            command = "make test"
        else:
            return json.dumps({"error": "could not detect test framework — provide an explicit command"})

    return _run(command, cwd, timeout)


@tool(permissions=("execute",), sandbox=True)
def lint(cwd: str = ".", command: str = "", timeout: int = 120) -> str:
    """Run the project linter.

    Auto-detects ruff/flake8 for Python, eslint for JS/TS.

    Args:
        cwd: Project root / worktree directory.
        command: Explicit lint command. Leave empty for auto-detect.
        timeout: Maximum seconds before the linter is killed.
    """
    if not command:
        if os.path.isfile(os.path.join(cwd, "pyproject.toml")) or os.path.isfile(os.path.join(cwd, "setup.py")):
            command = "python -m ruff check ."
        elif os.path.isfile(os.path.join(cwd, "package.json")):
            command = "npx eslint ."
        else:
            return json.dumps({"error": "could not detect linter — provide an explicit command"})

    return _run(command, cwd, timeout)


@tool(permissions=("execute",), sandbox=True)
def typecheck(cwd: str = ".", command: str = "", timeout: int = 120) -> str:
    """Run the project type checker.

    Auto-detects mypy/pyright for Python, tsc for TypeScript.

    Args:
        cwd: Project root / worktree directory.
        command: Explicit typecheck command. Leave empty for auto-detect.
        timeout: Maximum seconds before the type checker is killed.
    """
    if not command:
        if os.path.isfile(os.path.join(cwd, "tsconfig.json")):
            command = "npx tsc --noEmit"
        elif os.path.isfile(os.path.join(cwd, "pyproject.toml")) or os.path.isfile(os.path.join(cwd, "setup.py")):
            command = "python -m mypy ."
        else:
            return json.dumps({"error": "could not detect type checker — provide an explicit command"})

    return _run(command, cwd, timeout)


@tool(permissions=("execute",), sandbox=True)
def build(cwd: str = ".", command: str = "", timeout: int = 300) -> str:
    """Build the project.

    Auto-detects common build systems (make, npm, pip).

    Args:
        cwd: Project root / worktree directory.
        command: Explicit build command. Leave empty for auto-detect.
        timeout: Maximum seconds before the build is killed.
    """
    if not command:
        if os.path.isfile(os.path.join(cwd, "Makefile")):
            command = "make"
        elif os.path.isfile(os.path.join(cwd, "package.json")):
            command = "npm run build"
        elif os.path.isfile(os.path.join(cwd, "pyproject.toml")):
            command = "python -m build"
        elif os.path.isfile(os.path.join(cwd, "setup.py")):
            command = "pip install -e ."
        else:
            return json.dumps({"error": "could not detect build system — provide an explicit command"})

    return _run(command, cwd, timeout)


def _run(command: str, cwd: str, timeout: int) -> str:
    """Execute a command and return structured JSON output."""
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        return json.dumps({
            "command": command,
            "stdout": proc.stdout[-4000:] if len(proc.stdout) > 4000 else proc.stdout,
            "stderr": proc.stderr[-2000:] if len(proc.stderr) > 2000 else proc.stderr,
            "returncode": proc.returncode,
            "passed": proc.returncode == 0,
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"timed out after {timeout}s", "command": command})
