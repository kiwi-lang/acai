"""ConverseGraph — single-agent conversation with tool follow-ups."""

from __future__ import annotations

import logging
import os
import traceback as _tb
from typing import AsyncIterator

from acai.tasks.graph import Acc, TaskGraph

log = logging.getLogger(__name__)


def _auto_knowledge_context(workspace: str, messages: list[dict], limit: int = 5) -> str:
    """Run hybrid (vector + FTS) search on the last user message and return context.

    Tries vector search first for semantic matching.  Falls back to FTS
    if embeddings are not available.  When both are available, uses
    reciprocal rank fusion to combine results.
    """
    last_user = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                last_user = content
            break

    if not last_user or len(last_user) < 5:
        return ""

    knowledge_dir = os.path.join(workspace, "knowledge")
    db_path = os.path.join(knowledge_dir, ".knowledge.db")
    if not os.path.isfile(db_path):
        return ""

    try:
        from acai.knowledge.db import KnowledgeDB
        from acai.knowledge.store import KnowledgeStore
        from acai.knowledge.vectors import VectorStore

        db = KnowledgeDB(db_path)
        store = KnowledgeStore(knowledge_dir)

        # Try hybrid search (vector + FTS)
        vec_store = VectorStore(knowledge_dir)
        if vec_store.embedding_available:
            fts_hits = db.fts_search(last_user, limit=limit)
            hybrid_results = vec_store.hybrid_search(last_user, fts_hits, limit=limit)
            if hybrid_results:
                parts: list[str] = []
                for hit in hybrid_results:
                    doc = store.get_by_path(hit.path)
                    if doc and doc.content:
                        parts.append(f"### {doc.subject}/{doc.subsubject}/{doc.title}\n\n{doc.content}")
                return "\n\n---\n\n".join(parts) if parts else ""

        # Fallback: FTS only
        hits = db.fts_search(last_user, limit=limit)
        if not hits:
            return ""

        parts = []
        for hit in hits:
            doc = store.get_by_path(hit["path"])
            if doc and doc.content:
                parts.append(f"### {doc.subject}/{doc.subsubject}/{doc.title}\n\n{doc.content}")

        return "\n\n---\n\n".join(parts) if parts else ""
    except Exception:
        log.debug("auto-knowledge lookup failed", exc_info=True)
        return ""


class ConverseGraph(TaskGraph):
    """Single agent → dispatch → tool-call follow-up loop → persist.

    This is the most common graph and matches the behaviour of the
    old ``ConversationScheduler``.
    """

    async def run(self, work: dict) -> AsyncIterator[dict]:
        # Proactively compress the conversation if approaching context limit
        compress_ev = await self._try_compress_conversation(work)
        if compress_ev:
            yield compress_ev

        try:
            agent_name = work.get("agent", "default")

            extra_context = None
            messages = self.chat.read(self.conversation) if self.conversation else []
            if messages:
                kc = _auto_knowledge_context(self.config.workspace, messages)
                if kc:
                    extra_context = {"knowledge_context": kc}

            payload = self.prepare(agent_name, work, extra_context=extra_context)
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
        git = await self._finalize_git(work)
        yield self._done_event(git)
