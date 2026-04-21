"""Stream tracker — pub-sub hub for in-progress LLM streams.

The orchestrator owns a single ``StreamTracker`` instance.  It records
which stream (keyed by the root task id) each sub-task belongs to,
buffers partial text for reconnection, and fans out events to SSE
subscribers.

Producers call ``push(stream_id, event)`` to deliver events.
Consumers call ``subscribe(stream_id)`` to get a ``Queue`` that
receives events, and ``unsubscribe`` to clean up.
"""

from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger(__name__)


class StreamTracker:
    """Thread-safe pub-sub hub for in-flight LLM token streams.

    The *stream_id* is typically the conversation id.  ``TaskGraph``
    subclasses push events here so UI clients can subscribe via the
    ``/stream/{stream_id}`` SSE endpoint.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._buffers: dict[str, str] = {}
        self._task_to_stream: dict[str, str] = {}
        self._subscribers: dict[str, list[queue.Queue]] = {}

    # -- registration ------------------------------------------------------

    def register(self, task_id: str, stream_id: str) -> None:
        """Map *task_id* → *stream_id* before streaming starts."""
        with self._lock:
            self._task_to_stream[task_id] = stream_id

    def stream_for(self, task_id: str) -> str:
        """Return the stream id for *task_id*, or ``""``."""
        with self._lock:
            return self._task_to_stream.get(task_id, "")

    # back-compat alias used by stream/push resolution
    conv_for = stream_for

    # -- pub-sub -----------------------------------------------------------

    def push(self, stream_id: str, event: dict) -> None:
        """Push *event* to all subscribers of *stream_id*.

        Also accumulates partial text for ``token`` events so
        ``get_partial`` can serve reconnecting clients.
        """
        with self._lock:
            if event.get("event_type") == "token":
                self._buffers.setdefault(stream_id, "")
                self._buffers[stream_id] += event.get("data", {}).get("token", "")

            if event.get("event_type") == "done":
                self._buffers.pop(stream_id, None)
                task_id = event.get("data", {}).get("task_id", "")
                self._task_to_stream.pop(task_id, None)

            for q in self._subscribers.get(stream_id, []):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    log.warning("subscriber queue full for stream=%s, dropping event", stream_id)

    def subscribe(self, stream_id: str, maxsize: int = 4096) -> queue.Queue:
        """Return a queue that receives events for *stream_id*."""
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.setdefault(stream_id, []).append(q)
        return q

    def unsubscribe(self, stream_id: str, q: queue.Queue) -> None:
        """Remove a subscriber queue."""
        with self._lock:
            subs = self._subscribers.get(stream_id, [])
            try:
                subs.remove(q)
            except ValueError:
                pass
            if not subs:
                self._subscribers.pop(stream_id, None)

    # -- reconnection support ----------------------------------------------

    def get_partial(self, stream_id: str) -> tuple[str | None, str]:
        """Return ``(task_id, partial_text)`` for *stream_id*.

        Returns ``(None, "")`` if no stream is active.  A registered task
        is considered active even before the first token arrives.
        """
        with self._lock:
            for task_id, sid in self._task_to_stream.items():
                if sid == stream_id:
                    return task_id, self._buffers.get(stream_id, "")
        return None, ""
