"""Uber-conversation scheduler — conversation-aware message routing.

The uber chat is a single input that automatically routes each message
to the most relevant conversation.  The scheduler:

1. Collects metadata (id, title, description, tags) for every conversation.
2. Queues a fast **routing** task that asks the LLM to pick the best
   conversation (or create a new one).
3. Appends the user message to the chosen conversation and queues the
   main **LLM response** task through the normal pipeline.

The scheduler never creates an LLM client — all inference goes through
the shared work queue.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from assai.core.chat import ChatStore
from assai.core.stream import StreamTracker
from assai.queue.work import TaskStatus, WorkQueue

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assai.core.config import AssaiConfig

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


class UberScheduler:
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
    ):
        self.config = config
        self.chat = chat
        self.queue = queue
        self.tracker = stream_tracker
        self.tasks_dir = config.worker.tasks_dir
        self._lock = threading.Lock()

        log.info("UberScheduler initialised  tasks_dir=%s", self.tasks_dir)

    # ------------------------------------------------------------------
    # Task polling
    # ------------------------------------------------------------------

    def _wait_for_task(
        self, task_id: str, timeout: float = 60.0, interval: float = 0.3,
    ) -> str | None:
        """Block until *task_id* completes and return the result text."""
        deadline = time.monotonic() + timeout
        tasks_dir = self.config.worker.tasks_dir
        t0 = time.monotonic()
        polls = 0

        log.info("waiting for task %s (timeout=%.0fs)", task_id, timeout)

        while time.monotonic() < deadline:
            task = self.queue.get(task_id)
            polls += 1
            if task is None:
                log.warning("task %s vanished from queue after %d polls", task_id, polls)
                return None

            if task.status in (TaskStatus.COMPLETED, "chained"):
                elapsed = time.monotonic() - t0
                result_path = task.result_path or os.path.join(
                    tasks_dir, task_id, "result.json",
                )
                if os.path.isfile(result_path):
                    try:
                        with open(result_path, encoding="utf-8") as f:
                            raw = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        log.error("task %s completed but result unreadable at %s", task_id, result_path)
                        return None
                    text = raw if isinstance(raw, str) else (
                        raw.get("content", str(raw)) if isinstance(raw, dict) else str(raw)
                    )
                    log.info(
                        "task %s completed in %.1fs (%d polls)  result=%r",
                        task_id, elapsed, polls, text[:200],
                    )
                    return text
                log.warning("task %s completed but no result file at %s", task_id, result_path)
                return None

            if task.status == TaskStatus.FAILED:
                elapsed = time.monotonic() - t0
                log.warning("task %s FAILED after %.1fs: %s", task_id, elapsed, task.error_log)
                return None

            time.sleep(interval)

        elapsed = time.monotonic() - t0
        log.warning(
            "task %s timed out after %.0fs (%d polls, last status=%s)",
            task_id, elapsed, polls, task.status if task else "?",
        )
        return None

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

    def _route_message(self, message: str, current_conv_id: str = "") -> dict:
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

        routing_messages = [
            {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        task = self.queue.push(
            title="uber: route message",
            kind="llm_complete",
            spec_path="",
            agent="default",
        )

        task_dir = os.path.join(self.tasks_dir, task.id)
        os.makedirs(task_dir, exist_ok=True)
        spec_path = os.path.join(task_dir, "conversation.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(routing_messages, f, ensure_ascii=False)
        self.queue.update(task.id, spec_path=spec_path)
        log.debug("wrote routing spec to %s", spec_path)
        self.queue.update(task.id, status=TaskStatus.READY)
        log.info("routing task queued  task_id=%s", task.id)

        result = self._wait_for_task(task.id)
        if not result:
            log.warning("routing returned empty — falling back to new conversation")
            return {"id": "new", "title": message.strip().split("\n")[0][:60], "tags": []}

        return self._parse_routing_result(result, catalogue, message)

    def _parse_routing_result(self, raw: str, catalogue: list[dict], message: str) -> dict:
        """Extract the JSON decision from the LLM output."""
        text = raw.strip()

        # Strip markdown fences
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

        # Fallback: check if the raw text contains a known ID
        for c in catalogue:
            if c["id"] in text:
                log.info("routing decision (fallback parse): existing conv %s", c["id"])
                return {"id": c["id"]}

        log.warning("could not parse routing result %r — creating new", text[:200])
        return {"id": "new", "title": message.strip().split("\n")[0][:60], "tags": []}

    # ------------------------------------------------------------------
    # Schedule — main entry point
    # ------------------------------------------------------------------

    def schedule(
        self,
        message: str,
        current_conv_id: str = "",
        provider: str = "auto",
        agent: str = "default",
        route_only: bool = False,
    ) -> dict:
        """Route a user message to the best conversation and optionally queue the response.

        When *route_only* is ``True`` the scheduler only picks (or creates)
        the target conversation — it does **not** append the user message or
        queue an LLM task.  The caller is expected to send the message
        through the normal ``converse`` flow afterwards.

        Returns ``conversation``, ``is_new``, and (unless *route_only*)
        ``task_id``.
        """
        log.info(
            "schedule() called  message=%r  current_conv=%s  agent=%s  route_only=%s",
            message[:80], current_conv_id or "(none)", agent, route_only,
        )

        decision = self._route_message(message, current_conv_id)

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
            log.info("created new conversation %s  title=%r  tags=%s", conv_id, meta.title, tags)
        else:
            conv_id = decision["id"]
            is_new = False
            log.info("routing to existing conversation %s", conv_id)

        if route_only:
            log.info("route_only — skipping message append and LLM task")
            return {"conversation": conv_id, "is_new": is_new}

        self.chat.append(conv_id, {"role": "user", "content": message})

        conv_path = self.chat._msg_path(conv_id)
        task = self.queue.push(
            title=f"converse: {message[:60]}",
            kind="llm_complete",
            spec_path=conv_path,
            agent=agent,
        )
        self.queue.update(task.id, status=TaskStatus.READY)
        self.tracker.register(task.id, conv_id)

        log.info(
            "main task queued  task_id=%s  conversation=%s  is_new=%s",
            task.id, conv_id, is_new,
        )

        return {
            "task_id": task.id,
            "conversation": conv_id,
            "is_new": is_new,
        }
