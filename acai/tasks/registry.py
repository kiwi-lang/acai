"""Task-graph registry — maps ``kind`` to a TaskGraph subclass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acai.tasks.graph import TaskGraph
    from acai.orchestrator.load_balancer import WorkerInfo

from acai.tasks.converse import ConverseGraph
from acai.tasks.converse_scribe import ConverseScribeGraph
from acai.tasks.think import ThinkGraph
from acai.tasks.uber import UberGraph
from acai.tasks.dynamic import DynamicGraph


@dataclass
class GraphDef:
    kind: str
    cls: type
    label: str
    description: str
    user_facing: bool = True

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "description": self.description,
        }


_GRAPHS: dict[str, GraphDef] = {
    "converse": GraphDef(
        "converse", ConverseGraph,
        "Converse",
        "Single agent conversation with tool follow-ups.",
    ),
    "converse_scribe": GraphDef(
        "converse_scribe", ConverseScribeGraph,
        "Converse + Knowledge (Python)",
        "Curates knowledge before replying, then updates the knowledge base after.",
    ),
    "think": GraphDef(
        "think", ThinkGraph,
        "Think → Reply",
        "Two-phase: a thinker agent reasons first, then the reply agent responds.",
    ),
    "uber": GraphDef(
        "uber", UberGraph,
        "Uber Router",
        "Routes messages to the right conversation automatically.",
        user_facing=False,
    ),
    "workflow": GraphDef(
        "workflow", DynamicGraph,
        "Workflow",
        "Executes a node-based workflow defined by a JSON spec.",
        user_facing=False,
    ),
}


def register(kind: str, cls: type, *, label: str = "", description: str = "", user_facing: bool = True) -> None:
    """Register a graph class for a task kind."""
    _GRAPHS[kind] = GraphDef(kind, cls, label or kind, description, user_facing)


def get_graph(kind: str, worker, work: dict, **deps):
    """Instantiate the graph for *kind*, falling back to ConverseGraph."""
    gdef = _GRAPHS.get(kind)
    cls = gdef.cls if gdef else ConverseGraph
    return cls.from_work(worker, work, **deps)


def list_graphs(*, user_facing_only: bool = True) -> list[dict]:
    """Return graph definitions for the UI."""
    return [
        gdef.to_dict()
        for gdef in _GRAPHS.values()
        if not user_facing_only or gdef.user_facing
    ]
