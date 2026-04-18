"""Task graphs — composable multi-agent pipelines.

Re-exports the public API for convenience::

    from assai.tasks import TaskGraph, ConverseGraph, ThinkGraph, UberGraph, get_graph
"""

from assai.tasks.graph import Acc, TaskGraph
from assai.tasks.converse import ConverseGraph
from assai.tasks.converse_scribe import ConverseScribeGraph
from assai.tasks.think import ThinkGraph
from assai.tasks.uber import UberGraph
from assai.tasks.dynamic import DynamicGraph
from assai.tasks.registry import get_graph, list_graphs

__all__ = [
    "Acc", "TaskGraph",
    "ConverseGraph", "ConverseScribeGraph",
    "ThinkGraph", "UberGraph", "DynamicGraph",
    "get_graph", "list_graphs",
]
