"""ConverseGraph — single-agent conversation with tool follow-ups."""

from __future__ import annotations

import logging
import traceback as _tb
from typing import AsyncIterator

from acai.tasks.graph import Acc, TaskGraph

log = logging.getLogger(__name__)


class ConverseGraph(TaskGraph):
    """Single agent → dispatch → tool-call follow-up loop → persist.

    This is the most common graph and matches the behaviour of the
    old ``ConversationScheduler``.
    """

    async def run(self, work: dict) -> AsyncIterator[dict]:
        try:
            agent_name = work.get("agent", "default")
            payload = self.prepare(agent_name, work)
        except Exception as exc:
            log.exception("ConverseGraph prepare error")
            yield self._error_event(
                f"Failed to prepare agent '{work.get('agent', 'default')}': {exc}",
                _tb.format_exc(),
            )
            return

        async for event in self._run_with_tools(payload):
            yield event
            if event.get("event_type") == "error":
                return

        self._save_response(self._last_acc)
        yield self._done_event()
