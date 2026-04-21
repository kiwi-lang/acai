"""Task graphs — composable multi-agent pipelines.

Re-exports the public API for convenience::

    from acai.tasks import TaskGraph, ConverseGraph, ThinkGraph, UberGraph, get_graph
"""

from acai.tasks.graph import Acc, TaskGraph
from acai.tasks.converse import ConverseGraph
from acai.tasks.converse_scribe import ConverseScribeGraph
from acai.tasks.think import ThinkGraph
from acai.tasks.uber import UberGraph
from acai.tasks.dynamic import DynamicGraph
from acai.tasks.registry import get_graph, list_graphs

__all__ = [
    "Acc", "TaskGraph",
    "ConverseGraph", "ConverseScribeGraph",
    "ThinkGraph", "UberGraph", "DynamicGraph",
    "get_graph", "list_graphs",
]
