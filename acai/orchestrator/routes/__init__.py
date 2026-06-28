"""Orchestrator route modules — split from the monolithic server.py.

Each module exports a ``create_*_router(deps)`` factory that returns
a FastAPI ``APIRouter`` bound to the shared application state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acai.knowledge import KnowledgeDB, KnowledgeStore
    from acai.orchestrator.agent_store import AgentStore
    from acai.orchestrator.chat import ChatStore
    from acai.orchestrator.config import AcaiConfig
    from acai.orchestrator.load_balancer import LoadBalancer
    from acai.orchestrator.projects import ProjectStore
    from acai.orchestrator.skill_store import SkillStore
    from acai.orchestrator.stream import StreamTracker
    from acai.orchestrator.tools import ToolRegistry
    from acai.orchestrator.events import EventBus
    from acai.queue.work import WorkQueue


@dataclass
class RouterDeps:
    """Shared dependencies injected into each route factory."""

    config: AcaiConfig
    queue: WorkQueue
    chat: ChatStore
    agent_store: AgentStore
    knowledge: KnowledgeStore
    knowledge_db: KnowledgeDB
    skill_store: SkillStore
    tool_registry: ToolRegistry
    projects: ProjectStore
    tracker: StreamTracker
    events: EventBus
    load_balancer: LoadBalancer
    workflows_dir: str = ""
    builtin_wf_dir: str = ""
    socketio_ref: list = field(default_factory=lambda: [None])
