"""Task-graph registry — maps ``kind`` to a TaskGraph subclass."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assai.core.graph import TaskGraph
    from assai.core.load_balancer import WorkerInfo

from assai.tasks.converse import ConverseGraph
from assai.tasks.think import ThinkGraph

_GRAPHS: dict[str, type] = {
    "converse": ConverseGraph,
    "llm_complete": ConverseGraph,
    "think": ThinkGraph,
}


def register(kind: str, cls: type) -> None:
    """Register a graph class for a task kind."""
    _GRAPHS[kind] = cls


def get_graph(kind: str, worker: WorkerInfo, work: dict, **deps) -> TaskGraph:
    """Instantiate the graph for *kind*, falling back to ConverseGraph."""
    cls = _GRAPHS.get(kind, ConverseGraph)
    return cls.from_work(worker, work, **deps)
