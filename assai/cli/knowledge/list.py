"""List knowledge documents.

Usage::

    assai knowledge list
    assai knowledge list --subject python
    assai knowledge list --subject python --subsubject asyncio
    assai knowledge list --full
"""

from __future__ import annotations

from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from assai.cli import CommonArguments
from assai.cli.knowledge import get_store, fmt_time


@dataclass
class ListArgs(CommonArguments):
    subject: str = argument(default="", help="filter by subject")
    subsubject: str = argument(default="", help="filter by subsubject")
    full: bool = argument(default=False, action="store_true", help="show full content")


class List(Command):
    """List knowledge documents."""

    name = "list"
    Arguments = ListArgs

    @staticmethod
    def execute(args) -> int:
        store = get_store(args)
        docs = store.list(subject=args.subject, subsubject=args.subsubject)

        if not docs:
            print("No documents found.")
            return 0

        print(f"{'Path':<50s} {'Updated'}")
        print("─" * 70)

        for doc in docs:
            print(f"{doc.path:<50s} {fmt_time(doc.updated_at)}")
            if args.full:
                for line in doc.content.split("\n"):
                    print(f"  │ {line}")
                print()

        print(f"\n{len(docs)} document(s)")
        return 0


COMMANDS = List
