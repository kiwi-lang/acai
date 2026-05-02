"""UberGraph — conversation routing via a lightweight LLM call.

The routing phase dispatches a small LLM call to classify the user
message into an existing or new conversation.  A ``route`` event is
yielded with the decision, then ``done``.  The frontend is responsible
for confirming the routing and starting a regular ``/converse`` call.
"""

from __future__ import annotations

import json
import logging
import re
import traceback as _tb
from typing import AsyncIterator

from acai.tasks.graph import Acc, TaskGraph

log = logging.getLogger(__name__)


def _fallback_title(message: str) -> str:
    return message.strip().split("\n")[0][:60]


class UberGraph(TaskGraph):
    """Route a user message to the right conversation.

    ``run()`` dispatches a lightweight LLM call to pick (or create) the
    target conversation, yields a ``route`` event with the decision,
    then yields ``done``.  The actual conversation is started separately
    by the frontend via ``POST /converse``.
    """

    # ------------------------------------------------------------------
    # Routing helpers
    # ------------------------------------------------------------------

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
        meta = self.chat.get_meta(decision["id"])
        title = meta.get("title", "") if meta else ""
        return {"conversation": decision["id"], "is_new": False, "title": title}

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
        return {"conversation": conv_id, "is_new": True, "title": meta.title}

    # ------------------------------------------------------------------
    # Route phase — lightweight LLM call to pick the conversation
    # ------------------------------------------------------------------

    async def _route(self, work: dict) -> dict:
        """Dispatch a routing LLM call and return the decision dict."""
        message = work.get("message", "")
        current_conv_id = work.get("current_conversation", "")
        agent = work.get("agent", "default")

        catalogue = self._build_catalogue()
        log.info(
            "routing message  catalogue_size=%d  current_conv=%s  message=%r",
            len(catalogue), current_conv_id or "(none)", message[:80],
        )

        active_conversation = None
        if current_conv_id:
            active_conversation = next(
                (c for c in catalogue if c["id"] == current_conv_id), None,
            )

        route_work = {
            "task_id": "uber-route",
            "kind": "route",
            "message": message,
            "provider_override": work.get("provider_override"),
        }

        payload = self.prepare(
            "router", route_work,
            extra_context={
                "catalogue": catalogue,
                "active_conversation": active_conversation,
                "user_message": message,
            },
        )

        result_text = ""
        acc = Acc(self.dispatch(payload))
        try:
            async for _event in acc:
                pass
            result_text = acc.text
        except Exception:
            log.exception("routing dispatch failed")

        if not result_text:
            log.warning("routing returned empty — falling back to new conversation")
            return self._create_new(message, agent)

        decision = self._parse_routing_result(result_text, catalogue, message)
        return self._apply_decision(decision, message, agent)

    # ------------------------------------------------------------------
    # run — route only
    # ------------------------------------------------------------------

    async def run(self, work: dict) -> AsyncIterator[dict]:
        """Route the user message and yield the decision.

        Yields a ``route`` event followed by ``done``.  The frontend
        confirms the routing and starts a ``/converse`` call separately.
        """
        agent_name = work.get("agent", "default")

        try:
            routing = await self._route(work)
        except Exception as exc:
            log.exception("uber routing error")
            yield self._error_event(
                f"Routing failed: {exc}",
                _tb.format_exc(),
            )
            return

        yield {
            "event_type": "route",
            "data": {
                "conversation": routing["conversation"],
                "is_new": routing.get("is_new", False),
                "title": routing.get("title", ""),
            },
        }

        yield self._done_event()


# Backward-compatible alias
UberRouter = UberGraph
