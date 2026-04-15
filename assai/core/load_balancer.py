"""Load balancer — worker registry and work distribution.

Workers register themselves on startup via ``POST /workers/register``.
The orchestrator acquires a worker for each task using an async context
manager that waits for a free worker and auto-releases it.

A background reaper thread marks workers *offline* when no heartbeat
has been received within the configured timeout.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger(__name__)


class WorkerStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"


@dataclass
class WorkerInfo:
    """Metadata for a registered worker."""

    worker_id: str
    url: str
    capabilities: dict = field(default_factory=dict)
    last_heartbeat: float = field(default_factory=time.time)
    status: WorkerStatus = WorkerStatus.IDLE
    current_task: str = ""
    telemetry: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "url": self.url,
            "capabilities": self.capabilities,
            "status": self.status.value,
            "current_task": self.current_task,
            "last_heartbeat": self.last_heartbeat,
            "telemetry": self.telemetry,
        }


class LoadBalancer:
    """Thread-safe worker registry with round-robin selection.

    Parameters
    ----------
    heartbeat_timeout : float
        Seconds of silence before a worker is marked *offline*.
    reaper_interval : float
        How often (seconds) the reaper thread checks for stale workers.
    """

    def __init__(
        self,
        heartbeat_timeout: float = 30.0,
        reaper_interval: float = 10.0,
    ):
        self._lock = threading.Lock()
        self._workers: dict[str, WorkerInfo] = {}
        self._rr_index = 0
        self._heartbeat_timeout = heartbeat_timeout
        self._reaper_interval = reaper_interval
        self._stop_reaper = threading.Event()
        self._reaper_thread: threading.Thread | None = None

        self._worker_available = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background health-reaper thread."""
        if self._reaper_thread is not None:
            return
        self._stop_reaper.clear()
        self._reaper_thread = threading.Thread(
            target=self._reaper_loop, daemon=True, name="lb-reaper",
        )
        self._reaper_thread.start()
        log.info("load balancer reaper started  timeout=%.0fs", self._heartbeat_timeout)

    def stop(self) -> None:
        self._stop_reaper.set()
        if self._reaper_thread is not None:
            self._reaper_thread.join(timeout=5)
            self._reaper_thread = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, url: str, capabilities: dict | None = None) -> str:
        """Register a worker and return its id."""
        worker_id = uuid.uuid4().hex[:12]
        info = WorkerInfo(
            worker_id=worker_id,
            url=url.rstrip("/"),
            capabilities=capabilities or {},
        )
        with self._lock:
            self._workers[worker_id] = info
        self._notify_available()
        log.info("worker registered  id=%s  url=%s", worker_id, url)
        return worker_id

    def unregister(self, worker_id: str) -> bool:
        with self._lock:
            removed = self._workers.pop(worker_id, None)
        if removed:
            log.info("worker unregistered  id=%s  url=%s", worker_id, removed.url)
        return removed is not None

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def heartbeat(self, worker_id: str, telemetry: dict | None = None) -> bool:
        """Update heartbeat timestamp.  Returns ``False`` if unknown."""
        with self._lock:
            w = self._workers.get(worker_id)
            if w is None:
                return False
            w.last_heartbeat = time.time()
            if telemetry:
                w.telemetry = telemetry
            if w.status == WorkerStatus.OFFLINE:
                w.status = WorkerStatus.IDLE
                log.info("worker back online  id=%s", worker_id)
                self._notify_available()
            return True

    # ------------------------------------------------------------------
    # Selection (internal)
    # ------------------------------------------------------------------

    def _try_select(self, task_id: str = "") -> WorkerInfo | None:
        """Pick the next idle worker (round-robin), mark it busy atomically."""
        with self._lock:
            candidates = [
                w for w in self._workers.values()
                if w.status == WorkerStatus.IDLE
            ]
            if not candidates:
                return None
            self._rr_index = self._rr_index % len(candidates)
            chosen = candidates[self._rr_index]
            self._rr_index += 1
            chosen.status = WorkerStatus.BUSY
            chosen.current_task = task_id
            return chosen

    def release(self, worker_id: str) -> None:
        """Mark a worker as idle and notify waiters."""
        with self._lock:
            w = self._workers.get(worker_id)
            if w:
                w.status = WorkerStatus.IDLE
                w.current_task = ""
        self._notify_available()

    # ------------------------------------------------------------------
    # Async context manager — wait for a free worker, auto-release
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def acquire(self, task_id: str = "", timeout: float = 300):
        """Wait for an idle worker and yield it; release on exit.

        Usage::

            async with lb.acquire(task_id="abc") as worker:
                result = await dispatch_llm(worker.url, payload, ...)

        Raises ``TimeoutError`` if no worker becomes available within
        *timeout* seconds.
        """
        deadline = time.monotonic() + timeout
        worker: WorkerInfo | None = None

        while worker is None:
            worker = self._try_select(task_id)
            if worker is not None:
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"no worker available after {timeout:.0f}s"
                )

            self._worker_available.clear()
            try:
                await asyncio.wait_for(
                    self._worker_available.wait(),
                    timeout=min(remaining, 2.0),
                )
            except asyncio.TimeoutError:
                pass

        try:
            yield worker
        finally:
            self.release(worker.worker_id)

    # ------------------------------------------------------------------
    # Backward-compat helpers (used by tests and simple call sites)
    # ------------------------------------------------------------------

    def select(self) -> WorkerInfo | None:
        """Pick the next idle worker (round-robin). Does NOT mark busy."""
        with self._lock:
            candidates = [
                w for w in self._workers.values()
                if w.status == WorkerStatus.IDLE
            ]
            if not candidates:
                return None
            self._rr_index = self._rr_index % len(candidates)
            chosen = candidates[self._rr_index]
            self._rr_index += 1
            return chosen

    def mark_busy(self, worker_id: str, task_id: str = "") -> None:
        with self._lock:
            w = self._workers.get(worker_id)
            if w:
                w.status = WorkerStatus.BUSY
                w.current_task = task_id

    def mark_idle(self, worker_id: str) -> None:
        self.release(worker_id)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def list_workers(self) -> list[WorkerInfo]:
        with self._lock:
            return list(self._workers.values())

    def get(self, worker_id: str) -> WorkerInfo | None:
        with self._lock:
            return self._workers.get(worker_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _notify_available(self) -> None:
        """Signal waiters that a worker may be free."""
        try:
            self._worker_available.set()
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Reaper
    # ------------------------------------------------------------------

    def _reaper_loop(self) -> None:
        while not self._stop_reaper.is_set():
            self._reap()
            self._stop_reaper.wait(self._reaper_interval)

    def _reap(self) -> None:
        now = time.time()
        with self._lock:
            for w in self._workers.values():
                if w.status == WorkerStatus.OFFLINE:
                    continue
                if now - w.last_heartbeat > self._heartbeat_timeout:
                    log.warning(
                        "worker %s unresponsive (%.0fs), marking offline",
                        w.worker_id, now - w.last_heartbeat,
                    )
                    w.status = WorkerStatus.OFFLINE
                    w.current_task = ""
