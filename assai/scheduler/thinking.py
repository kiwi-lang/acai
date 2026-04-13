"""ThinkingScheduler — orchestrates emulated reasoning.

Composition logic lives here, not in the worker or the stream handler.

Flow:
1. ``schedule()`` pushes a thinker task and records continuation metadata.
2. The stream handler calls ``is_thinking_task()`` to remap tokens → reasoning.
3. On thinker "done", the stream handler calls ``on_complete()`` which:
   - Chains the main-agent task with reasoning stored in ``ext.injected_reasoning``
   - Returns ``True`` so the stream handler suppresses the "done" event
     (the SSE stays open for the main task's events).
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from assai.queue.work import TaskStatus

if TYPE_CHECKING:
    from assai.core.chat import ChatStore
    from assai.core.stream import StreamTracker
    from assai.queue.work import WorkQueue

log = logging.getLogger(__name__)

THINKER_AGENT = "thinker"


class ThinkingScheduler:
    def __init__(self, chat: ChatStore, queue: WorkQueue, tracker: StreamTracker):
        self._chat = chat
        self._queue = queue
        self._tracker = tracker
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}

    def schedule(
        self,
        conversation: str,
        agent: str = "default",
        project: str = "",
        parent_task: str = "",
        title: str = "",
    ) -> dict:
        """Push the thinker task and register for chaining."""
        root = self._queue.resolve_root(parent_task) if parent_task else ""
        conv_path = self._chat._msg_path(conversation)

        task = self._queue.push(
            title=title or "think",
            kind="llm_complete",
            spec_path=conv_path,
            project=project,
            agent=THINKER_AGENT,
            parent_task=parent_task,
            root_task=root,
            conversation=conversation,
        )
        self._queue.update(task.id, status=TaskStatus.READY)
        self._tracker.register(task.id, conversation)

        with self._lock:
            self._pending[task.id] = {
                "agent": agent,
                "project": project,
                "parent_task": parent_task,
                "conversation": conversation,
            }

        log.info(
            "[%s] thinker task queued  conversation=%s  continuation_agent=%s",
            task.id, conversation, agent,
        )
        return {"task_id": task.id, "conversation": conversation}

    def is_thinking_task(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._pending

    def on_complete(self, task_id: str, reasoning: str) -> bool:
        """Chain the main-agent task after the thinker finishes.

        Returns ``True`` if this task was a thinker (caller should
        suppress the normal "done" event), ``False`` otherwise.
        """
        with self._lock:
            meta = self._pending.pop(task_id, None)
        if meta is None:
            return False

        conversation = meta["conversation"]
        agent = meta["agent"]
        parent = meta.get("parent_task", "")
        project = meta.get("project", "")

        root = self._queue.resolve_root(parent) if parent else ""
        conv_path = self._chat._msg_path(conversation)

        main_task = self._queue.push(
            title="converse (post-think)",
            kind="llm_complete",
            spec_path=conv_path,
            project=project,
            agent=agent,
            parent_task=parent,
            root_task=root,
            conversation=conversation,
        )
        self._queue.update(main_task.id, ext={
            "injected_reasoning": reasoning,
        })
        self._queue.update(main_task.id, status=TaskStatus.READY)
        self._tracker.register(main_task.id, conversation)
        self._queue.update(task_id, status="chained")

        log.info(
            "[%s] thinker done — chained main task %s  agent=%s  reasoning=%d chars",
            task_id, main_task.id, agent, len(reasoning),
        )
        return True
