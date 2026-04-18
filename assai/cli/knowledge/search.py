"""Search knowledge documents by text.

Usage::

    assai knowledge search --query <text>
    assai knowledge search --query <text> --subject python
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from assai.cli.knowledge import ArgsWithQuery, get_store, truncate


@dataclass
class SearchArgs(ArgsWithQuery):
    subject: str = argument(default="", help="restrict to subject")
    subsubject: str = argument(default="", help="restrict to subsubject")


class Search(Command):
    """Search knowledge documents by text."""

    name = "search"
    Arguments = SearchArgs

    @staticmethod
    def execute(args) -> int:
        if not args.query:
            print("error: --query is required", file=sys.stderr)
            return 1

        store = get_store(args)
        docs = store.search(
            args.query,
            subject=args.subject,
            subsubject=args.subsubject,
        )

        if not docs:
            print(f"No documents matching \"{args.query}\".")
            return 0

        print(f"{'Path':<50s} {'Preview'}")
        print("─" * 80)
        for doc in docs:
            preview = truncate(doc.content, 60)
            print(f"{doc.path:<50s} {preview}")

        print(f"\n{len(docs)} result(s)")
        return 0


COMMANDS = Search
