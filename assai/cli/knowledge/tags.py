"""Show the knowledge tree (subjects → subsubjects → documents).

Usage::

    assai knowledge tree
"""

from __future__ import annotations

from dataclasses import dataclass

from argklass.command import Command

from assai.cli import CommonArguments
from assai.cli.knowledge import get_store


@dataclass
class TreeArgs(CommonArguments):
    pass


class Tree(Command):
    """Show the knowledge tree."""

    name = "tree"
    Arguments = TreeArgs

    @staticmethod
    def execute(args) -> int:
        store = get_store(args)
        t = store.tree()

        if not t:
            print("Knowledge store is empty.")
            return 0

        total = 0
        for subject, subs in sorted(t.items()):
            print(f"{subject}/")
            for subsub, titles in sorted(subs.items()):
                print(f"  {subsub}/")
                for title in titles:
                    print(f"    {title}.md")
                    total += 1

        print(f"\n{len(t)} subject(s), {total} document(s)")
        return 0


COMMANDS = Tree
