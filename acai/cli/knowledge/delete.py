"""Delete a knowledge document.

Usage::

    acai knowledge delete --path python/asyncio/generators
    acai knowledge delete --path python/asyncio/generators --force
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from acai.cli.knowledge import ArgsWithPath, get_store


@dataclass
class DeleteArgs(ArgsWithPath):
    force: bool = argument(default=False, action="store_true", help="skip confirmation")


class Delete(Command):
    """Delete a knowledge document."""

    name = "delete"
    Arguments = DeleteArgs

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

        if not args.force:
            print(f"Delete \"{doc.path}\"?")
            answer = input("Type 'yes' to confirm: ").strip().lower()
            if answer != "yes":
                print("Cancelled.")
                return 0

        store.delete_by_path(args.path)
        print(f"Deleted: {args.path}")
        return 0


COMMANDS = Delete
