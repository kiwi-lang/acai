"""Input queue — asyncio Future-based message passing.

Provides a general-purpose mechanism for awaiting responses across
async boundaries.  Used by the TaskRunner for sub-agent coordination.

Note: User interaction (ask_user, confirm) does NOT use this — those
tools render as UI elements in the conversation and the user's
response is simply their next message.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class InputRequest:
    """A pending input request from an agent to the user."""

    conversation_id: str
    request_id: str
    question: str
    options: list[dict] = field(default_factory=list)
    allow_free_text: bool = True
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class InputQueue:
    """Per-conversation pending input requests with asyncio Future resolution."""

    def __init__(self):
        self._pending: dict[str, asyncio.Future[dict]] = {}
        self._requests: dict[str, InputRequest] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def has_pending(self, conversation_id: str) -> bool:
        return conversation_id in self._pending

    def get_request(self, conversation_id: str) -> InputRequest | None:
        return self._requests.get(conversation_id)

    async def wait_for_input(
        self,
        conversation_id: str,
        request: InputRequest,
        timeout: float = 300.0,
    ) -> dict:
        """Block until the user provides input or timeout expires.

        Args:
            conversation_id: The conversation awaiting input.
            request: The structured input request.
            timeout: Max seconds to wait (default 5 minutes).

        Returns:
            The user's response dict (keys: ``choice``, ``text``, ``request_id``).

        Raises:
            TimeoutError: If no response arrives within *timeout* seconds.
            RuntimeError: If there's already a pending request for this conversation.
        """
        if conversation_id in self._pending:
            raise RuntimeError(
                f"Conversation {conversation_id!r} already has a pending input request"
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self._pending[conversation_id] = future
        self._requests[conversation_id] = request

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            log.warning(
                "Input request timed out for conversation %s after %.0fs",
                conversation_id, timeout,
            )
            raise TimeoutError(
                f"User did not respond within {timeout:.0f} seconds"
            )
        finally:
            self._pending.pop(conversation_id, None)
            self._requests.pop(conversation_id, None)

    def submit_input(self, conversation_id: str, response: dict) -> bool:
        """Resolve a pending input request with the user's response.

        Called by the REST endpoint when the user answers.

        Args:
            conversation_id: The conversation ID.
            response: The user's response (``choice``, ``text``, etc.).

        Returns:
            True if a pending request was resolved, False if none was pending.
        """
        future = self._pending.get(conversation_id)
        if future is None:
            log.warning(
                "submit_input called for %s but no pending request",
                conversation_id,
            )
            return False

        if future.done():
            log.warning(
                "submit_input called for %s but future already resolved",
                conversation_id,
            )
            return False

        future.set_result(response)
        return True

    def cancel(self, conversation_id: str, reason: str = "cancelled") -> bool:
        """Cancel a pending input request.

        Args:
            conversation_id: The conversation ID.
            reason: Cancellation reason.

        Returns:
            True if a pending request was cancelled.
        """
        future = self._pending.get(conversation_id)
        if future is None or future.done():
            return False

        future.set_result({"cancelled": True, "reason": reason})
        return True

    def cancel_all(self) -> int:
        """Cancel all pending requests. Returns count cancelled."""
        count = 0
        for conv_id in list(self._pending):
            if self.cancel(conv_id, reason="shutdown"):
                count += 1
        return count
