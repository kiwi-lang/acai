"""Project tools — inspect the structure of the current project/repo."""

from __future__ import annotations

import json
import os
import subprocess

from assai.orchestrator.tools import tool


_IGNORED_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "dist", "build", "egg-info", ".eggs", ".next", ".nuxt",
}

_IGNORED_SUFFIXES = {
    ".pyc", ".pyo", ".so", ".o", ".a", ".dylib",
    ".egg", ".whl", ".class", ".jar",
}


def _is_ignored_dir(name: str) -> bool:
    return name in _IGNORED_DIRS or name.endswith(".egg-info")


@tool(permissions=("read",))
def tree(
    cwd: str = ".",
    subpath: str = "",
    max_depth: int = 0,
    respect_gitignore: bool = True,
) -> str:
    """List project files recursively as a flat list of relative paths.

    Returns files grouped by directory, skipping common noise
    (.git, __pycache__, node_modules, .venv, etc.).

    Args:
        cwd: Project root directory.
        subpath: Subdirectory to scope the listing to (relative to cwd).
        max_depth: Maximum directory depth (0 = unlimited).
        respect_gitignore: If true and the project has a .gitignore, exclude matching paths.
    """
    root = os.path.abspath(os.path.join(cwd, subpath))
    if not os.path.isdir(root):
        return json.dumps({"error": f"not a directory: {root}"})

    gitignore_patterns: set[str] = set()
    if respect_gitignore:
        gitignore_patterns = _load_gitignore(cwd)

    entries: list[str] = []
    base_depth = root.rstrip(os.sep).count(os.sep)

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth

        if max_depth > 0 and depth >= max_depth:
            dirnames.clear()
            continue

        dirnames[:] = sorted(
            d for d in dirnames
            if not _is_ignored_dir(d) and not d.startswith(".")
        )

        for fname in sorted(filenames):
            if any(fname.endswith(s) for s in _IGNORED_SUFFIXES):
                continue
            rel = os.path.join(rel_dir, fname) if rel_dir != "." else fname
            if rel in gitignore_patterns:
                continue
            entries.append(rel)

        if len(entries) > 5000:
            entries.append("... (truncated at 5000 files)")
            break

    return json.dumps({
        "root": root,
        "files": entries,
        "count": len(entries),
    })


@tool(permissions=("read",))
def summary(cwd: str = ".", subpath: str = "") -> str:
    """Show a high-level summary: directory tree (folders only) plus file counts per directory.

    Useful for understanding project layout without listing every file.

    Args:
        cwd: Project root directory.
        subpath: Subdirectory to scope the summary to (relative to cwd).
    """
    root = os.path.abspath(os.path.join(cwd, subpath))
    if not os.path.isdir(root):
        return json.dumps({"error": f"not a directory: {root}"})

    dir_stats: list[dict] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if not _is_ignored_dir(d) and not d.startswith(".")
        )
        rel = os.path.relpath(dirpath, root)
        if rel == ".":
            rel = ""

        ext_counts: dict[str, int] = {}
        for f in filenames:
            if any(f.endswith(s) for s in _IGNORED_SUFFIXES):
                continue
            _, ext = os.path.splitext(f)
            ext = ext.lower() or "(no ext)"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

        if ext_counts:
            dir_stats.append({
                "dir": rel or ".",
                "files": sum(ext_counts.values()),
                "by_ext": dict(sorted(ext_counts.items(), key=lambda x: -x[1])),
            })

        if len(dir_stats) > 500:
            break

    return json.dumps({
        "root": root,
        "directories": dir_stats,
        "total_dirs": len(dir_stats),
    })


@tool(permissions=("read",))
def find(
    pattern: str,
    cwd: str = ".",
    subpath: str = "",
    file_type: str = "",
    max_results: int = 100,
) -> str:
    """Find files by name pattern (glob-style).

    Args:
        pattern: Glob pattern to match against file names (e.g. ``*.py``, ``test_*``).
        cwd: Project root directory.
        subpath: Subdirectory to scope the search to (relative to cwd).
        file_type: Filter by extension without dot (e.g. ``py``, ``ts``).
        max_results: Maximum number of results to return.
    """
    root = os.path.abspath(os.path.join(cwd, subpath))
    if not os.path.isdir(root):
        return json.dumps({"error": f"not a directory: {root}"})

    import fnmatch

    matches: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not _is_ignored_dir(d) and not d.startswith(".")
        ]

        for fname in filenames:
            if file_type and not fname.endswith(f".{file_type}"):
                continue
            if fnmatch.fnmatch(fname, pattern):
                rel = os.path.relpath(os.path.join(dirpath, fname), root)
                matches.append(rel)
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    return json.dumps({
        "root": root,
        "pattern": pattern,
        "matches": matches,
        "count": len(matches),
        "truncated": len(matches) >= max_results,
    })


def _load_gitignore(cwd: str) -> set[str]:
    """Use git ls-files to get the set of tracked + untracked-but-not-ignored files.

    Returns an empty set on failure (non-git repo, git not installed, etc.).
    """
    gitignore = os.path.join(cwd, ".gitignore")
    if not os.path.isfile(gitignore):
        return set()

    try:
        proc = subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            return set(proc.stdout.strip().splitlines())
    except (OSError, subprocess.TimeoutExpired):
        pass

    return set()
