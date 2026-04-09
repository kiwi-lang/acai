"""Worker — pops work items from the queue and executes them.

Work items are either ``llm_complete`` (send messages to an LLM) or
``tool_call`` (run a shell command, read a file, etc.).

The worker is lazy about the LLM server: it keeps it alive as long as
nothing needs the GPU, and only kills it when a ``gpu=1`` tool comes in.
When the next ``llm_complete`` arrives the server is started again.

Batching: when the first item popped is ``llm_complete``, the worker
peeks for more ``llm_complete`` items so related work runs back-to-back
and benefits from KV-cache locality.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
from typing import TYPE_CHECKING

from assai.agents.llm import create_llm
from assai.queue.work import TaskStatus

if TYPE_CHECKING:
    from assai.agents.llm import LLM
    from assai.core.config import AssaiConfig
    from assai.queue.work import Task, WorkQueue

log = logging.getLogger(__name__)


class Worker:
    """Dispatch loop that pops work items and executes them."""

    def __init__(self, config: AssaiConfig, queue: WorkQueue,
                 tasks_dir: str = "tasks"):
        self.config = config
        self.queue = queue
        self.tasks_dir = tasks_dir

        self._llm_proc: subprocess.Popen | None = None
        self._llm_client: LLM | None = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        """Block forever, popping and executing work items."""
        while True:
            batch = self._pop_batch()
            if not batch:
                time.sleep(self.config.queue.poll_interval)
                continue
            for task in batch:
                self._dispatch(task)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, task: Task):
        self.queue.update(task.id, status=TaskStatus.IN_PROGRESS)
        try:
            if task.kind == "llm_complete":
                self._ensure_llm()
                self._run_llm(task)
            elif task.kind == "tool_call":
                if task.gpu:
                    self._kill_llm()
                self._run_tool(task)
            else:
                raise ValueError(f"unknown task kind: {task.kind}")

            self.queue.update(task.id, status=TaskStatus.COMPLETED)
        except Exception as exc:
            log.exception("task %s failed", task.id)
            if task.retries + 1 < task.max_retries:
                self.queue.update(
                    task.id, status=TaskStatus.READY,
                    retries=task.retries + 1,
                    error_log=f"retry {task.retries + 1}: {exc}",
                )
            else:
                self.queue.update(
                    task.id, status=TaskStatus.FAILED,
                    error_log=str(exc),
                )

    # ------------------------------------------------------------------
    # Batching
    # ------------------------------------------------------------------

    def _pop_batch(self) -> list[Task]:
        """Pop one item, or batch multiple llm_complete items."""
        first = self.queue.pop(status=TaskStatus.READY)
        if first is None:
            return []
        if first.kind != "llm_complete":
            return [first]

        batch = [first]
        while True:
            more = self.queue.pop(status=TaskStatus.READY)
            if more is None:
                break
            if more.kind == "llm_complete":
                batch.append(more)
            else:
                break
        return batch

    # ------------------------------------------------------------------
    # LLM execution
    # ------------------------------------------------------------------

    def _run_llm(self, task: Task):
        messages = self._read_payload(task)
        result = self._llm_client.complete(messages)
        self._write_result(task.id, result)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _run_tool(self, task: Task):
        payload = self._read_payload(task)
        tool = payload.get("tool", "")
        args = payload.get("args", {})

        if tool == "shell":
            result = self._tool_shell(args)
        elif tool == "read_file":
            result = self._tool_read_file(args, task.worktree)
        elif tool == "write_file":
            result = self._tool_write_file(args, task.worktree)
        elif tool == "list_directory":
            result = self._tool_list_directory(args, task.worktree)
        else:
            result = json.dumps({"error": f"unknown tool: {tool}"})

        self._write_result(task.id, result)

    def _tool_shell(self, args: dict) -> str:
        command = args.get("command", "")
        cwd = args.get("cwd", None)
        timeout = args.get("timeout", self.config.worker.timeout)
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

    def _tool_read_file(self, args: dict, worktree: str) -> str:
        path = os.path.join(worktree, args.get("path", "")) if worktree else args.get("path", "")
        try:
            with open(path) as f:
                return f.read()
        except OSError as exc:
            return json.dumps({"error": str(exc)})

    def _tool_write_file(self, args: dict, worktree: str) -> str:
        path = os.path.join(worktree, args.get("path", "")) if worktree else args.get("path", "")
        content = args.get("content", "")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return json.dumps({"written": path})
        except OSError as exc:
            return json.dumps({"error": str(exc)})

    def _tool_list_directory(self, args: dict, worktree: str) -> str:
        path = os.path.join(worktree, args.get("path", ".")) if worktree else args.get("path", ".")
        try:
            entries = sorted(os.listdir(path))
            return json.dumps(entries)
        except OSError as exc:
            return json.dumps({"error": str(exc)})

    # ------------------------------------------------------------------
    # Lazy LLM lifecycle
    # ------------------------------------------------------------------

    def _ensure_llm(self):
        """Start the LLM server if we manage it and it's not running."""
        cmd = self.config.llm.server_command
        if cmd and self._llm_proc is None:
            log.info("starting LLM server: %s", cmd)
            self._llm_proc = subprocess.Popen(
                shlex.split(cmd),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._wait_healthy()
        if self._llm_client is None:
            self._llm_client = create_llm(self.config.llm)

    def _kill_llm(self):
        """Kill the LLM server to free GPU.  No-op if we don't manage it."""
        if self._llm_proc is not None:
            log.info("killing LLM server (pid %d) for GPU work", self._llm_proc.pid)
            self._llm_proc.terminate()
            self._llm_proc.wait(timeout=30)
            self._llm_proc = None
            self._llm_client = None

    def _wait_healthy(self, retries: int = 60, interval: float = 2.0):
        """Poll the LLM health endpoint until it responds."""
        import requests as _req

        url = f"{self.config.llm.endpoint}/health"
        for _ in range(retries):
            try:
                r = _req.get(url, timeout=5)
                if r.status_code < 500:
                    return
            except _req.ConnectionError:
                pass
            time.sleep(interval)
        log.warning("LLM server did not become healthy after %d attempts", retries)

    # ------------------------------------------------------------------
    # File I/O helpers
    # ------------------------------------------------------------------

    def _read_payload(self, task: Task):
        """Read the task payload from spec_path (JSON)."""
        with open(task.spec_path) as f:
            return json.load(f)

    def _write_result(self, task_id: str, result: str):
        task_dir = os.path.join(self.tasks_dir, task_id)
        os.makedirs(task_dir, exist_ok=True)
        path = os.path.join(task_dir, "result.json")
        with open(path, "w") as f:
            f.write(result)
        self.queue.update(task_id, result_path=path)
