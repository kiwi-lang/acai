"""Inspect and manage the knowledge store.

Usage::

    assai knowledge list
    assai knowledge list --subject python
    assai knowledge show --path python/asyncio/generators
    assai knowledge search --query <text>
    assai knowledge delete --path python/asyncio/generators
    assai knowledge tree
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from argklass import argument
from argklass.command import ParentCommand

from assai.cli import CommonArguments


class Knowledge(ParentCommand):
    """Inspect and manage the knowledge store."""

    name: str = "knowledge"

    @staticmethod
    def module():
        import assai.cli.knowledge
        return assai.cli.knowledge


def get_store(args):
    """Build a KnowledgeStore from CLI args."""
    from assai.cli import setup

    config, _ = setup(args)
    from assai.orchestrator.knowledge import KnowledgeStore

    return KnowledgeStore(os.path.join(config.workspace, "knowledge"))


def fmt_time(ts: float) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def truncate(text: str, width: int = 60) -> str:
    line = text.replace("\n", " ").strip()
    return (line[:width] + "…") if len(line) > width else line


@dataclass
class ArgsWithPath(CommonArguments):
    """Base for commands that need a document path."""
    path: str = argument(default=None, help="document path (subject/subsubject/title)")


@dataclass
class ArgsWithQuery(CommonArguments):
    """Base for commands that need a search query."""
    query: str = argument(default=None, help="search string")


COMMANDS = Knowledge
