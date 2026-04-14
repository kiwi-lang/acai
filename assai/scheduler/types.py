"""Scheduler data types — shared between schedulers and the driver loop.

``WorkStep`` is yielded by a scheduler's ``run()`` generator to describe
the next unit of work for the worker.  ``StepResult`` is sent back via
``asend()`` once the worker finishes that step.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkStep:
    """A single unit of work yielded by a scheduler.

    Attributes
    ----------
    payload : dict
        Hydrated work payload for the worker (messages, tools, etc.).
    kind : str
        Worker dispatch kind — ``"llm_complete"`` or ``"tool_call"``.
    stream_mode : str
        Controls how stream events are forwarded to the ``StreamTracker``:

        * ``"token"``     — forward tokens as ``token`` events (default).
        * ``"reasoning"`` — forward tokens as ``reasoning`` events.
        * ``"silent"``    — don't forward to tracker (internal steps).
        * ``"tool"``      — forward ``tool_start`` / ``tool_end`` events.
    """

    payload: dict
    kind: str = "llm_complete"
    stream_mode: str = "token"


@dataclass
class StepResult:
    """Accumulated result of a single work step.

    Sent back to the scheduler generator via ``asend()`` after the
    worker streams all events for that step.
    """

    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None
