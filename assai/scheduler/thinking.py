"""ThinkScheduler — generator-based emulated reasoning.

Two-step flow:
1. **Think**: Run a thinker agent whose tokens stream as ``reasoning``
   events to the UI.
2. **Reply**: Run the main agent with the thinker's output injected,
   streaming tokens normally.  Tool-call follow-ups are handled the
   same way as :class:`ConversationScheduler`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from assai.scheduler.base import Scheduler
from assai.scheduler.conversation import ConversationScheduler
from assai.scheduler.types import StepResult, WorkStep

if TYPE_CHECKING:
    from assai.core.agent_store import AgentStore
    from assai.core.chat import ChatStore
    from assai.core.config import AssaiConfig
    from assai.core.projects import ProjectStore
    from assai.core.stream import StreamTracker
    from assai.queue.work import Task as QueueTask, WorkQueue

log = logging.getLogger(__name__)

THINKER_AGENT = "thinker"


class ThinkScheduler(Scheduler):
    """Think-then-reply scheduler using the generator protocol.

    Parameters
    ----------
    thinker_agent : str
        Agent name for the thinking step (default ``"thinker"``).
    tool_registry : optional
        Passed through for tool resolution during hydration.
    """

    def __init__(
        self,
        config: AssaiConfig,
        chat: ChatStore,
        queue: WorkQueue,
        tracker: StreamTracker,
        agent_store: AgentStore,
        projects: ProjectStore | None = None,
        tool_registry=None,
        thinker_agent: str = THINKER_AGENT,
    ):
        super().__init__(config, chat, queue, tracker, agent_store, projects)
        self.tool_registry = tool_registry
        self.thinker_agent = thinker_agent
        self._conversation_sched = ConversationScheduler(
            config=config,
            chat=chat,
            queue=queue,
            tracker=tracker,
            agent_store=agent_store,
            projects=projects,
            tool_registry=tool_registry,
        )

    async def run(
        self,
        task: QueueTask,
        conversation: str,
    ) -> AsyncGenerator[WorkStep, StepResult]:
        # Step 1: Think — thinker agent, tokens forwarded as reasoning
        think_payload = self._conversation_sched.hydrate(
            task, agent_override=self.thinker_agent,
        )
        reasoning_result: StepResult = yield WorkStep(
            payload=think_payload, stream_mode="reasoning",
        )

        # Step 2: Reply — main agent with reasoning injected
        reply_payload = self._conversation_sched.hydrate(
            task, injected_reasoning=reasoning_result.text,
        )
        result: StepResult = yield WorkStep(
            payload=reply_payload, stream_mode="token",
        )

        # Tool-call follow-up loop (same as ConversationScheduler)
        while result.tool_calls:
            tool_results: list[StepResult] = []
            dispatched_calls: list[dict] = []

            for call in result.tool_calls:
                tool_payload = self._conversation_sched._build_tool_payload(
                    call, task, conversation,
                )
                dispatched_calls.append(call)

                self.chat.append(conversation, {
                    "role": "tool_call",
                    "content": json.dumps(
                        {"tool": tool_payload["tool"], "args": tool_payload["args"]},
                        ensure_ascii=False,
                    ),
                    "name": tool_payload["tool"],
                })
                self.tracker.push(conversation, {
                    "event_type": "tool_start",
                    "data": {
                        "conversation": conversation,
                        "tool_name": tool_payload["tool"],
                        "args": tool_payload["args"],
                    },
                })

                tr: StepResult = yield WorkStep(
                    payload=tool_payload, kind="tool_call", stream_mode="tool",
                )
                tool_results.append(tr)

                result_preview = tr.text[:500] if tr.text else ""
                self.chat.append(conversation, {
                    "role": "tool_result",
                    "content": result_preview,
                    "name": tool_payload["tool"],
                })
                self.tracker.push(conversation, {
                    "event_type": "tool_end",
                    "data": {
                        "conversation": conversation,
                        "tool_name": tool_payload["tool"],
                        "result_preview": result_preview[:200],
                    },
                })

            followup_messages = self._conversation_sched._build_followup_messages(
                reply_payload["messages"], result, dispatched_calls, tool_results,
            )
            followup_payload = dict(reply_payload)
            followup_payload["messages"] = followup_messages

            result = yield WorkStep(payload=followup_payload, stream_mode="token")
