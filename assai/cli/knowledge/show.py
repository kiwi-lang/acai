"""Show full content of a knowledge document.

Usage::

    assai knowledge show --path python/asyncio/generators
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from argklass.command import Command

from assai.cli.knowledge import ArgsWithPath, get_store, fmt_time


@dataclass
class ShowArgs(ArgsWithPath):
    pass


class Show(Command):
    """Show full content of a knowledge document."""

    name = "show"
    Arguments = ShowArgs

    @staticmethod
    def execute(args) -> int:
        if not args.path:
            print("error: --path is required (subject/subsubject/title)", file=sys.stderr)
            return 1

        store = get_store(args)
        doc = store.get_by_path(args.path)

        if doc is None:
            print(f"Document not found: {args.path}")
            return 1

        print(f"Path:     {doc.path}")
        print(f"Updated:  {fmt_time(doc.updated_at)}")
        print("─" * 60)
        print(doc.content)
        return 0


COMMANDS = Show
