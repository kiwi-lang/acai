"""Code tools — search, outline, test, lint, typecheck, and build.

Project-scoped development tools that operate within a worktree.
The worktree path is communicated to the agent via its system prompt
so it can pass it as the ``cwd`` argument.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess

from acai.orchestrator.tools import tool


# ---------------------------------------------------------------------------
# file_outline — AST-based for Python, regex fallback for others
# ---------------------------------------------------------------------------

_LANG_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    ".js":   [
        ("function",  re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)")),
        ("class",     re.compile(r"^(?:export\s+)?class\s+(\w+)")),
        ("variable",  re.compile(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)")),
    ],
    ".ts":   [],  # filled from .js below
    ".tsx":  [],
    ".jsx":  [],
    ".go":   [
        ("function",  re.compile(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)")),
        ("type",      re.compile(r"^type\s+(\w+)")),
        ("variable",  re.compile(r"^var\s+(\w+)")),
    ],
    ".rs":   [
        ("function",  re.compile(r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)")),
        ("struct",    re.compile(r"^(?:pub\s+)?struct\s+(\w+)")),
        ("enum",      re.compile(r"^(?:pub\s+)?enum\s+(\w+)")),
        ("trait",     re.compile(r"^(?:pub\s+)?trait\s+(\w+)")),
        ("impl",      re.compile(r"^impl(?:<[^>]+>)?\s+(\w+)")),
    ],
    ".java": [
        ("class",     re.compile(r"^(?:public|private|protected)?\s*(?:static\s+)?class\s+(\w+)")),
        ("interface", re.compile(r"^(?:public\s+)?interface\s+(\w+)")),
        ("method",    re.compile(r"^\s{2,4}(?:public|private|protected)\s+.*?\s+(\w+)\s*\(")),
    ],
    ".rb":   [
        ("class",     re.compile(r"^class\s+(\w+)")),
        ("module",    re.compile(r"^module\s+(\w+)")),
        ("method",    re.compile(r"^\s*def\s+(\w+)")),
    ],
}
_LANG_PATTERNS[".ts"] = _LANG_PATTERNS[".js"]
_LANG_PATTERNS[".tsx"] = _LANG_PATTERNS[".js"]
_LANG_PATTERNS[".jsx"] = _LANG_PATTERNS[".js"]


def _python_outline(source: str) -> list[dict]:
    """Extract top-level symbols from Python source using the AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [{"error": f"SyntaxError: {exc}"}]

    symbols: list[dict] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            symbols.append({
                "type": "function",
                "name": node.name,
                "args": args,
                "line_start": node.lineno,
                "line_end": node.end_lineno or node.lineno,
            })
        elif isinstance(node, ast.ClassDef):
            members: list[dict] = []
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    members.append({
                        "type": "method",
                        "name": child.name,
                        "line_start": child.lineno,
                        "line_end": child.end_lineno or child.lineno,
                    })
            symbols.append({
                "type": "class",
                "name": node.name,
                "line_start": node.lineno,
                "line_end": node.end_lineno or node.lineno,
                "members": members,
            })
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.append({
                        "type": "variable",
                        "name": target.id,
                        "line_start": node.lineno,
                        "line_end": node.end_lineno or node.lineno,
                    })
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.append({
                "type": "variable",
                "name": node.target.id,
                "line_start": node.lineno,
                "line_end": node.end_lineno or node.lineno,
            })
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            symbols.append({
                "type": "import",
                "name": ", ".join(names),
                "line_start": node.lineno,
                "line_end": node.end_lineno or node.lineno,
            })
        elif isinstance(node, ast.ImportFrom):
            symbols.append({
                "type": "import",
                "name": f"from {node.module or '?'}",
                "line_start": node.lineno,
                "line_end": node.end_lineno or node.lineno,
            })
    return symbols


def _regex_outline(source: str, ext: str) -> list[dict]:
    """Fallback outline using regex patterns keyed by file extension."""
    patterns = _LANG_PATTERNS.get(ext, [])
    if not patterns:
        return []

    symbols: list[dict] = []
    lines = source.splitlines()
    for i, line in enumerate(lines, start=1):
        for kind, pat in patterns:
            m = pat.match(line)
            if m:
                symbols.append({
                    "type": kind,
                    "name": m.group(1),
                    "line_start": i,
                    "line_end": i,
                })
                break
    return symbols


@tool(permissions=("read",), resources=("code:read",))
def file_outline(path: str) -> str:
    """Return the top-level structure of a source file — functions, classes, variables, and their line ranges.

    Use this before ``read_file`` to understand a file's layout and decide
    which sections to read. For Python files this uses the AST so the
    line ranges are exact; for other languages a regex heuristic is used.

    Args:
        path: Path to the source file to outline.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError as exc:
        return json.dumps({"error": str(exc)})

    total_lines = source.count("\n") + (1 if source and not source.endswith("\n") else 0)
    _, ext = os.path.splitext(path)
    ext = ext.lower()

    if ext == ".py":
        symbols = _python_outline(source)
    else:
        symbols = _regex_outline(source, ext)

    return json.dumps({
        "path": path,
        "total_lines": total_lines,
        "language": ext.lstrip(".") or "unknown",
        "symbols": symbols,
    })


@tool(permissions=("read",), resources=("code:read",))
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


@tool(permissions=("execute",), resources=("code:test",), sandbox=True)
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


@tool(permissions=("execute",), resources=("code:lint",), sandbox=True)
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


@tool(permissions=("execute",), resources=("code:typecheck",), sandbox=True)
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


@tool(permissions=("execute",), resources=("code:build",), sandbox=True)
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
