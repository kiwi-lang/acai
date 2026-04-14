"""ConversationScheduler — basic chat with tool follow-up support.

Single LLM call followed by an optional tool → follow-up loop.  This is
the default scheduler for ``kind="converse"`` (and the legacy
``"llm_complete"`` tasks that go through the driver).

The generator yields one :class:`WorkStep` per LLM / tool call.  The
orchestrator driver pushes each step to the work queue, collects stream
events from the worker, and feeds the accumulated
:class:`StepResult` back.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from assai.core.agent_store import hydrate_task, resolve_task
from assai.scheduler.base import Scheduler
from assai.scheduler.types import StepResult, WorkStep

if TYPE_CHECKING:
    from assai.core.agent_store import AgentStore
    from assai.core.chat import ChatStore
    from assai.core.config import AssaiConfig
    from assai.core.projects import ProjectStore
    from assai.core.stream import StreamTracker
    from assai.queue.work import Task as QueueTask, WorkQueue

log = logging.getLogger(__name__)


class ConversationScheduler(Scheduler):
    """Yield a single LLM step, then loop on tool-call follow-ups.

    Parameters
    ----------
    tool_registry : optional
        If provided, used to resolve MCP tool definitions for agents
        that declare ``tools``.
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
    ):
        super().__init__(config, chat, queue, tracker, agent_store, projects)
        self.tool_registry = tool_registry

    # ------------------------------------------------------------------
    # Hydration — builds the worker payload from a queue Task
    # ------------------------------------------------------------------

    def hydrate(
        self,
        task: QueueTask,
        *,
        agent_override: str = "",
        injected_reasoning: str = "",
    ) -> dict:
        """Resolve and render the task into a worker-ready payload dict.

        This is the logic previously inlined in ``_do_pop`` for
        ``llm_complete`` tasks.
        """
        resolved = resolve_task(task, self.config, self.chat, self.projects)
        agent_name = agent_override or resolved["agent"] or "default"
        agent_def = self.agent_store.get(agent_name) or self.agent_store.get("default")

        tool_defs, tools_desc = self.resolve_tools(agent_def)

        messages = hydrate_task(
            agent_def, self.agent_store, resolved,
            tools_description=tools_desc,
        )

        if injected_reasoning:
            reasoning_msg = {
                "role": "system",
                "content": (
                    "## Prior Reasoning\n"
                    "The following analysis was produced about this task. "
                    "Use it to inform your response.\n\n"
                    + injected_reasoning
                ),
            }
            pos = 1 if messages and messages[0].get("role") == "system" else 0
            messages.insert(pos, reasoning_msg)

        result: dict = {
            "task_id": task.id,
            "kind": task.kind,
            "messages": messages,
            "conversation": resolved["conversation"],
            "agent": agent_name,
            "compressor": agent_def.compressor,
        }
        if tool_defs:
            result["tools"] = tool_defs

        project_obj = resolved.get("project_obj")
        if project_obj and project_obj.path:
            result["project_path"] = project_obj.path
            result["project_name"] = resolved.get("project", "")

        if task.enable_thinking is not None:
            result["enable_thinking"] = task.enable_thinking

        return result

    # ------------------------------------------------------------------
    # Tool-call payload helpers
    # ------------------------------------------------------------------

    def _build_tool_payload(self, call: dict, task: QueueTask, conv_id: str) -> dict:
        fn = call.get("function", {})
        tool_name = fn.get("name", "unknown")
        try:
            tool_args = json.loads(fn.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            tool_args = {}

        return {
            "tool": tool_name,
            "args": tool_args,
            "call_id": call.get("id", ""),
            "conversation": conv_id,
            "project": task.project or "",
            "agent": task.agent or "",
        }

    def _build_followup_messages(
        self,
        original_messages: list[dict],
        assistant_result: StepResult,
        dispatched_calls: list[dict],
        tool_results: list[StepResult],
    ) -> list[dict]:
        """Build the message list for a follow-up LLM call after tools."""
        followup = list(original_messages)

        assistant_msg: dict = {
            "role": "assistant",
            "content": assistant_result.text or None,
            "tool_calls": dispatched_calls,
        }
        if assistant_result.reasoning:
            assistant_msg["reasoning"] = assistant_result.reasoning
        followup.append(assistant_msg)

        for call, tr in zip(dispatched_calls, tool_results):
            followup.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": tr.text or "",
            })

        return followup

    # ------------------------------------------------------------------
    # Generator — the scheduler's task graph
    # ------------------------------------------------------------------

    async def run(
        self,
        task: QueueTask,
        conversation: str,
    ) -> AsyncGenerator[WorkStep, StepResult]:
        payload = self.hydrate(task)
        result: StepResult = yield WorkStep(payload=payload, stream_mode="token")

        while result.tool_calls:
            tool_results: list[StepResult] = []
            dispatched_calls: list[dict] = []

            for call in result.tool_calls:
                tool_payload = self._build_tool_payload(call, task, conversation)
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

            followup_messages = self._build_followup_messages(
                payload["messages"], result, dispatched_calls, tool_results,
            )
            followup_payload = dict(payload)
            followup_payload["messages"] = followup_messages

            result = yield WorkStep(payload=followup_payload, stream_mode="token")
