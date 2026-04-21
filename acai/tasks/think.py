"""ThinkGraph — two-phase think-then-reply with tool follow-ups."""

from __future__ import annotations

import logging
import traceback as _tb
from typing import AsyncIterator

from acai.tasks.graph import Acc, TaskGraph

log = logging.getLogger(__name__)

THINKER_AGENT = "thinker"


class ThinkGraph(TaskGraph):
    """Think → reply → tool loop → persist.

    Phase 1 dispatches the *thinker* agent; its tokens stream as
    ``reasoning`` events.  Phase 2 dispatches the main agent with
    the reasoning injected, then enters the standard tool-call
    follow-up loop.
    """

    async def run(self, work: dict) -> AsyncIterator[dict]:
        thinker_agent = work.get("thinker_agent", THINKER_AGENT)
        agent_name = work.get("agent", "default")

        # Phase 1 — think
        try:
            think_payload = self.prepare(thinker_agent, work)
        except Exception as exc:
            log.exception("ThinkGraph prepare (thinker) error")
            yield self._error_event(
                f"Failed to prepare thinker agent '{thinker_agent}': {exc}",
                _tb.format_exc(),
            )
            return

        think_acc = Acc(self.dispatch(think_payload, stream_mode="reasoning"))
        async for event in think_acc:
            yield event
            if event.get("event_type") == "error":
                return

        # Phase 2 — reply using the reasoning
        try:
            reply_payload = self.prepare(agent_name, work)
            if think_acc.reasoning:
                msg = {
                    "role": "system",
                    "content": (
                        "## Prior Reasoning\n"
                        "The following analysis was produced about this task. "
                        "Use it to inform your response.\n\n"
                        + think_acc.reasoning
                    ),
                }
                msgs = reply_payload.get("messages", [])
                pos = 1 if msgs and msgs[0].get("role") == "system" else 0
                msgs.insert(pos, msg)
        except Exception as exc:
            log.exception("ThinkGraph prepare (reply) error")
            yield self._error_event(
                f"Failed to prepare reply agent '{agent_name}': {exc}",
                _tb.format_exc(),
            )
            return

        async for event in self._run_with_tools(reply_payload):
            yield event
            if event.get("event_type") == "error":
                return

        self._save_response(self._last_acc)
        yield self._done_event()
