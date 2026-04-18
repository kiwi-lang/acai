"""ConverseScribeGraph — curator → converse → scribe as a DynamicGraph.

Loads its workflow spec from ``agents/dynamic/converse-scribe.json``
(the same directory as all other dynamic graph specs).  The execution
flow is:

1. **Curator** — ``background_agent`` (silent) searches the knowledge
   base and selects relevant documents.
2. **Parse** — ``parse_knowledge`` extracts the curator's JSON output
   into a ``knowledge_context`` string.
3. **Converse** — ``agent_call`` prepares the main agent with the
   curated knowledge injected as extra template context.
4. **Tools** — ``tool_loop`` handles tool-call follow-ups from the
   main agent, streaming tokens to the user.
5. **Scribe** — ``background_agent`` (silent) reviews the exchange
   and updates the knowledge base.

The graph finishes with ``DynamicGraph``'s built-in response
persistence and ``done`` event.
"""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

from assai.tasks.dynamic import DynamicGraph

_SPEC_PATH = os.path.join(
    os.path.dirname(__file__), os.pardir,
    "agents", "dynamic", "converse-scribe.json",
)
_SPEC_PATH = os.path.normpath(_SPEC_PATH)


def _load_spec() -> dict:
    with open(_SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


class ConverseScribeGraph(DynamicGraph):
    """Curator → converse → scribe, loaded from agents/dynamic/converse-scribe.json."""

    async def run(self, work: dict) -> AsyncIterator[dict]:
        if not work.get("workflow_spec"):
            work = dict(work, workflow_spec=_load_spec())
        async for event in super().run(work):
            yield event
