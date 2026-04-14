"""Scheduler registry — maps ``Task.kind`` to scheduler classes.

The driver looks up the scheduler for a task via :func:`get_scheduler`
and then runs its async generator to execute the task graph.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from assai.scheduler.base import Scheduler
from assai.scheduler.conversation import ConversationScheduler
from assai.scheduler.thinking import ThinkScheduler

if TYPE_CHECKING:
    from assai.core.agent_store import AgentStore
    from assai.core.chat import ChatStore
    from assai.core.config import AssaiConfig
    from assai.core.projects import ProjectStore
    from assai.core.stream import StreamTracker
    from assai.queue.work import WorkQueue

log = logging.getLogger(__name__)

_SCHEDULER_CLASSES: dict[str, type[Scheduler]] = {
    "converse": ConversationScheduler,
    "llm_complete": ConversationScheduler,
    "think": ThinkScheduler,
}


def register(kind: str, cls: type[Scheduler]) -> None:
    """Register a scheduler class for a task kind."""
    _SCHEDULER_CLASSES[kind] = cls


def get_scheduler(
    kind: str,
    *,
    config: AssaiConfig,
    chat: ChatStore,
    queue: WorkQueue,
    tracker: StreamTracker,
    agent_store: AgentStore,
    projects: ProjectStore | None = None,
    tool_registry=None,
) -> Scheduler:
    """Instantiate the scheduler for *kind*, falling back to ConversationScheduler."""
    cls = _SCHEDULER_CLASSES.get(kind, ConversationScheduler)

    init_kwargs: dict = dict(
        config=config,
        chat=chat,
        queue=queue,
        tracker=tracker,
        agent_store=agent_store,
        projects=projects,
    )

    import inspect
    sig = inspect.signature(cls.__init__)
    if "tool_registry" in sig.parameters:
        init_kwargs["tool_registry"] = tool_registry

    return cls(**init_kwargs)
