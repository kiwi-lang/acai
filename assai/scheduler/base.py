"""Base scheduler — async building blocks for multi-agent workflows.

Provides reusable primitives that any scheduler can compose:

* :class:`AsyncTask` — a handle to a queued work item with an awaitable result.
* :class:`AgentContext` — accumulates messages for an LLM call, then submits.
* :class:`BaseScheduler` — owns the queue/chat/tracker wiring and exposes
  ``submit_llm``, ``submit_tool``, ``create_agent_context``, and ``notify``.

Subclass ``BaseScheduler`` and implement your routing / orchestration logic
as ``async`` methods.  Use ``await task.result()`` to wait for a single task
or ``asyncio.gather`` to run several in parallel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING

from assai.core.stream import StreamTracker
from assai.queue.work import TaskStatus, WorkQueue

if TYPE_CHECKING:
    from assai.core.chat import ChatStore
    from assai.core.config import AssaiConfig
    from assai.core.projects import Project

log = logging.getLogger(__name__)


class AsyncTask:
    """A handle to a queued work item whose result can be ``await``-ed.

    The underlying work queue is poll-based (SQLite), so :meth:`result`
    uses ``asyncio.sleep`` between polls to yield control without
    blocking the event loop.
    """

    def __init__(self, task_id: str, queue: WorkQueue, tasks_dir: str):
        self.task_id = task_id
        self._queue = queue
        self._tasks_dir = tasks_dir

    async def result(self, timeout: float = 120.0, interval: float = 0.3) -> str | None:
        """Poll until the task completes and return the result text.

        Returns ``None`` on timeout or failure.
        """
        elapsed = 0.0
        polls = 0

        while elapsed < timeout:
            task = self._queue.get(self.task_id)
            polls += 1

            if task is None:
                log.warning("task %s vanished after %d polls", self.task_id, polls)
                return None

            if task.status in (TaskStatus.COMPLETED, "chained"):
                return self._read_result(task)

            if task.status == TaskStatus.FAILED:
                log.warning("task %s FAILED: %s", self.task_id, task.error_log)
                return None

            await asyncio.sleep(interval)
            elapsed += interval

        log.warning(
            "task %s timed out after %.0fs (%d polls)",
            self.task_id, elapsed, polls,
        )
        return None

    def status(self) -> str:
        """Return the current status string for this task."""
        task = self._queue.get(self.task_id)
        return task.status if task else "unknown"

    def _read_result(self, task) -> str | None:
        result_path = task.result_path or os.path.join(
            self._tasks_dir, self.task_id, "result.json",
        )
        if not os.path.isfile(result_path):
            log.warning("task %s completed but no result file at %s", self.task_id, result_path)
            return None
        try:
            with open(result_path, encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            log.error("task %s result unreadable at %s", self.task_id, result_path)
            return None

        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            return raw.get("content", str(raw))
        return str(raw)


class AgentContext:
    """Accumulates messages for an LLM call, then submits via the scheduler.

    Usage::

        ctx = scheduler.create_agent_context(agent="default")
        ctx.add_context("You are a router.", role="system")
        ctx.add_context(user_prompt, role="user")
        task = await ctx.submit(title="route message")
        result = await task.result()
    """

    def __init__(self, agent: str, scheduler: BaseScheduler):
        self.agent = agent
        self.messages: list[dict] = []
        self._scheduler = scheduler

    def add_context(self, content: str, role: str = "system") -> AgentContext:
        """Append a message and return ``self`` for chaining."""
        self.messages.append({"role": role, "content": content})
        return self

    async def submit(self, title: str = "") -> AsyncTask:
        """Queue the accumulated messages as an LLM task."""
        return await self._scheduler.submit_llm(
            messages=self.messages,
            agent=self.agent,
            title=title or f"agent:{self.agent}",
        )


class BaseScheduler:
    """Async-first base class for schedulers that dispatch LLM work.

    Subclasses implement domain logic (routing, planning, pipelines …)
    using :meth:`submit_llm`, :meth:`submit_tool`, and
    :meth:`create_agent_context` as building blocks.

    Parameters
    ----------
    config : AssaiConfig
    chat : ChatStore
    queue : WorkQueue
    stream_tracker : StreamTracker
    project : Project, optional
        Default project context available to all scheduler LLM calls.
    """

    def __init__(
        self,
        config: AssaiConfig,
        chat: ChatStore,
        queue: WorkQueue,
        stream_tracker: StreamTracker,
        project: Project | None = None,
    ):
        self.config = config
        self.chat = chat
        self.queue = queue
        self.tracker = stream_tracker
        self.project = project
        self.tasks_dir = config.worker.tasks_dir

        self._active_conversation: str = ""

    # ------------------------------------------------------------------
    # Task submission
    # ------------------------------------------------------------------

    async def submit_llm(
        self,
        messages: list[dict],
        agent: str = "default",
        title: str = "scheduler llm call",
    ) -> AsyncTask:
        """Queue an LLM completion and return an :class:`AsyncTask`."""
        task = self.queue.push(
            title=title,
            kind="llm_complete",
            spec_path="",
            agent=agent,
        )

        task_dir = os.path.join(self.tasks_dir, task.id)
        os.makedirs(task_dir, exist_ok=True)
        spec_path = os.path.join(task_dir, "conversation.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False)

        self.queue.update(task.id, spec_path=spec_path)
        self.queue.update(task.id, status=TaskStatus.READY)

        log.info("submit_llm  task_id=%s  agent=%s  title=%r", task.id, agent, title)
        return AsyncTask(task.id, self.queue, self.tasks_dir)

    async def submit_tool(
        self,
        tool: str,
        args: dict,
        title: str = "",
    ) -> AsyncTask:
        """Queue a tool call and return an :class:`AsyncTask`."""
        effective_title = title or f"tool: {tool}"
        payload = {
            "tool": tool,
            "args": args,
            "call_id": "",
            "conversation": self._active_conversation,
        }

        task = self.queue.push(
            title=effective_title,
            kind="tool_call",
            spec_path="",
            gpu=0,
        )

        task_dir = os.path.join(self.tasks_dir, task.id)
        os.makedirs(task_dir, exist_ok=True)
        spec_path = os.path.join(task_dir, "payload.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        self.queue.update(task.id, spec_path=spec_path)
        self.queue.update(task.id, status=TaskStatus.READY)

        log.info("submit_tool  task_id=%s  tool=%s", task.id, tool)
        return AsyncTask(task.id, self.queue, self.tasks_dir)

    # ------------------------------------------------------------------
    # Agent context builder
    # ------------------------------------------------------------------

    def create_agent_context(
        self,
        agent: str = "default",
        system_extra: str = "",
    ) -> AgentContext:
        """Create a mutable :class:`AgentContext` to build up before submitting.

        If *system_extra* is provided it is added as the first system
        message automatically.
        """
        ctx = AgentContext(agent, self)
        if system_extra:
            ctx.add_context(system_extra, role="system")
        return ctx

    # ------------------------------------------------------------------
    # User notifications
    # ------------------------------------------------------------------

    def notify(self, message: str, level: str = "info") -> None:
        """Push a status message to the user via the stream tracker.

        Only delivers if there is an active conversation to target.
        """
        if not self._active_conversation:
            return
        self.tracker.push(self._active_conversation, {
            "event_type": "scheduler_status",
            "data": {"message": message, "level": level},
        })

    def notify_progress(self, step: int, total: int, message: str = "") -> None:
        """Push a progress update (e.g. step 2 of 5)."""
        if not self._active_conversation:
            return
        self.tracker.push(self._active_conversation, {
            "event_type": "scheduler_progress",
            "data": {
                "step": step,
                "total": total,
                "message": message or f"Step {step}/{total}",
            },
        })
