"""Task runner — manages sub-agent conversations and background tasks.

Supports two execution modes:

1. **Full sub-agent**: Spawns a new agent conversation with its own LLM
   calls and tool loop.  Can run blocking (caller awaits result) or
   async (caller gets a task_id to poll later).

2. **Lightweight background task**: Executes a registered Python
   coroutine in the background.  Same polling interface.

Both modes track status and results via a unified ``RunningTask`` object.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

log = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class RunningTask:
    """Tracks a running sub-agent or background task."""

    task_id: str
    kind: str  # "subagent" or "background"
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""
    error: str = ""
    progress: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    metadata: dict = field(default_factory=dict)
    _future: asyncio.Future | None = field(default=None, repr=False)


class TaskRunner:
    """Manages sub-agent and background task lifecycle."""

    def __init__(self):
        self._tasks: dict[str, RunningTask] = {}
        self._registered_tasks: dict[str, Callable[..., Coroutine]] = {}
        self._max_completed: int = 100

    # ------------------------------------------------------------------
    # Task registry (for lightweight background tasks)
    # ------------------------------------------------------------------

    def register_task(self, name: str, coro_factory: Callable[..., Coroutine]) -> None:
        """Register a named coroutine factory for background execution.

        Args:
            name: Task name (e.g. "index_knowledge", "run_tests").
            coro_factory: Async function that accepts kwargs and returns a result string.
        """
        self._registered_tasks[name] = coro_factory

    def list_registered(self) -> list[str]:
        """List all registered background task names."""
        return list(self._registered_tasks.keys())

    # ------------------------------------------------------------------
    # Sub-agent execution
    # ------------------------------------------------------------------

    async def run_subagent(
        self,
        *,
        agent_name: str,
        message: str,
        conversation: str = "",
        graph_factory: Callable | None = None,
        parent_conversation: str = "",
        **extra,
    ) -> RunningTask:
        """Run a sub-agent synchronously (blocking).

        Creates a new conversation and runs the agent to completion.
        Returns the completed RunningTask with the agent's final response.

        Args:
            agent_name: Agent to run.
            message: The user message to send.
            conversation: Explicit conversation ID (auto-generated if empty).
            graph_factory: Callable that creates and runs the graph.
            parent_conversation: Parent conversation ID for tracking.
            **extra: Additional context passed to the graph.
        """
        task_id = str(uuid.uuid4())
        task = RunningTask(
            task_id=task_id,
            kind="subagent",
            status=TaskStatus.RUNNING,
            metadata={
                "agent": agent_name,
                "message": message[:200],
                "parent_conversation": parent_conversation,
            },
        )
        self._tasks[task_id] = task

        try:
            if graph_factory is None:
                task.status = TaskStatus.FAILED
                task.error = "No graph factory provided"
                task.completed_at = time.time()
                return task

            result = await graph_factory(
                agent_name=agent_name,
                message=message,
                conversation=conversation or f"sub-{task_id}",
                **extra,
            )
            task.status = TaskStatus.COMPLETED
            task.result = result or ""
            task.completed_at = time.time()

        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
        except Exception as exc:
            log.exception("Sub-agent %s failed: %s", agent_name, exc)
            task.status = TaskStatus.FAILED
            task.error = f"{type(exc).__name__}: {exc}"
            task.completed_at = time.time()

        self._cleanup_old()
        return task

    async def run_subagent_async(
        self,
        *,
        agent_name: str,
        message: str,
        conversation: str = "",
        graph_factory: Callable | None = None,
        parent_conversation: str = "",
        **extra,
    ) -> str:
        """Start a sub-agent asynchronously. Returns the task_id immediately.

        The sub-agent runs in the background. Use ``get_task`` or
        ``wait_for_task`` to retrieve the result.
        """
        task_id = str(uuid.uuid4())
        task = RunningTask(
            task_id=task_id,
            kind="subagent",
            status=TaskStatus.RUNNING,
            metadata={
                "agent": agent_name,
                "message": message[:200],
                "parent_conversation": parent_conversation,
            },
        )
        self._tasks[task_id] = task

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        task._future = future

        async def _run():
            try:
                if graph_factory is None:
                    raise RuntimeError("No graph factory provided")
                result = await graph_factory(
                    agent_name=agent_name,
                    message=message,
                    conversation=conversation or f"sub-{task_id}",
                    **extra,
                )
                task.status = TaskStatus.COMPLETED
                task.result = result or ""
                task.completed_at = time.time()
                if not future.done():
                    future.set_result(result)
            except asyncio.CancelledError:
                task.status = TaskStatus.CANCELLED
                task.completed_at = time.time()
                if not future.done():
                    future.cancel()
            except Exception as exc:
                log.exception("Async sub-agent %s failed", agent_name)
                task.status = TaskStatus.FAILED
                task.error = f"{type(exc).__name__}: {exc}"
                task.completed_at = time.time()
                if not future.done():
                    future.set_exception(exc)
            self._cleanup_old()

        asyncio.create_task(_run())
        return task_id

    # ------------------------------------------------------------------
    # Background task execution
    # ------------------------------------------------------------------

    async def run_background(self, name: str, **kwargs) -> str:
        """Start a registered background task. Returns task_id.

        Args:
            name: Registered task name.
            **kwargs: Arguments passed to the coroutine factory.

        Raises:
            KeyError: If *name* is not registered.
        """
        factory = self._registered_tasks.get(name)
        if factory is None:
            raise KeyError(f"Background task {name!r} not registered")

        task_id = str(uuid.uuid4())
        task = RunningTask(
            task_id=task_id,
            kind="background",
            status=TaskStatus.RUNNING,
            metadata={"name": name, "kwargs_keys": list(kwargs.keys())},
        )
        self._tasks[task_id] = task

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        task._future = future

        async def _run():
            try:
                result = await factory(**kwargs)
                task.status = TaskStatus.COMPLETED
                task.result = str(result) if result is not None else ""
                task.completed_at = time.time()
                if not future.done():
                    future.set_result(task.result)
            except asyncio.CancelledError:
                task.status = TaskStatus.CANCELLED
                task.completed_at = time.time()
                if not future.done():
                    future.cancel()
            except Exception as exc:
                log.exception("Background task %s failed", name)
                task.status = TaskStatus.FAILED
                task.error = f"{type(exc).__name__}: {exc}"
                task.completed_at = time.time()
                if not future.done():
                    future.set_exception(exc)
            self._cleanup_old()

        asyncio.create_task(_run())
        return task_id

    # ------------------------------------------------------------------
    # Task status and retrieval
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> RunningTask | None:
        """Get a task by ID. Returns None if not found."""
        return self._tasks.get(task_id)

    async def wait_for_task(self, task_id: str, timeout: float = 300.0) -> RunningTask:
        """Wait for a task to complete.

        Args:
            task_id: The task to wait for.
            timeout: Max seconds to wait.

        Raises:
            KeyError: If task_id is not found.
            TimeoutError: If the task doesn't complete in time.
        """
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id!r} not found")

        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return task

        if task._future is None:
            return task

        try:
            await asyncio.wait_for(asyncio.shield(task._future), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Task {task_id} did not complete within {timeout:.0f}s")
        except Exception:
            pass  # Error is captured in task.error

        return task

    def list_tasks(self, status: TaskStatus | None = None) -> list[RunningTask]:
        """List tasks, optionally filtered by status."""
        tasks = list(self._tasks.values())
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task.

        Returns True if the task was cancelled.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False
        if task.status != TaskStatus.RUNNING:
            return False

        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()
        if task._future and not task._future.done():
            task._future.cancel()
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _cleanup_old(self) -> None:
        """Remove oldest completed tasks if we exceed the limit."""
        completed = [
            t for t in self._tasks.values()
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
        ]
        if len(completed) > self._max_completed:
            completed.sort(key=lambda t: t.completed_at)
            for t in completed[: len(completed) - self._max_completed]:
                self._tasks.pop(t.task_id, None)
