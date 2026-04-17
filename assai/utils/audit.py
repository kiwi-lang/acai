"""Request-level audit trail for the orchestrator.

Records events, timing, and payloads throughout the lifecycle of a
single request.  Each request gets a UUID that follows it from the
server endpoint through the ``TaskGraph`` and back.

**Filesystem layout** (when using the default filesystem backend)::

    {audit_dir}/
        {request_id}/
            audit.json         # event timeline + summary
            payload-{label}.json   # saved message payloads
        latest -> {request_id}     # symlink to most recent run

Toggle via ``AuditConfig.enabled`` or the ``ASSAI_AUDIT_ENABLED`` env
var.  When disabled, callers receive a ``NullAuditTrail`` whose methods
are all no-ops — no ``if audit:`` guards needed anywhere.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# NullAuditTrail — zero-cost no-op (used when auditing is disabled)
# ------------------------------------------------------------------

class NullAuditTrail:
    """Drop-in replacement for ``AuditTrail`` that does nothing.

    Every public method is a no-op so callers can use it without
    any conditional checks.
    """

    request_id: str = ""

    def set_meta(self, **kwargs: Any) -> None: ...
    def record(self, event: str, *, phase: str = "", **data: Any) -> None: ...
    def save_payload(self, label: str, payload: Any) -> None: ...
    def finalize(self) -> None: ...

    @contextmanager
    def span(self, name: str, *, phase: str = "", **data: Any) -> Iterator[None]:
        yield

    @asynccontextmanager
    async def aspan(
        self, name: str, *, phase: str = "", **data: Any,
    ) -> AsyncIterator[None]:
        yield


# ------------------------------------------------------------------
# AuditTrail — the real implementation
# ------------------------------------------------------------------

class AuditTrail:
    """Collects timestamped events for a single request.

    Parameters
    ----------
    request_id:
        Unique identifier for this request.  Generated if omitted.
    output_dir:
        Filesystem directory where audit artefacts are written.
        When empty, events are collected in-memory only.
    """

    def __init__(
        self,
        request_id: str | None = None,
        *,
        output_dir: str = "",
    ):
        self.request_id: str = request_id or uuid.uuid4().hex[:16]
        self.output_dir: str = output_dir
        self.events: list[dict[str, Any]] = []
        self._t0: float = time.monotonic()
        self._wall_start: float = time.time()
        self._meta: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def set_meta(self, **kwargs: Any) -> None:
        """Attach top-level metadata (conversation, agent, endpoint, …)."""
        self._meta.update(kwargs)

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def record(self, event: str, *, phase: str = "", **data: Any) -> None:
        """Append a single timestamped event."""
        self.events.append({
            "ts": time.time(),
            "elapsed_ms": round((time.monotonic() - self._t0) * 1000, 2),
            "event": event,
            "phase": phase,
            **data,
        })

    # ------------------------------------------------------------------
    # Context managers for tracking spans
    # ------------------------------------------------------------------

    @contextmanager
    def span(self, name: str, *, phase: str = "", **data: Any) -> Iterator[None]:
        """Synchronous context manager that records *name*.start / .end."""
        self.record(f"{name}.start", phase=phase, **data)
        t0 = time.monotonic()
        try:
            yield
        except Exception as exc:
            dur = round((time.monotonic() - t0) * 1000, 2)
            self.record(
                f"{name}.error", phase=phase, duration_ms=dur, error=str(exc),
                **data,
            )
            raise
        else:
            dur = round((time.monotonic() - t0) * 1000, 2)
            self.record(f"{name}.end", phase=phase, duration_ms=dur, **data)

    @asynccontextmanager
    async def aspan(
        self, name: str, *, phase: str = "", **data: Any,
    ) -> AsyncIterator[None]:
        """Async context manager that records *name*.start / .end."""
        self.record(f"{name}.start", phase=phase, **data)
        t0 = time.monotonic()
        try:
            yield
        except Exception as exc:
            dur = round((time.monotonic() - t0) * 1000, 2)
            self.record(
                f"{name}.error", phase=phase, duration_ms=dur, error=str(exc),
                **data,
            )
            raise
        else:
            dur = round((time.monotonic() - t0) * 1000, 2)
            self.record(f"{name}.end", phase=phase, duration_ms=dur, **data)

    # ------------------------------------------------------------------
    # Payload snapshots
    # ------------------------------------------------------------------

    def save_payload(self, label: str, payload: Any) -> None:
        """Persist an arbitrary JSON-serialisable object to a file.

        Also records a ``payload.saved`` event in the timeline.
        """
        self.record("payload.saved", label=label)
        if not self.output_dir:
            return
        req_dir = self._ensure_dir()
        path = os.path.join(req_dir, f"payload-{label}.json")
        try:
            with open(path, "w") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
        except Exception:
            log.debug("audit: failed to write payload %s", path, exc_info=True)

    # ------------------------------------------------------------------
    # Finalisation
    # ------------------------------------------------------------------

    def finalize(self) -> None:
        """Write the event log to disk and update the ``latest`` symlink.

        Safe to call multiple times — subsequent calls overwrite the
        previous log.  Does nothing when *output_dir* is unset.
        """
        if not self.output_dir:
            return

        total_ms = round((time.monotonic() - self._t0) * 1000, 2)
        self.record("finalize", phase="audit", total_duration_ms=total_ms)

        summary: dict[str, Any] = {
            "request_id": self.request_id,
            "started_at_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(self._wall_start),
            ),
            "started_at": self._wall_start,
            "total_duration_ms": total_ms,
            "meta": self._meta,
            "events": self.events,
        }

        req_dir = self._ensure_dir()
        log_path = os.path.join(req_dir, "audit.json")
        try:
            with open(log_path, "w") as fh:
                json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)
        except Exception:
            log.warning("audit: failed to write %s", log_path, exc_info=True)
            return

        self._update_latest_symlink()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_dir(self) -> str:
        req_dir = os.path.join(self.output_dir, self.request_id)
        os.makedirs(req_dir, exist_ok=True)
        return req_dir

    def _update_latest_symlink(self) -> None:
        latest = os.path.join(self.output_dir, "latest")
        try:
            tmp = latest + f".{os.getpid()}"
            os.symlink(self.request_id, tmp)
            os.replace(tmp, latest)
        except OSError:
            log.debug("audit: could not update latest symlink", exc_info=True)
