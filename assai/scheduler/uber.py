"""Uber-conversation scheduler — conversation-aware message routing.

The uber chat is a single input that automatically routes each message
to the most relevant conversation.  The scheduler:

1. Collects metadata (id, title, description, tags) for every conversation.
2. Queues a fast **routing** task that asks the LLM to pick the best
   conversation (or create a new one).
3. Returns the target conversation to the caller, which then delegates
   the actual response to a :class:`ConversationScheduler` via the
   scheduler driver.

The scheduler never creates an LLM client — all inference goes through
the shared work queue.
"""

from __future__ import annotations

import json
import logging
import re

from assai.scheduler.base import BaseScheduler

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assai.core.chat import ChatStore
    from assai.core.config import AssaiConfig
    from assai.core.projects import Project
    from assai.core.stream import StreamTracker
    from assai.queue.work import WorkQueue

log = logging.getLogger(__name__)

_ROUTER_SYSTEM_PROMPT = """\
You are a conversation router.  Given a user message and a catalogue of
existing conversations, decide which conversation this message belongs to.

Return a JSON object — nothing else (no markdown fences, no commentary):

  Existing conversation:  {"id": "<conversation_id>"}
  New conversation:       {"id": "new", "title": "<2-6 word title>", "tags": ["tag1", "tag2"]}

Rules:
- If the message clearly continues an existing conversation, return its exact id.
- When a currently_active conversation is provided, PREFER it unless the message
  is clearly about a different topic.
- Only return "new" when the message does not fit ANY existing conversation.
- For new conversations, provide a concise title and 2-5 topic tags."""


class UberScheduler(BaseScheduler):
    """Routes user messages to the right conversation via the work queue.

    All LLM work is dispatched through the shared :class:`WorkQueue` —
    the scheduler only writes conversation files, pushes tasks, and polls
    for results.
    """

    def __init__(
        self,
        config: AssaiConfig,
        chat: ChatStore,
        queue: WorkQueue,
        stream_tracker: StreamTracker,
        project: Project | None = None,
    ):
        super().__init__(config, chat, queue, stream_tracker, project=project)
        log.info("UberScheduler initialised  tasks_dir=%s", self.tasks_dir)

    # ------------------------------------------------------------------
    # Conversation catalogue
    # ------------------------------------------------------------------

    def _build_catalogue(self) -> list[dict]:
        """Return lightweight metadata for every conversation."""
        catalogue = []
        for c in self.chat.list():
            catalogue.append({
                "id": c["id"],
                "title": c.get("title", ""),
                "description": c.get("description", ""),
                "tags": c.get("tags", []),
            })
        return catalogue

    # ------------------------------------------------------------------
    # Routing via the queue
    # ------------------------------------------------------------------

    async def _route_message(self, message: str, current_conv_id: str = "") -> dict:
        """Queue a routing task and return the parsed decision.

        Returns ``{"id": "<conv_id>"}`` or
        ``{"id": "new", "title": "...", "tags": [...]}``.
        """
        catalogue = self._build_catalogue()
        log.info(
            "routing message  catalogue_size=%d  current_conv=%s  message=%r",
            len(catalogue), current_conv_id or "(none)", message[:80],
        )

        if catalogue:
            lines = []
            for c in catalogue:
                tags_str = ", ".join(c["tags"]) if c["tags"] else ""
                desc = c["description"]
                parts = [f'id={c["id"]}', f'title="{c["title"]}"']
                if desc:
                    parts.append(f'description="{desc}"')
                if tags_str:
                    parts.append(f"tags=[{tags_str}]")
                lines.append("- " + "  ".join(parts))
            catalogue_text = "\n".join(lines)
        else:
            catalogue_text = "(no conversations yet)"

        active_hint = ""
        if current_conv_id:
            current = next((c for c in catalogue if c["id"] == current_conv_id), None)
            if current:
                active_hint = (
                    f'\ncurrently_active: {current_conv_id} — "{current["title"]}"'
                    f"\n(Prefer this conversation unless the message is clearly about a different topic.)\n"
                )

        user_prompt = (
            f"Conversations:\n{catalogue_text}\n"
            f"{active_hint}\n"
            f"User message:\n{message}"
        )

        ctx = self.create_agent_context(agent="default")
        ctx.add_context(_ROUTER_SYSTEM_PROMPT, role="system")
        ctx.add_context(user_prompt, role="user")

        self.notify("Routing message to conversation...")
        task = await ctx.submit(title="uber: route message")
        result = await task.result(timeout=60.0)

        if not result:
            log.warning("routing returned empty — falling back to new conversation")
            return {"id": "new", "title": message.strip().split("\n")[0][:60], "tags": []}

        return self._parse_routing_result(result, catalogue, message)

    def _parse_routing_result(self, raw: str, catalogue: list[dict], message: str) -> dict:
        """Extract the JSON decision from the LLM output."""
        text = raw.strip()

        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

        try:
            decision = json.loads(text)
            if isinstance(decision, dict) and "id" in decision:
                conv_id = decision["id"]
                if conv_id == "new":
                    title = decision.get("title", message.strip().split("\n")[0][:60])
                    tags = decision.get("tags", [])
                    if isinstance(tags, str):
                        tags = [t.strip() for t in tags.split(",") if t.strip()]
                    log.info("routing decision: NEW  title=%r  tags=%s", title, tags)
                    return {"id": "new", "title": title, "tags": tags}

                valid_ids = {c["id"] for c in catalogue}
                if conv_id in valid_ids:
                    log.info("routing decision: existing conv %s", conv_id)
                    return {"id": conv_id}

                log.warning("LLM returned unknown conv id %r — creating new", conv_id)
                return {"id": "new", "title": message.strip().split("\n")[0][:60], "tags": []}

        except (json.JSONDecodeError, TypeError, KeyError):
            pass

        for c in catalogue:
            if c["id"] in text:
                log.info("routing decision (fallback parse): existing conv %s", c["id"])
                return {"id": c["id"]}

        log.warning("could not parse routing result %r — creating new", text[:200])
        return {"id": "new", "title": message.strip().split("\n")[0][:60], "tags": []}

    # ------------------------------------------------------------------
    # Route — main entry point
    # ------------------------------------------------------------------

    async def route(
        self,
        message: str,
        current_conv_id: str = "",
        agent: str = "default",
    ) -> dict:
        """Route a user message to the best conversation (or create one).

        Returns ``{"conversation": "<id>", "is_new": bool}``.
        The caller is responsible for appending the user message and
        launching the scheduler driver for the actual LLM response.
        """
        self._active_conversation = current_conv_id

        log.info(
            "route() called  message=%r  current_conv=%s  agent=%s",
            message[:80], current_conv_id or "(none)", agent,
        )

        decision = await self._route_message(message, current_conv_id)

        if decision["id"] == "new":
            meta = self.chat.create(
                title=decision.get("title", ""),
                agent=agent,
            )
            conv_id = meta.id
            tags = decision.get("tags", [])
            if tags:
                self.chat.update_meta(conv_id, tags=tags)
            is_new = True
            self.notify(f"Created new conversation: {meta.title}")
            log.info("created new conversation %s  title=%r  tags=%s", conv_id, meta.title, tags)
        else:
            conv_id = decision["id"]
            is_new = False
            self.notify("Routed to existing conversation")
            log.info("routing to existing conversation %s", conv_id)

        return {"conversation": conv_id, "is_new": is_new}
