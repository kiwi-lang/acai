"""Scribe agent — distils conversation events into living spec documents.

.. deprecated::
    This module has been retired. Spec management will be handled via
    the task queue and configurable agents.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from assai.retired import Agent, load_prompt
from assai.events import EventKind

if TYPE_CHECKING:
    from assai.core.llm import LLM
    from assai.events import EventBus
    from assai.tracker.git import GitTracker


REFINE_PROMPT = load_prompt("scribe_refine.md")

CONVERSATION_EVENTS = (
    EventKind.NEW_REQUIREMENT,
    EventKind.CLARIFICATION,
    EventKind.DECISION,
    EventKind.CONTRADICTION,
)


class ScribeAgent(Agent):
    """Maintains the spec by reacting to conversation events."""

    def __init__(self, name, config, events: EventBus | None = None,
                 llm: LLM | None = None, specs_dir: str | None = None,
                 git: GitTracker | None = None):
        self.specs_dir = specs_dir or config.scribe.specs_dir
        self.git = git
        super().__init__(name, config, events, llm)

    def setup(self):
        if self.events is None:
            return
        for kind in CONVERSATION_EVENTS:
            self.events.subscribe(kind, self.handle)

    def handle(self, event):
        spec = self._read_spec()
        updated = self._refine(
            spec=spec,
            kind=event.kind.value,
            summary=event.data.get("summary", ""),
            human=event.data.get("human", ""),
            assistant=event.data.get("assistant", ""),
        )
        self._write_spec(updated)
        self.emit(EventKind.SPEC_UPDATED, {"path": self._spec_path()})

    def _spec_path(self) -> str:
        return os.path.join(self.specs_dir, "spec.md")

    def _read_spec(self) -> str:
        path = self._spec_path()
        if os.path.isfile(path):
            with open(path) as f:
                return f.read()
        return ""

    def _write_spec(self, content: str):
        os.makedirs(self.specs_dir, exist_ok=True)
        path = self._spec_path()
        with open(path, "w") as f:
            f.write(content)
        if self.git and self.config.git.auto_commit:
            self.git.commit(message="scribe: update spec", files=[path])

    def _refine(self, spec, kind, summary, human, assistant) -> str:
        prompt = REFINE_PROMPT.format(
            spec=spec or "(empty — create a new spec)",
            kind=kind,
            summary=summary,
            human=human,
            assistant=assistant,
        )
        return self.llm.complete([{"role": "user", "content": prompt}])
