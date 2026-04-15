"""UberRouter — conversation-aware message routing via direct worker dispatch.

Replaces the queue-based ``UberScheduler`` with a direct HTTP call to
the worker, making routing faster and simpler.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assai.core.agent_store import AgentStore
    from assai.core.chat import ChatStore
    from assai.core.config import AssaiConfig
    from assai.core.load_balancer import WorkerInfo

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


def _fallback_title(message: str) -> str:
    return message.strip().split("\n")[0][:60]


class UberRouter:
    """Routes user messages to the right conversation via a direct worker call.

    Unlike the old ``UberScheduler`` this does *not* use the work queue;
    it dispatches a lightweight LLM request directly to an acquired worker.
    """

    def __init__(
        self,
        chat: ChatStore,
        agent_store: AgentStore,
        config: AssaiConfig,
    ):
        self.chat = chat
        self.agent_store = agent_store
        self.config = config

    def _build_catalogue(self) -> list[dict]:
        return [
            {
                "id": c["id"],
                "title": c.get("title", ""),
                "description": c.get("description", ""),
                "tags": c.get("tags", []),
            }
            for c in self.chat.list()
        ]

    async def route(
        self,
        worker: WorkerInfo,
        message: str,
        current_conv_id: str = "",
        agent: str = "default",
    ) -> dict:
        """Route a user message to the best conversation (or create one).

        Returns ``{"conversation": "<id>", "is_new": bool}``.
        """
        from assai.core.iterator import AsyncSSEIterator

        catalogue = self._build_catalogue()
        log.info(
            "routing message  catalogue_size=%d  current_conv=%s  message=%r",
            len(catalogue), current_conv_id or "(none)", message[:80],
        )

        if catalogue:
            lines = []
            for c in catalogue:
                parts = [f'id={c["id"]}', f'title="{c["title"]}"']
                if c["description"]:
                    parts.append(f'description="{c["description"]}"')
                if c["tags"]:
                    parts.append(f'tags=[{", ".join(c["tags"])}]')
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

        messages = [
            {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        payload = {
            "task_id": "uber-route",
            "kind": "llm_complete",
            "messages": messages,
        }

        result_text = ""
        url = f"{worker.url}/llm/complete"
        try:
            async for event in AsyncSSEIterator(url, json=payload):
                if event.event == "done":
                    break
                if event.event == "token":
                    try:
                        data = event.json()
                        result_text += data.get("token", "")
                    except (json.JSONDecodeError, ValueError):
                        pass
                if event.event == "error":
                    log.error("routing LLM error: %s", event.data)
                    break
        except Exception:
            log.exception("routing dispatch failed")

        if not result_text:
            log.warning("routing returned empty — falling back to new conversation")
            return self._create_new(message, agent)

        decision = self._parse_routing_result(result_text, catalogue, message)
        return self._apply_decision(decision, message, agent)

    def _parse_routing_result(self, raw: str, catalogue: list[dict], message: str) -> dict:
        text = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        text = re.sub(r"\s*```$", "", text).strip()

        new_fallback = {"id": "new", "title": _fallback_title(message), "tags": []}

        try:
            decision = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            decision = None

        if isinstance(decision, dict) and "id" in decision:
            conv_id = decision["id"]

            if conv_id == "new":
                tags = decision.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                title = decision.get("title") or _fallback_title(message)
                log.info("routing decision: NEW  title=%r  tags=%s", title, tags)
                return {"id": "new", "title": title, "tags": tags}

            if conv_id in {c["id"] for c in catalogue}:
                log.info("routing decision: existing conv %s", conv_id)
                return {"id": conv_id}

            log.warning("LLM returned unknown conv id %r — creating new", conv_id)
            return new_fallback

        for c in catalogue:
            if c["id"] in text:
                log.info("routing decision (fallback parse): existing conv %s", c["id"])
                return {"id": c["id"]}

        log.warning("could not parse routing result %r — creating new", text[:200])
        return new_fallback

    def _apply_decision(self, decision: dict, message: str, agent: str) -> dict:
        if decision["id"] == "new":
            return self._create_new(
                message, agent,
                title=decision.get("title", ""),
                tags=decision.get("tags", []),
            )
        return {"conversation": decision["id"], "is_new": False}

    def _create_new(
        self,
        message: str,
        agent: str,
        title: str = "",
        tags: list[str] | None = None,
    ) -> dict:
        meta = self.chat.create(
            title=title or _fallback_title(message),
            agent=agent,
        )
        conv_id = meta.id
        if tags:
            self.chat.update_meta(conv_id, tags=tags)
        log.info("created new conversation %s  title=%r", conv_id, meta.title)
        return {"conversation": conv_id, "is_new": True}
