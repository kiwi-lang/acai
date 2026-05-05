"""ConverseScribeGraph — curator → converse → scribe pipeline.

Flow:
  1. Curator agent — searches knowledge base, returns list of paths.
  2. Load knowledge — read the files and build knowledge_context.
  3. Converse agent — main reply with knowledge injected, plus tool loop.
  4. Scribe agent — reviews exchange, updates knowledge base silently.
"""

from __future__ import annotations

import json
import logging
import os
import traceback as _tb
from typing import AsyncIterator

from acai.tasks.graph import Acc, TaskGraph

log = logging.getLogger(__name__)

_WORKFLOW_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "workflows", "converse-scribe")
_WORKFLOW_DIR = os.path.normpath(_WORKFLOW_DIR)

_CURATOR_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "curator_paths",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["paths"],
            "additionalProperties": False,
        },
    },
}


def _parse_curator_paths(text: str) -> list[str]:
    """Extract a list of knowledge paths from curator JSON output."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        log.warning("Curator output is not valid JSON: %.200s", text)
        return []

    if isinstance(parsed, dict):
        paths = parsed.get("paths", [])
    elif isinstance(parsed, list):
        paths = parsed
    else:
        return []

    return [str(p) for p in paths if isinstance(p, str) and p.strip()]


def _load_knowledge_context(workspace: str, paths: list[str]) -> str:
    """Load knowledge documents by path and format as context string."""
    from acai.orchestrator.knowledge import KnowledgeStore

    knowledge_dir = os.path.join(workspace, "knowledge")
    store = KnowledgeStore(knowledge_dir)

    parts: list[str] = []
    for doc_path in paths[:10]:
        doc = store.get_by_path(doc_path)
        if doc and doc.content:
            parts.append(f"### {doc.subject}/{doc.subsubject}/{doc.title}\n\n{doc.content}")
        else:
            log.debug("load_knowledge: %r not found or empty", doc_path)

    return "\n\n---\n\n".join(parts) if parts else ""


class ConverseScribeGraph(TaskGraph):
    """Curator → Converse → Scribe, all in plain Python."""

    @classmethod
    def from_work(cls, worker, work, **kwargs):
        work = dict(work)
        work.setdefault("workflow_dir", _WORKFLOW_DIR)
        return super().from_work(worker, work, **kwargs)

    async def _background_agent(
        self,
        phase: str,
        payload: dict,
    ) -> AsyncIterator[dict]:
        """Run a background agent (curator/scribe) with phase-scoped events.

        Yields phase-prefixed SSE events so the frontend can group
        tool calls inside the agent's own bubble.  After iteration,
        the final ``Acc`` is available as ``self._last_acc``.
        """
        yield {"event_type": f"{phase}_start", "data": {"agent": phase}}

        acc = Acc(self.dispatch(payload))
        async for event in acc:
            if event.get("event_type") in ("token", "reasoning"):
                yield {**event, "event_type": f"{phase}_token"}
            else:
                yield event

        while acc.tool_calls:
            followup = list(payload["messages"])
            followup.append({
                "role": "assistant",
                "content": acc.text or None,
                "tool_calls": acc.tool_calls,
            })
            for call in acc.tool_calls:
                fn = call.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    tool_args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                yield {"event_type": f"{phase}_tool_start", "data": {
                    "tool_name": tool_name, "args": tool_args,
                }}
                try:
                    result_text = await self.dispatch_tool(tool_name, tool_args)
                except Exception as exc:
                    log.exception("%s tool error: %s", phase, tool_name)
                    result_text = f"[Tool error] {type(exc).__name__}: {exc}"

                followup.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result_text,
                })
                yield {"event_type": f"{phase}_tool_end", "data": {
                    "tool_name": tool_name,
                    "result_preview": result_text[:2000],
                }}

            payload = dict(payload, messages=followup)
            acc = Acc(self.dispatch(payload))
            async for event in acc:
                pass  # silent on follow-up rounds

        self._last_acc = acc
        yield {"event_type": f"{phase}_end", "data": {
            "status": "done", "text_length": len(acc.text),
        }}

    async def run(self, work: dict) -> AsyncIterator[dict]:  # noqa: C901
        try:
            # ==============================================================
            # Phase 1: Curator — find relevant knowledge paths
            # ==============================================================
            curator_payload = self.prepare("curator", work)
            curator_payload["response_format"] = _CURATOR_RESPONSE_FORMAT

            async for event in self._background_agent("curator", curator_payload):
                yield event

            paths = _parse_curator_paths(self._last_acc.text)
            log.info("curator done, paths=%s", paths)

            knowledge_context = _load_knowledge_context(
                self.config.workspace, paths,
            )
            log.info("knowledge loaded, %d chars from %d paths",
                     len(knowledge_context), len(paths))

            # ==============================================================
            # Phase 2: Converse — main agent reply with knowledge
            # ==============================================================
            extra_context = {}
            if knowledge_context:
                extra_context["knowledge_context"] = knowledge_context

            converse_payload = self.prepare(
                work.get("agent", "default"), work,
                extra_context=extra_context or None,
            )

            async for event in self._run_with_tools(converse_payload):
                yield event
                if event.get("event_type") == "error":
                    return

            acc_converse = self._last_acc
            self._save_response(acc_converse)

            # ==============================================================
            # Phase 3: Scribe — update knowledge base silently
            # ==============================================================
            scribe_payload = self.prepare("scribe", work)

            async for event in self._background_agent("scribe", scribe_payload):
                yield event

            log.info("scribe done")

        except Exception as exc:
            log.exception("ConverseScribeGraph error")
            yield self._error_event(
                f"{type(exc).__name__}: {exc}",
                _tb.format_exc(),
            )
            return

        git = await self._finalize_git(work)
        yield self._done_event(git)
