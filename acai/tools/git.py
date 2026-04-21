"""Git tools — status, diff, commit, push, log."""

from __future__ import annotations

import json
import subprocess

from acai.orchestrator.tools import tool


@tool(permissions=("read",))
def status(cwd: str = ".") -> str:
    """Show the git status of the working directory.

    Args:
        cwd: Project root / worktree directory.
    """
    try:
        proc = subprocess.run(
            ["git", "status", "--short"], cwd=cwd,
            capture_output=True, text=True, timeout=15,
        )
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=cwd,
            capture_output=True, text=True, timeout=5,
        )
        return json.dumps({
            "branch": branch.stdout.strip(),
            "files": proc.stdout.strip(),
            "clean": proc.stdout.strip() == "",
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",))
def diff(cwd: str = ".", ref: str = "") -> str:
    """Show the git diff of uncommitted changes.

    Args:
        cwd: Project root / worktree directory.
        ref: Optional ref to diff against (e.g. "HEAD~1", "main").
    """
    cmd = ["git", "diff"]
    if ref:
        cmd.append(ref)
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=15)
        output = proc.stdout
        if len(output) > 8000:
            output = output[:8000] + "\n... (truncated)"
        return output or "(no changes)"
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",), sandbox=True)
def commit(message: str, cwd: str = ".", files: str = "") -> str:
    """Stage and commit changes.

    Args:
        message: Commit message.
        cwd: Project root / worktree directory.
        files: Space-separated list of files to stage. Empty stages all changes.
    """
    try:
        if files:
            for f in files.split():
                subprocess.run(["git", "add", f], cwd=cwd, capture_output=True, text=True, check=True)
        else:
            subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, text=True, check=True)

        proc = subprocess.run(
            ["git", "commit", "-m", message], cwd=cwd,
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            if "nothing to commit" in proc.stdout:
                return json.dumps({"ok": False, "message": "nothing to commit"})
            return json.dumps({"error": proc.stderr.strip() or proc.stdout.strip()})

        return json.dumps({"ok": True, "message": proc.stdout.strip()})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",), sandbox=True)
def push(cwd: str = ".", remote: str = "origin") -> str:
    """Push the current branch to the remote.

    Args:
        cwd: Project root / worktree directory.
        remote: Remote name (default "origin").
    """
    try:
        branch_proc = subprocess.run(
            ["git", "branch", "--show-current"], cwd=cwd,
            capture_output=True, text=True, timeout=5,
        )
        branch = branch_proc.stdout.strip()

        proc = subprocess.run(
            ["git", "push", "-u", remote, branch], cwd=cwd,
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            return json.dumps({"error": proc.stderr.strip()})
        return json.dumps({"ok": True, "branch": branch, "remote": remote})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",))
def worktree_list(cwd: str = ".") -> str:
    """List git worktrees for the repository.

    Args:
        cwd: Path inside a git worktree / repository.
    """
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return json.dumps({"error": proc.stderr.strip() or proc.stdout.strip()})
        return json.dumps({"listing": proc.stdout.strip()})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",), sandbox=True)
def worktree_add(
    worktree_path: str,
    branch: str = "",
    cwd: str = ".",
) -> str:
    """Create a new linked worktree (optionally a new branch).

    Args:
        worktree_path: Filesystem path for the new worktree.
        branch: Branch to check out; if empty, HEAD is detached at current commit.
        cwd: Path inside the main repository.
    """
    try:
        cmd = ["git", "worktree", "add"]
        if branch:
            cmd.extend([worktree_path, branch])
        else:
            cmd.append(worktree_path)
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return json.dumps({"error": proc.stderr.strip() or proc.stdout.strip()})
        return json.dumps({"ok": True, "stdout": proc.stdout.strip()})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",), sandbox=True)
def worktree_remove(worktree_path: str, force: bool = False) -> str:
    """Remove a worktree directory registration (``git worktree remove``).

    Args:
        worktree_path: Path of the worktree to remove.
        force: Pass ``--force`` to delete even if not clean.
    """
    try:
        cmd = ["git", "worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(worktree_path)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return json.dumps({"error": proc.stderr.strip() or proc.stdout.strip()})
        return json.dumps({"ok": True})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",))
def log(cwd: str = ".", count: int = 10) -> str:
    """Show recent git log entries.

    Args:
        cwd: Project root / worktree directory.
        count: Number of recent commits to show.
    """
    try:
        proc = subprocess.run(
            ["git", "log", f"--max-count={count}", "--oneline", "--no-decorate"],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
        return proc.stdout.strip() or "(no commits)"
    except Exception as exc:
        return json.dumps({"error": str(exc)})
