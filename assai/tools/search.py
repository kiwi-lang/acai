"""Search tools — glob file paths and grep/ripgrep contents (read-only)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from assai.orchestrator.tools import tool


@tool(permissions=("read",))
def glob_files(pattern: str, path: str = ".") -> str:
    """Find files matching a glob pattern under a directory, newest first.

    Args:
        pattern: Glob pattern (e.g. ``**/*.py``). Use ``**`` for recursive search.
        path: Root directory to search in.
    """
    import glob as globmod

    root = os.path.abspath(path)
    if not os.path.isdir(root):
        return json.dumps({"error": f"not a directory: {root}"})

    try:
        matches = globmod.glob(
            pattern,
            root_dir=root,
            recursive=True,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    def mtime_key(rel: str) -> float:
        fp = os.path.join(root, rel)
        try:
            return os.path.getmtime(fp)
        except OSError:
            return 0.0

    matches.sort(key=mtime_key, reverse=True)
    return json.dumps({"root": root, "matches": matches, "count": len(matches)})


@tool(permissions=("read",))
def grep(
    pattern: str,
    path: str = ".",
    glob_filter: str = "",
    output_mode: str = "content",
    context_before: int = 0,
    context_after: int = 0,
    context_lines: int = 0,
    case_insensitive: bool = False,
    line_numbers: bool = True,
    file_type: str = "",
    head_limit: int = 200,
) -> str:
    """Search file contents with ripgrep (``rg``) or grep as fallback.

    Args:
        pattern: Regex pattern to search for.
        path: File or directory to search.
        glob_filter: Optional glob for files (e.g. ``*.py``).
        output_mode: ``content``, ``files_with_matches``, or ``count``.
        context_before: Lines of context before each match (``rg -B``).
        context_after: Lines of context after each match (``rg -A``).
        context_lines: Lines before and after (``rg -C``); overrides B/A if > 0.
        case_insensitive: Case-insensitive search.
        line_numbers: Show line numbers in content mode.
        file_type: Ripgrep ``--type`` (e.g. ``py``, ``js``) when using ``rg``.
        head_limit: Max lines or files to return (depending on mode).
    """
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--color", "never"]
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        if case_insensitive:
            cmd.append("-i")
        if line_numbers and output_mode == "content":
            cmd.append("-n")
        if context_lines > 0:
            cmd.extend(["-C", str(context_lines)])
        else:
            if context_before > 0:
                cmd.extend(["-B", str(context_before)])
            if context_after > 0:
                cmd.extend(["-A", str(context_after)])
        if glob_filter:
            cmd.extend(["--glob", glob_filter])
        if file_type:
            cmd.extend(["--type", file_type])
        cmd.append(pattern)
        cmd.append(path)
    else:
        cmd = ["grep", "--color=never"]
        if case_insensitive:
            cmd.append("-i")
        if output_mode == "files_with_matches":
            cmd.extend(["-rl"])
            if glob_filter:
                cmd.extend(["--include", glob_filter])
            cmd.extend([pattern, path])
        elif output_mode == "count":
            cmd.extend(["-r", "-c"])
            if glob_filter:
                cmd.extend(["--include", glob_filter])
            cmd.extend([pattern, path])
        else:
            cmd.extend(["-rn"])
            if glob_filter:
                cmd.extend(["--include", glob_filter])
            cmd.extend([pattern, path])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        raw = (proc.stdout or "").strip()
        lines = raw.splitlines()
        truncated = len(lines) > head_limit
        lines = lines[:head_limit]
        return json.dumps({
            "backend": "rg" if rg else "grep",
            "command": cmd,
            "returncode": proc.returncode,
            "lines": lines,
            "truncated": truncated,
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "grep timed out"})
    except OSError as exc:
        return json.dumps({"error": str(exc)})
