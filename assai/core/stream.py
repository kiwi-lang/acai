"""Stream tracker — pub-sub hub for in-progress LLM streams.

The orchestrator owns a single ``StreamTracker`` instance.  It records
which conversation each task belongs to, buffers partial text for
reconnection, and fans out events to SSE subscribers.

Producers call ``push(conversation, event)`` to deliver events.
Consumers call ``subscribe(conversation)`` to get a ``Queue`` that
receives events, and ``unsubscribe`` to clean up.
"""

from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger(__name__)


class StreamTracker:
    """Thread-safe pub-sub hub for in-flight LLM token streams."""

    def __init__(self):
        self._lock = threading.Lock()
        self._buffers: dict[str, str] = {}
        self._task_to_conv: dict[str, str] = {}
        self._subscribers: dict[str, list[queue.Queue]] = {}

    # -- registration ------------------------------------------------------

    def register(self, task_id: str, conversation: str) -> None:
        """Record a task -> conversation mapping before streaming starts."""
        with self._lock:
            self._task_to_conv[task_id] = conversation

    def conv_for(self, task_id: str) -> str:
        """Return the conversation id for *task_id*, or ``""``."""
        with self._lock:
            return self._task_to_conv.get(task_id, "")

    # -- pub-sub -----------------------------------------------------------

    def push(self, conversation: str, event: dict) -> None:
        """Push *event* to all subscribers of *conversation*.

        Also accumulates partial text for ``token`` events so
        ``get_partial`` can serve reconnecting clients.
        """
        with self._lock:
            if event.get("event_type") == "token":
                self._buffers.setdefault(conversation, "")
                self._buffers[conversation] += event.get("data", {}).get("token", "")

            if event.get("event_type") == "done":
                self._buffers.pop(conversation, None)
                task_id = event.get("data", {}).get("task_id", "")
                self._task_to_conv.pop(task_id, None)

            for q in self._subscribers.get(conversation, []):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    log.warning("subscriber queue full for conv=%s, dropping event", conversation)

    def subscribe(self, conversation: str, maxsize: int = 4096) -> queue.Queue:
        """Return a queue that receives events for *conversation*."""
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.setdefault(conversation, []).append(q)
        return q

    def unsubscribe(self, conversation: str, q: queue.Queue) -> None:
        """Remove a subscriber queue."""
        with self._lock:
            subs = self._subscribers.get(conversation, [])
            try:
                subs.remove(q)
            except ValueError:
                pass
            if not subs:
                self._subscribers.pop(conversation, None)

    # -- reconnection support ----------------------------------------------

    def get_partial(self, conversation: str) -> tuple[str | None, str]:
        """Return ``(task_id, partial_text)`` for *conversation*.

        Returns ``(None, "")`` if no stream is active.
        """
        with self._lock:
            for task_id, conv in self._task_to_conv.items():
                if conv == conversation and conversation in self._buffers:
                    return task_id, self._buffers[conversation]
        return None, ""
