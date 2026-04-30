"""CI tools — workflow runs, job logs, status checks.

Generic CI interface with pluggable backends.  Currently supports GitHub
Actions via the ``gh`` CLI; GitLab CI and Codeberg (Forgejo Actions)
backends can be added by implementing the :class:`_Backend` protocol.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Protocol


from acai.orchestrator.tools import tool


# -- Module-level config (set by orchestrator / worker at startup) ---------

_ci_config: dict | None = None


def _configure(ci_cfg) -> None:
    """Bind the system CI config so tools pick up defaults.

    Called at startup with the ``CIConfig`` dataclass (or a dict).
    """
    global _ci_config
    from dataclasses import asdict, is_dataclass
    _ci_config = asdict(ci_cfg) if is_dataclass(ci_cfg) else dict(ci_cfg or {})


def _cfg(key: str, default=None):
    """Read a value from the bound CI config."""
    if _ci_config is None:
        return default
    return _ci_config.get(key, default)


# -- Backend abstraction ---------------------------------------------------


class _Backend(Protocol):
    """Minimal CI backend interface.

    Each method either returns structured data or raises ``RuntimeError``
    with a human-readable message on failure.
    """

    def list_workflows(self, cwd: str) -> list[dict]: ...

    def list_runs(
        self, cwd: str, branch: str, workflow: str, status: str, limit: int,
    ) -> list[dict]: ...

    def get_run(self, cwd: str, run_id: str) -> dict: ...

    def get_logs(self, cwd: str, run_id: str, failed_only: bool) -> str: ...

    def trigger(
        self, cwd: str, workflow: str, ref: str, inputs: dict[str, str],
    ) -> dict: ...

    def rerun(self, cwd: str, run_id: str, failed_only: bool) -> dict: ...

    def cancel(self, cwd: str, run_id: str) -> dict: ...


def _detect_platform(cwd: str) -> str:
    """Detect the CI platform from the git remote URL."""
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        url = proc.stdout.strip().lower()
    except Exception:
        return "unknown"

    if "github.com" in url or "github" in url:
        return "github"
    if "gitlab" in url:
        return "gitlab"
    if "codeberg.org" in url:
        return "codeberg"
    return "unknown"


_BACKENDS: dict[str, _Backend] = {}


def _get_backend(platform: str) -> _Backend:
    if platform not in _BACKENDS:
        if platform == "github":
            _BACKENDS["github"] = _GitHubBackend()
        else:
            raise ValueError(
                f"CI backend '{platform}' is not yet supported "
                f"(available: github)"
            )
    return _BACKENDS[platform]


def _backend_for(cwd: str, platform: str) -> _Backend:
    """Resolve backend: explicit param > config > auto-detect from remote."""
    p = platform or _cfg("platform", "auto")
    if p == "auto":
        p = _detect_platform(cwd)
    return _get_backend(p)


# -- GitHub Actions backend ------------------------------------------------


_RUN_FIELDS = (
    "databaseId,displayTitle,status,conclusion,headBranch,"
    "event,createdAt,updatedAt,url,workflowName"
)

_RUN_DETAIL_FIELDS = (
    "databaseId,displayTitle,status,conclusion,headBranch,"
    "event,createdAt,updatedAt,url,workflowName,jobs"
)


class _GitHubBackend:
    """GitHub Actions backend using the ``gh`` CLI."""

    @staticmethod
    def _gh(
        args: list[str],
        cwd: str,
        timeout: int = 30,
    ) -> dict | list | str:
        """Run a ``gh`` command and return parsed JSON (or raw text)."""
        cmd = ["gh"] + args
        env = None
        token = _cfg("token", "")
        if token:
            env = {**os.environ, "GH_TOKEN": token}
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        if proc.returncode != 0:
            error = proc.stderr.strip() or proc.stdout.strip()
            raise RuntimeError(error)
        text = proc.stdout.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # -- protocol methods --------------------------------------------------

    def list_workflows(self, cwd: str) -> list[dict]:
        result = self._gh(
            ["workflow", "list", "--json", "id,name,state,path"], cwd,
        )
        return result if isinstance(result, list) else []

    def list_runs(
        self,
        cwd: str,
        branch: str,
        workflow: str,
        status: str,
        limit: int,
    ) -> list[dict]:
        cmd = ["run", "list", "--json", _RUN_FIELDS]
        if branch:
            cmd += ["--branch", branch]
        if workflow:
            cmd += ["--workflow", workflow]
        if status:
            cmd += ["--status", status]
        cmd += ["--limit", str(limit)]
        result = self._gh(cmd, cwd)
        return result if isinstance(result, list) else []

    def get_run(self, cwd: str, run_id: str) -> dict:
        result = self._gh(
            ["run", "view", run_id, "--json", _RUN_DETAIL_FIELDS], cwd,
        )
        return result if isinstance(result, dict) else {"raw": result}

    def get_logs(self, cwd: str, run_id: str, failed_only: bool) -> str:
        flag = "--log-failed" if failed_only else "--log"
        result = self._gh(
            ["run", "view", run_id, flag], cwd, timeout=60,
        )
        return result if isinstance(result, str) else json.dumps(result)

    def trigger(
        self,
        cwd: str,
        workflow: str,
        ref: str,
        inputs: dict[str, str],
    ) -> dict:
        cmd = ["workflow", "run", workflow]
        if ref:
            cmd += ["--ref", ref]
        for k, v in inputs.items():
            cmd += ["-f", f"{k}={v}"]
        self._gh(cmd, cwd)
        return {"ok": True, "workflow": workflow, "ref": ref or "(default)"}

    def rerun(self, cwd: str, run_id: str, failed_only: bool) -> dict:
        cmd = ["run", "rerun", run_id]
        if failed_only:
            cmd.append("--failed")
        self._gh(cmd, cwd)
        return {"ok": True, "run_id": run_id, "failed_only": failed_only}

    def cancel(self, cwd: str, run_id: str) -> dict:
        self._gh(["run", "cancel", run_id], cwd)
        return {"ok": True, "run_id": run_id}


# -- Public tool functions -------------------------------------------------

_MAX_LOG_CHARS = 12_000


@tool(permissions=("read",), resources=("ci:read",))
def list_workflows(cwd: str = ".", platform: str = "") -> str:
    """List available CI workflows / pipelines for the repository.

    Args:
        cwd: Project root / worktree directory.
        platform: CI platform override ("github", "gitlab", "codeberg").
                  Auto-detected from git remote when empty.
    """
    try:
        backend = _backend_for(cwd, platform)
        workflows = backend.list_workflows(cwd)
        return json.dumps({"workflows": workflows, "count": len(workflows)})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",), resources=("ci:read",))
def list_runs(
    cwd: str = ".",
    branch: str = "",
    workflow: str = "",
    status: str = "",
    limit: int = 15,
    platform: str = "",
) -> str:
    """List recent CI workflow runs.

    Args:
        cwd: Project root / worktree directory.
        branch: Filter by branch name.
        workflow: Filter by workflow name or filename.
        status: Filter by status ("queued", "in_progress", "completed",
                "success", "failure", "cancelled").
        limit: Maximum number of runs to return.
        platform: CI platform override. Auto-detected when empty.
    """
    try:
        backend = _backend_for(cwd, platform)
        runs = backend.list_runs(cwd, branch, workflow, status, limit)
        return json.dumps({"runs": runs, "count": len(runs)})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",), resources=("ci:read",))
def get_run(run_id: str, cwd: str = ".", platform: str = "") -> str:
    """Get detailed information about a specific CI run, including its jobs.

    Args:
        run_id: The run ID (numeric for GitHub).
        cwd: Project root / worktree directory.
        platform: CI platform override. Auto-detected when empty.
    """
    try:
        backend = _backend_for(cwd, platform)
        run_data = backend.get_run(cwd, run_id)
        return json.dumps(run_data)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",), resources=("ci:read",))
def get_logs(
    run_id: str,
    cwd: str = ".",
    failed_only: bool = True,
    platform: str = "",
) -> str:
    """Get CI job logs for a specific run.

    By default only logs from failed jobs are returned (much shorter and
    more actionable).  Set ``failed_only=False`` to get the full log.

    Args:
        run_id: The run ID.
        cwd: Project root / worktree directory.
        failed_only: Only return logs from failed jobs.
        platform: CI platform override. Auto-detected when empty.
    """
    try:
        backend = _backend_for(cwd, platform)
        logs = backend.get_logs(cwd, run_id, failed_only)
        if len(logs) > _MAX_LOG_CHARS:
            logs = logs[-_MAX_LOG_CHARS:]
            logs = "(truncated — showing last 12 000 chars)\n" + logs
        return json.dumps({"run_id": run_id, "failed_only": failed_only, "logs": logs})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("execute",), resources=("ci:trigger",), sandbox=True)
def trigger(
    workflow: str,
    cwd: str = ".",
    ref: str = "",
    inputs: str = "",
    platform: str = "",
) -> str:
    """Trigger a CI workflow / pipeline run.

    Args:
        workflow: Workflow name or filename (e.g. "test.yml").
        cwd: Project root / worktree directory.
        ref: Git ref (branch/tag) to run on. Uses the default branch if empty.
        inputs: JSON-encoded key/value pairs for workflow_dispatch inputs.
        platform: CI platform override. Auto-detected when empty.
    """
    try:
        backend = _backend_for(cwd, platform)
        parsed_inputs: dict[str, str] = {}
        if inputs:
            parsed_inputs = json.loads(inputs)
            if not isinstance(parsed_inputs, dict):
                return json.dumps({"error": "inputs must be a JSON object"})
        result = backend.trigger(cwd, workflow, ref, parsed_inputs)
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("execute",), resources=("ci:trigger",), sandbox=True)
def rerun(
    run_id: str,
    cwd: str = ".",
    failed_only: bool = True,
    platform: str = "",
) -> str:
    """Re-run a CI workflow run (all jobs or only failed ones).

    Args:
        run_id: The run ID to re-run.
        cwd: Project root / worktree directory.
        failed_only: Only re-run failed jobs instead of the entire run.
        platform: CI platform override. Auto-detected when empty.
    """
    try:
        backend = _backend_for(cwd, platform)
        result = backend.rerun(cwd, run_id, failed_only)
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(permissions=("execute",), resources=("ci:cancel",), sandbox=True)
def cancel(run_id: str, cwd: str = ".", platform: str = "") -> str:
    """Cancel a running CI workflow run.

    Args:
        run_id: The run ID to cancel.
        cwd: Project root / worktree directory.
        platform: CI platform override. Auto-detected when empty.
    """
    try:
        backend = _backend_for(cwd, platform)
        result = backend.cancel(cwd, run_id)
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
