"""Stream tracker — buffers in-progress LLM streams on the orchestrator.

The orchestrator owns a single ``StreamTracker`` instance.  It records
which conversation each task belongs to (via ``register``), and exposes
``get_partial`` so ``GET /history`` can recover accumulated text when a
client reconnects mid-stream.

Accumulation happens transparently: ``wrap_socketio`` returns a thin
proxy that intercepts ``chunk`` / ``stream_end`` / ``stream_error``
events emitted by the worker.  The worker never imports or references
the tracker — it just calls ``socketio.emit`` as usual.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask_socketio import SocketIO

log = logging.getLogger(__name__)


class StreamTracker:
    """Thread-safe accumulator for in-flight LLM token streams."""

    def __init__(self):
        self._lock = threading.Lock()
        self._buffers: dict[str, str] = {}
        self._task_to_conv: dict[str, str] = {}

    # -- orchestrator-facing API ----------------------------------------

    def register(self, task_id: str, conversation: str) -> None:
        """Record a task → conversation mapping before streaming starts."""
        with self._lock:
            self._task_to_conv[task_id] = conversation

    def get_partial(self, conversation: str) -> tuple[str | None, str]:
        """Return ``(task_id, partial_text)`` for *conversation*.

        Returns ``(None, "")`` if no stream is active.
        """
        with self._lock:
            for task_id, conv in self._task_to_conv.items():
                if conv == conversation and task_id in self._buffers:
                    return task_id, self._buffers[task_id]
        return None, ""

    def wrap_socketio(self, socketio: SocketIO) -> "_TrackedSocketIO":
        """Return a SocketIO-like proxy that intercepts streaming events."""
        return _TrackedSocketIO(socketio, self)

    # -- internal (called by the proxy) ---------------------------------

    def _on_chunk(self, task_id: str, token: str) -> None:
        with self._lock:
            if task_id in self._task_to_conv:
                self._buffers.setdefault(task_id, "")
                self._buffers[task_id] += token

    def _conv_for(self, task_id: str) -> str:
        """Return the conversation id for *task_id*, or ``""``."""
        with self._lock:
            return self._task_to_conv.get(task_id, "")

    def _on_stream_end(self, task_id: str) -> None:
        with self._lock:
            self._buffers.pop(task_id, None)
            self._task_to_conv.pop(task_id, None)


class _TrackedSocketIO:
    """Transparent proxy around SocketIO that feeds the tracker.

    The worker blueprint receives this instead of the raw SocketIO
    instance.  All method calls are forwarded; ``emit`` additionally
    feeds chunk data into the tracker so the orchestrator can serve
    partial content on reconnect.
    """

    def __init__(self, socketio: SocketIO, tracker: StreamTracker):
        self._sio = socketio
        self._tracker = tracker

    def emit(self, event: str, data=None, **kwargs):
        if isinstance(data, dict):
            task_id = data.get("task_id", "")
            if event == "chunk":
                self._tracker._on_chunk(task_id, data.get("token", ""))
            elif event in ("stream_end", "stream_error"):
                self._tracker._on_stream_end(task_id)

            if event in ("chunk", "stream_end", "stream_error") and "to" not in kwargs:
                conv = self._tracker._conv_for(task_id)
                if conv:
                    kwargs["to"] = f"conv:{conv}"

        self._sio.emit(event, data, **kwargs)

    def __getattr__(self, name):
        return getattr(self._sio, name)
